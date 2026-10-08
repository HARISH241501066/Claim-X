"""The masked LLM path: what is sent, what is accepted, what is logged, and every fallback."""

import json
import re
import sqlite3
import threading
import time
from types import SimpleNamespace

import httpx2
import openai
import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api.main import create_app
from backend.audit import AuditLog
from backend.brief import generate, llm_payload, masker, template
from backend.brief.evidence import build_pack
from backend.brief.llm_payload import IDENTIFIER, PLACEHOLDER
from backend.brief.masker import LeakError

RING = "CASE-0001"
CLAUDE = {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "test-key"}
GROK = {"LLM_PROVIDER": "xai", "LLM_API_KEY": "test-key"}
GROQ = {"LLM_PROVIDER": "groq", "LLM_API_KEY": "test-key"}


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("m7b") / "claimshield.db")


@pytest.fixture(scope="module")
def pack(shared):
    return build_pack(RING, db_path=shared.db_path)


@pytest.fixture
def audit(tmp_path):
    return AuditLog(tmp_path / "audit.db")


class ClaudeLike:
    """Anthropic-style client: records every call and replies with the given text."""

    def __init__(self, reply="", exc=None, stop_reason="end_turn", usage=(120, 80)):
        self.reply, self.exc, self.stop_reason, self.usage, self.calls = reply, exc, stop_reason, usage, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        block = SimpleNamespace(type="text", text=self.reply)
        usage = SimpleNamespace(input_tokens=self.usage[0], output_tokens=self.usage[1])
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason, usage=usage)

    @property
    def sent(self):
        """Everything that went out, as one string."""
        return "\n".join(
            [c["system"] + "\n" + "\n".join(m["content"] for m in c["messages"]) for c in self.calls]
        )


class ChatLike:
    """OpenAI-style client (Grok and Groq): chat.completions.create."""

    def __init__(self, reply="", exc=None, finish="stop", usage=(150, 90)):
        self.reply, self.exc, self.finish, self.usage, self.calls = reply, exc, finish, usage, []
        self.chat = SimpleNamespace(completions=self)

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        choice = SimpleNamespace(finish_reason=self.finish, message=SimpleNamespace(content=self.reply))
        usage = SimpleNamespace(prompt_tokens=self.usage[0], completion_tokens=self.usage[1])
        return SimpleNamespace(choices=[choice], usage=usage)

    @property
    def sent(self):
        return "\n".join(m["content"] for c in self.calls for m in c["messages"])


def masked_reply(pack, edit=lambda t: t):
    """A reply the way an LLM would write it: the template text, with identifiers as placeholders."""
    _, vault = llm_payload.prepare(pack)
    return masker._mask_text(edit(template.render_template(pack)), vault)


def recorder(audit):
    return lambda row: audit.record_llm_request(**row)


def ask(shared, client=None, env=CLAUDE, audit=None, case=RING):
    return generate.generate_brief(
        case, db_path=shared.db_path, client=client, env=env,
        record=recorder(audit) if audit else None,
    )


# ------------------------------------------------------------ (a) what is sent


@pytest.mark.parametrize("make", [ClaudeLike, ChatLike], ids=["claude-style", "grok-groq-style"])
def test_the_payload_sent_contains_no_real_names_or_identifiers(shared, pack, make):
    client = make(reply=masked_reply(pack))
    env = CLAUDE if make is ClaudeLike else GROQ
    brief = ask(shared, client, env)
    assert brief.source == "llm" and len(client.calls) == 1
    sent = client.sent
    assert not IDENTIFIER.findall(sent), IDENTIFIER.findall(sent)  # no PRV-, MEM-, OWN- or FAC- ids
    for real in pack.entity_ids:
        if real.startswith(("PRV-", "OWN-", "FAC-", "MEM-")):
            assert real not in sent
    assert "PERSON_1" in sent and "ORG_1" in sent
    assert "Confidence level: Medium" in sent  # the instructions still travel
    assert '"evidence_key": "E1"' in sent


def test_every_case_can_be_masked_with_nothing_left_and_nothing_dropped(shared):
    for case in shared.cases:
        case_pack = build_pack(case.case_id, db_path=shared.db_path)
        payload = llm_payload.to_masker_pack(case_pack)
        people, orgs = llm_payload.find_identifiers(payload)
        _, vault = masker.mask_pack(payload, people, orgs)
        assert vault.dropped_keys == set(), (case.case_id, vault.dropped_keys)  # nothing silently lost
        masked, _ = llm_payload.prepare(case_pack)  # runs both leak checks
        assert not IDENTIFIER.findall(json.dumps(masked)), case.case_id


def test_the_masked_payload_keeps_what_the_brief_needs(pack):
    masked, _ = llm_payload.prepare(pack)
    assert [f["evidence_key"] for f in masked["findings"]] == ["E1", "E2"]
    assert masked["confidence"].startswith("Confidence level: Medium")
    assert masked["limitations"] == pack.limitations
    assert masked["timeline"] and all(t["evidence_key"] for t in masked["timeline"])
    assert {"nodes", "edges"} == set(masked["network"])
    assert masked["prediction"]["horizon_days"] == 30
    assert masked["recommended_action"] == pack.recommended_action["text"]
    shared_edges = [e for e in masked["network"]["edges"] if "shared_members" in e]
    assert shared_edges and shared_edges[0]["shared_members"] == 15


# ------------------------------------------------------------ (b) a bad reply falls back


def test_a_reply_citing_an_unknown_evidence_key_falls_back_to_the_template(shared, pack, audit):
    reply = masked_reply(pack, lambda t: t.replace("[E1]", "[E99]", 1))
    brief = ask(shared, ClaudeLike(reply=reply), audit=audit)
    assert brief.source == "template" and brief.text == template.render_template(pack)
    assert "unknown citation [E99]" in brief.fallback_reason
    row = audit.list_llm_requests()[0]
    assert row["outcome"] == "validation_failed" and "unknown citation [E99]" in row["detail"]


@pytest.mark.parametrize(
    "edit",
    [
        lambda t: t.replace("## Timeline\n", ""),
        lambda t: t.replace("not a finding of wrongdoing", "the provider is guilty"),
        lambda t: t.replace("Confidence level: Medium", "Confidence level: High"),
        lambda t: t.replace(template.FINAL_LINE, ""),
    ],
    ids=["missing section", "banned word", "changed confidence", "no closing line"],
)
def test_other_invalid_replies_also_fall_back(shared, pack, audit, edit):
    brief = ask(shared, ClaudeLike(reply=masked_reply(pack, edit)), audit=audit)
    assert brief.source == "template"
    assert audit.list_llm_requests()[0]["outcome"] == "validation_failed"


def test_a_placeholder_the_vault_never_issued_is_rejected(shared, pack, audit):
    reply = masked_reply(pack) + "\nSee also PERSON_99 and ORG_42.\n"
    reply = reply.replace(template.FINAL_LINE + "\nSee also", "See also")  # keep the closing line last
    reply = reply.rstrip() + "\n\n" + template.FINAL_LINE + "\n"
    reply = reply.replace("## Summary\n", "## Summary\nPERSON_99 is involved. ", 1)
    brief = ask(shared, ClaudeLike(reply=reply), audit=audit)
    assert brief.source == "template" and "unknown placeholder PERSON_99" in brief.fallback_reason
    assert audit.list_llm_requests()[0]["outcome"] == "validation_failed"


# ------------------------------------------------------------ (c) a leak means no call at all


def test_a_leak_error_means_the_llm_is_never_called(shared, pack, audit, monkeypatch):
    client = ClaudeLike(reply=masked_reply(pack))  # built before the leak check is sabotaged

    def leaky(masked, vault):
        raise LeakError("Sensitive value still present (PERSON_1)")

    monkeypatch.setattr(llm_payload, "assert_no_leak", leaky)
    brief = ask(shared, client, audit=audit)
    assert client.calls == []  # nothing was sent
    assert brief.source == "template" and brief.text == template.render_template(pack)
    assert brief.fallback_reason.startswith("blocked before sending")
    row = audit.list_llm_requests()[0]
    assert row["outcome"] == "leak_blocked" and row["input_tokens"] is None
    assert row["tokens_by_kind"] == {"PERSON": 3, "ORG": 2}  # which kinds were masked, no values


def test_masking_that_fails_to_hide_an_identifier_is_caught_by_the_second_check(shared, pack, audit, monkeypatch):
    monkeypatch.setattr(llm_payload, "mask_pack", lambda payload, people, orgs: (payload, masker.Vault()))
    client = ChatLike(reply="x")
    brief = ask(shared, client, GROK, audit=audit)
    assert client.calls == [] and brief.source == "template"
    assert audit.list_llm_requests()[0]["outcome"] == "leak_blocked"


def test_a_leak_is_logged_without_naming_the_value(shared, pack, audit, monkeypatch, caplog):
    monkeypatch.setattr(llm_payload, "mask_pack", lambda payload, people, orgs: (payload, masker.Vault()))
    with caplog.at_level("WARNING"):
        ask(shared, ClaudeLike(), audit=audit)
    assert "blocked" in caplog.text
    assert not IDENTIFIER.findall(caplog.text) and not IDENTIFIER.findall(audit.list_llm_requests()[0]["detail"])


def test_fullwidth_citation_brackets_are_accepted_as_plain_ones(shared, pack, audit):
    reply = masked_reply(pack).replace("[E1]", "【E1】").replace("[E2]", "【E2】")
    assert "[E1]" not in reply and "【E1】" in reply
    brief = ask(shared, ClaudeLike(reply=reply), audit=audit)
    assert brief.source == "llm" and "[E1]" in brief.text and "【" not in brief.text
    assert set(re.findall(r"\[(E\d+)\]", brief.text)) <= set(pack.keys)


def test_a_made_up_fullwidth_citation_is_still_rejected(shared, pack, audit):
    reply = masked_reply(pack).replace("[E1]", "【E99】", 1)
    brief = ask(shared, ClaudeLike(reply=reply), audit=audit)
    assert brief.source == "template" and "unknown citation [E99]" in brief.fallback_reason
    assert generate.normalize_citations("a 【E3】 and ［E4］ and [E5] and 【 E7 】 and 【E 6】 and 【X1】") == (
        "a [E3] and [E4] and [E5] and [E7] and 【E 6】 and 【X1】"
    )  # only well-formed evidence keys are touched


def test_typographic_hyphens_in_a_heading_do_not_cause_a_rejection(shared, pack, audit):
    reply = masked_reply(pack).replace("human-review", "human\u2011review")  # non-breaking hyphen
    assert "human\u2011review" in reply
    brief = ask(shared, ClaudeLike(reply=reply), audit=audit)
    assert brief.source == "llm" and "## Recommended human-review action" in brief.text
    assert generate.normalize_reply("a\u2010b\u2011c \u2013 d \u2014 e") == "a-b-c \u2013 d \u2014 e"  # dashes stay


def test_a_failed_attempt_is_marked_retryable_but_a_missing_provider_is_not(shared, pack):
    failed = ask(shared, ClaudeLike(exc=RuntimeError("boom")))
    assert failed.source == "template" and failed.retryable is True
    assert ask(shared, None, {}).retryable is False
    assert ask(shared, ClaudeLike(reply=masked_reply(pack))).retryable is False


# ------------------------------------------------------------ restoring names after a good reply


def test_a_valid_reply_is_unmasked_for_display_with_the_real_identifiers(shared, pack, audit):
    client = ChatLike(reply=masked_reply(pack))
    brief = ask(shared, client, GROQ, audit=audit)
    assert brief.source == "llm" and brief.provider == "groq" and brief.masked is True
    assert not PLACEHOLDER.findall(brief.text)  # no PERSON_1 or ORG_2 left on screen
    assert "PRV-A01" in brief.text and "FAC-B01" in brief.text and "OWN-001" in brief.text
    assert brief.text == template.render_template(pack)  # the restored text is the real brief
    assert set(re.findall(r"\[(E\d+)\]", brief.text)) <= set(pack.keys)


def test_the_unmasked_names_never_go_back_to_the_provider_or_the_log(shared, pack, audit):
    client = ChatLike(reply=masked_reply(pack))
    ask(shared, client, GROQ, audit=audit)
    dump = json.dumps(audit.list_llm_requests())
    assert not IDENTIFIER.findall(dump)
    assert len(client.calls) == 1  # one request, and it was the masked one


# ------------------------------------------------------------ the provider switch


@pytest.mark.parametrize("env", [{}, {"LLM_PROVIDER": "none", "LLM_API_KEY": "k"}, {"LLM_PROVIDER": "groq"}])
def test_no_provider_or_no_key_never_builds_a_client_or_logs_a_request(shared, audit, monkeypatch, env):
    def boom(*args, **kwargs):
        raise AssertionError("a client must not be created")

    monkeypatch.setattr(generate, "make_client", boom)
    brief = ask(shared, None, env, audit=audit)
    assert brief.source == "template" and brief.provider is None
    assert audit.list_llm_requests() == []  # no request was attempted


@pytest.mark.parametrize(
    ("env", "constructor", "expected"),
    [
        (GROK, "openai.OpenAI", {"base_url": "https://api.x.ai/v1", "timeout": 15.0, "max_retries": 0}),
        (GROQ, "openai.OpenAI", {"base_url": "https://api.groq.com/openai/v1", "timeout": 15.0, "max_retries": 0}),
        (CLAUDE, "anthropic.Anthropic", {"timeout": 15.0, "max_retries": 0}),
    ],
    ids=["xai", "groq", "anthropic"],
)
def test_each_provider_uses_its_own_client_endpoint_and_a_15_second_timeout(shared, pack, monkeypatch, env, constructor, expected):
    seen = {}
    module, name = constructor.split(".")
    fake = ChatLike(reply=masked_reply(pack)) if module == "openai" else ClaudeLike(reply=masked_reply(pack))

    def fake_constructor(**kwargs):
        seen.update(kwargs)
        return fake

    monkeypatch.setattr(getattr(generate, module), name, fake_constructor)
    brief = ask(shared, None, env)
    assert brief.source == "llm"
    assert seen["api_key"] == "test-key"
    for key, value in expected.items():
        assert seen[key] == value, key


@pytest.mark.parametrize(
    ("env", "default"),
    [(GROK, "grok-4"), (GROQ, "openai/gpt-oss-120b"), (CLAUDE, "claude-opus-5-5")],
    ids=["xai", "groq", "anthropic"],
)
def test_default_models_and_the_model_override(shared, pack, env, default):
    make = ClaudeLike if env is CLAUDE else ChatLike
    first = make(reply=masked_reply(pack))
    assert ask(shared, first, env).model == default and first.calls[0]["model"] == default
    second = make(reply=masked_reply(pack))
    assert ask(shared, second, {**env, "LLM_MODEL": "custom-model"}).model == "custom-model"
    assert second.calls[0]["model"] == "custom-model"


def test_a_chat_style_request_carries_the_system_prompt_and_masked_data(shared, pack):
    client = ChatLike(reply=masked_reply(pack))
    ask(shared, client, GROK)
    call = client.calls[0]
    assert [m["role"] for m in call["messages"]] == ["system", "user"]
    assert "Never state or imply guilt" in call["messages"][0]["content"]
    assert "PERSON_1" in call["messages"][0]["content"]  # told how placeholders work
    assert call["max_tokens"] >= 4000


@pytest.mark.parametrize(
    "client",
    [
        ChatLike(exc=openai.APITimeoutError(request=httpx2.Request("POST", "https://x.test"))),
        ChatLike(exc=openai.APIConnectionError(request=httpx2.Request("POST", "https://x.test"))),
        ChatLike(exc=RuntimeError("boom")),
        ChatLike(reply="cut off", finish="length"),
        ChatLike(reply="filtered", finish="content_filter"),
        ChatLike(reply="   "),
    ],
    ids=["timeout", "connection", "runtime error", "truncated", "filtered", "empty"],
)
def test_any_provider_problem_returns_the_template_and_logs_an_error(shared, pack, audit, client):
    brief = ask(shared, client, GROQ, audit=audit)
    assert brief.source == "template" and brief.text == template.render_template(pack)
    row = audit.list_llm_requests()[0]
    assert row["outcome"] == "error" and row["provider"] == "groq"
    assert not IDENTIFIER.findall(json.dumps(row))


def test_a_rejected_key_through_the_real_openai_client_falls_back(shared, pack, audit):
    def handler(request):
        return httpx2.Response(401, json={"error": {"message": "Invalid API Key", "type": "invalid_request_error"}})

    http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    client = openai.OpenAI(api_key="bad", base_url="https://api.groq.com/openai/v1", max_retries=0, http_client=http)
    brief = ask(shared, client, GROQ, audit=audit)
    assert brief.source == "template" and "AuthenticationError (401)" in brief.fallback_reason
    assert audit.list_llm_requests()[0]["detail"] == "AuthenticationError (401)"


def test_a_good_reply_through_the_real_openai_client_is_accepted_and_tokens_are_recorded(shared, pack, audit):
    text = masked_reply(pack)

    def handler(request):
        sent = request.content.decode()
        assert "PRV-A01" not in sent and "FAC-B01" not in sent  # the wire carries only placeholders
        body = {
            "id": "c1", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}],
            "usage": {"prompt_tokens": 1500, "completion_tokens": 900, "total_tokens": 2400},
        }  # fmt: skip
        return httpx2.Response(200, json=body)

    http = openai.DefaultHttpxClient(transport=httpx2.MockTransport(handler))
    client = openai.OpenAI(api_key="k", base_url="https://api.groq.com/openai/v1", max_retries=0, http_client=http)
    brief = ask(shared, client, GROQ, audit=audit)
    assert brief.source == "llm" and "PRV-A01" in brief.text
    row = audit.list_llm_requests()[0]
    assert (row["input_tokens"], row["output_tokens"]) == (1500, 900)


# ------------------------------------------------------------ the llm_requests table


def test_llm_requests_holds_field_names_and_token_counts_only(shared, pack, audit):
    ask(shared, ClaudeLike(reply=masked_reply(pack), usage=(321, 123)), audit=audit)
    ask(shared, ChatLike(reply=masked_reply(pack, lambda t: t.replace("[E1]", "[E99]", 1))), GROQ, audit=audit)
    ask(shared, ChatLike(exc=RuntimeError("secret detail PRV-A01")), GROK, audit=audit)
    rows = audit.list_llm_requests()
    assert [r["outcome"] for r in rows] == ["error", "validation_failed", "llm_ok"]
    ok = rows[2]
    assert ok["provider"] == "anthropic" and ok["model"] == "claude-opus-5-5"
    assert ok["tokens_by_kind"] == {"PERSON": 3, "ORG": 2}
    assert ok["dropped_fields"] == [] and (ok["input_tokens"], ok["output_tokens"]) == (321, 123)
    assert ok["case_id"] == RING and ok["ts"].endswith("Z")
    assert rows[0]["detail"] == "RuntimeError"  # the class only: the message could hold real values
    con = sqlite3.connect(audit.path)
    try:
        cells = [str(c) for row in con.execute("SELECT * FROM llm_requests") for c in row]
    finally:
        con.close()
    blob = " ".join(cells)
    assert not IDENTIFIER.findall(blob) and "PRV-A01" not in blob and "FAC-B01" not in blob
    con = sqlite3.connect(audit.path)
    try:
        columns = {c[1] for c in con.execute("PRAGMA table_info(llm_requests)")}
    finally:
        con.close()
    assert columns == {
        "request_id", "ts", "case_id", "provider", "model", "outcome", "tokens_by_kind",
        "dropped_fields", "input_tokens", "output_tokens", "detail",
    }  # no column could hold a prompt, a reply, a vault or a real value


def test_llm_requests_is_append_only_and_only_allows_known_outcomes(audit):
    audit.record_llm_request(case_id=RING, provider="groq", model="m", outcome="llm_ok",
                             audit={"tokens_by_kind": {"PERSON": 1}, "dropped_fields": []})  # fmt: skip
    con = sqlite3.connect(audit.path)
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("UPDATE llm_requests SET outcome = 'error'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM llm_requests")
        with pytest.raises(sqlite3.IntegrityError):
            con.execute("INSERT INTO llm_requests (ts, case_id, provider, outcome, tokens_by_kind, "
                        "dropped_fields) VALUES ('t', 'c', 'p', 'surprise', '{}', '[]')")  # fmt: skip
    finally:
        con.close()


def test_a_failure_to_record_never_breaks_the_brief(shared, pack):
    def broken(row):
        raise RuntimeError("disk full")

    brief = generate.generate_brief(
        RING, db_path=shared.db_path, client=ClaudeLike(reply=masked_reply(pack)), env=CLAUDE, record=broken
    )
    assert brief.source == "llm"


# ------------------------------------------------------------ through the API


@pytest.fixture
def api(shared, tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(generate, "load_env", lambda path=None: {})
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda path: shared)
    with TestClient(app) as test_client:
        yield test_client, app


def test_simultaneous_requests_for_one_brief_make_a_single_llm_call(api, pack, monkeypatch):
    client, app = api
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_API_KEY", "test-key")

    class Slow(ChatLike):
        def create(self, **kwargs):
            time.sleep(0.4)  # long enough for the other requests to arrive meanwhile
            return super().create(**kwargs)

    fake = Slow(reply=masked_reply(pack))
    monkeypatch.setattr(generate, "make_client", lambda p, key: fake)
    results = []

    def fetch():
        results.append(client.get(f"/cases/{RING}/brief").json())

    threads = [threading.Thread(target=fetch) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(fake.calls) == 1  # not four
    assert len(results) == 4 and all(r["source"] == "llm" for r in results)
    assert sum(r["cached"] for r in results) == 3
    assert len(app.state.runtime.audit.list_llm_requests()) == 1


def test_a_failed_llm_attempt_is_retried_after_a_minute_not_cached_forever(api, pack, monkeypatch):
    client, _ = api
    monkeypatch.setenv("LLM_PROVIDER", "groq")
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    flaky = ChatLike(exc=RuntimeError("rate limited"))
    monkeypatch.setattr(generate, "make_client", lambda p, key: flaky)
    first = client.get(f"/cases/{RING}/brief").json()
    assert first["source"] == "template" and "LLM call failed" in first["fallback_reason"]
    assert client.get(f"/cases/{RING}/brief").json()["cached"] is True  # no hammering the provider
    assert len(flaky.calls) == 1
    clock = time.monotonic() + 61  # a minute later the provider recovered
    monkeypatch.setattr("backend.api.main.time.monotonic", lambda: clock)
    flaky.exc, flaky.reply = None, masked_reply(pack)
    again = client.get(f"/cases/{RING}/brief").json()
    assert again["source"] == "llm" and again["cached"] is False and len(flaky.calls) == 2
    assert client.get(f"/cases/{RING}/brief").json()["cached"] is True  # a success is kept


def test_the_api_reports_the_template_when_no_provider_is_set(api):
    client, app = api
    body = client.get(f"/cases/{RING}/brief").json()
    assert body["source"] == "template" and body["provider_label"] == "Template"
    assert body["provider"] is None and body["masked"] is False
    assert app.state.runtime.audit.list_llm_requests() == []


@pytest.mark.parametrize(
    ("provider", "label", "make"),
    [("anthropic", "Claude", ClaudeLike), ("xai", "Grok", ChatLike), ("groq", "Groq", ChatLike)],
)
def test_the_api_reports_which_provider_wrote_the_brief_and_logs_the_request(api, shared, pack, monkeypatch, provider, label, make):
    client, app = api
    monkeypatch.setenv("LLM_PROVIDER", provider)
    monkeypatch.setenv("LLM_API_KEY", "test-key")
    fake = make(reply=masked_reply(pack))
    monkeypatch.setattr(generate, "make_client", lambda p, key: fake)
    body = client.get(f"/cases/{RING}/brief").json()
    assert body["source"] == "llm" and body["provider"] == provider and body["provider_label"] == label
    assert body["masked"] is True and body["fallback_reason"] is None
    assert not PLACEHOLDER.findall(body["brief"]) and "PRV-A01" in body["brief"]
    assert not IDENTIFIER.findall(fake.sent)
    rows = app.state.runtime.audit.list_llm_requests()
    assert len(rows) == 1 and rows[0]["outcome"] == "llm_ok" and rows[0]["provider"] == provider
    again = client.get(f"/cases/{RING}/brief").json()  # cached: no second request
    assert again["cached"] is True and len(app.state.runtime.audit.list_llm_requests()) == 1
