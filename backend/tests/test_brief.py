import json
import logging
import re
import shutil
import sqlite3
from types import SimpleNamespace

import anthropic
import httpx2
import pytest

from backend.brief import evidence, generate, llm_payload, template, validator
from backend.brief.evidence import CaseNotFoundError, build_pack
from backend.cases import ranking
from backend.data import generator as gen
from backend.detect import anomaly, engine, graph
from backend.predict import model as risk_model

SECRET = "sk-ant-TEST-secret-key-do-not-log"
CLAUDE = {"LLM_PROVIDER": "anthropic", "LLM_API_KEY": "test-key"}  # an LLM is switched on
REAL_ANTHROPIC = anthropic.Anthropic  # captured before any test patches the module
CITATION = re.compile(r"\[(E\d+)\]")


# ------------------------------------------------------------ fixtures and helpers


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    out = tmp_path_factory.mktemp("m7")
    db = out / "t.db"
    gen.generate(db, out / "truth.csv")
    engine.run(db)
    anomaly.run(db)
    graph.run(db)
    risk_model.run(db, out / "metrics.json")
    result = ranking.run(db)
    by_primary = {c.primary_entity: c.case_id for c in result.cases}
    return {"db": db, "dir": out, "cases": result.cases, "by_primary": by_primary,
            "ring": by_primary["RING-01"]}  # fmt: skip


@pytest.fixture(scope="module")
def ring_pack(world):
    return build_pack(world["ring"], db_path=world["db"])


class FakeClient:
    """Stands in for the SDK client: returns a reply or raises, and records each call."""

    def __init__(self, reply="", exc=None, stop_reason="end_turn"):
        self.reply, self.exc, self.stop_reason, self.calls = reply, exc, stop_reason, []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        block = SimpleNamespace(type="text", text=self.reply)
        return SimpleNamespace(content=[block], stop_reason=self.stop_reason)


def message_json(text, stop_reason="end_turn"):
    return {
        "id": "msg_test", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
        "content": [{"type": "text", "text": text}], "stop_reason": stop_reason,
        "stop_sequence": None, "usage": {"input_tokens": 5, "output_tokens": 5},
    }  # fmt: skip


def sdk_client(handler):
    """A real SDK client wired to a mock transport, so no network call is made."""
    transport = httpx2.MockTransport(handler)
    return REAL_ANTHROPIC(
        api_key=SECRET, max_retries=0, http_client=anthropic.DefaultHttpxClient(transport=transport)
    )


def rejected_401(request):
    body = {"type": "error", "error": {"type": "authentication_error", "message": "invalid x-api-key"}}
    return httpx2.Response(401, json=body)


def citations(text):
    return CITATION.findall(text)


# Each mutation turns a valid brief into one the validator must reject.
MUTATIONS = {
    "unknown citation": lambda t: t.replace("[E1]", "[E99]", 1),
    "malformed citation": lambda t: t.replace("[E2]", "[E1, E2]", 1),
    "missing section": lambda t: t.replace("## Timeline\n", ""),
    "sections out of order": lambda t: t.replace("## Timeline", "## TMP").replace(
        "## Network context", "## Timeline"
    ).replace("## TMP", "## Network context"),
    "banned: guilty": lambda t: t.replace("not a finding of wrongdoing", "the provider is guilty"),
    "banned: fraudster": lambda t: t.replace("suspicious", "a fraudster", 1),
    "banned: committed fraud": lambda t: t.replace("suspicious", "committed fraud", 1),
    "banned: fraudulent": lambda t: t.replace("suspicious", "fraudulent", 1),
    "banned: criminal": lambda t: t.replace("suspicious", "criminal", 1),
    "confidence changed": lambda t: t.replace("Confidence level: Medium", "Confidence level: High"),
    "limitation reworded": lambda t: t.replace("do not describe real", "describe real"),
    "limitation removed": lambda t: re.sub(r"- Synthetic data:.*\n", "", t),
    "no final line": lambda t: t.replace(template.FINAL_LINE, ""),
    "text after final line": lambda t: t + "Extra remark.\n",
    "no citation in summary": lambda t: re.sub(
        r"\[E\d+\]", "", t.split("## Evidence")[0]
    ) + "## Evidence" + t.split("## Evidence", 1)[1],
    "no citation in evidence": lambda t: t.split("## Evidence")[0]
    + "## Evidence"
    + re.sub(r"\[E\d+\]", "", t.split("## Evidence", 1)[1].split("## Timeline")[0])
    + "## Timeline"
    + t.split("## Timeline", 1)[1],
}


# ------------------------------------------------------------ pack: ring case


def test_ring_pack_keys_findings_and_claims(world, ring_pack):
    pack = ring_pack
    assert pack.keys == [f"E{i}" for i in range(1, len(pack.evidence) + 1)]
    assert {e.detector for e in pack.evidence} == {"anomaly", "ring"}
    con = sqlite3.connect(world["db"])
    try:
        known = {r[0] for r in con.execute("SELECT claim_id FROM claims")}
        findings = {r[0] for r in con.execute("SELECT finding_id FROM findings")}
    finally:
        con.close()
    for item in pack.evidence:
        assert item.finding_id in findings and item.claim_ids and set(item.claim_ids) <= known
    ring = next(e for e in pack.evidence if e.detector == "ring")
    assert len(ring.claim_ids) == 180 and ring.scope == "claim-specific"
    assert next(e for e in pack.evidence if e.detector == "anomaly").scope == "provider-level"
    assert [e.severity for e in pack.evidence] == sorted(
        (e.severity for e in pack.evidence), key={"high": 0, "medium": 1, "low": 2}.get
    )  # most severe first


def test_ring_pack_timeline_is_sorted_and_fully_cited(ring_pack):
    pack = ring_pack
    assert pack.timeline
    assert [(t.date, t.kind) for t in pack.timeline] == sorted((t.date, t.kind) for t in pack.timeline)
    assert all(t.evidence_keys and set(t.evidence_keys) <= set(pack.keys) for t in pack.timeline)
    assert sum(t.count for t in pack.timeline) == 180  # only the claim-specific evidence
    assert sum(t.amount for t in pack.timeline) == pack.flagged_amount == 345703


def test_ring_pack_network_context(ring_pack):
    net = ring_pack.network
    ids = {d["id"] for d in net["entities"]}
    assert ids == {"PRV-A01", "FAC-B01", "FAC-C01", "OWN-001", "OWN-002"}
    owners = {o["owner_id"]: o for o in net["owners"]}
    assert owners["OWN-001"]["related_to"] == "OWN-002" and owners["OWN-002"]["related_to"] == "OWN-001"
    assert set(owners["OWN-001"]["owns"]) == {"FAC-B01", "PRV-A01"}
    pairs = {(p["referrer"], p["receiver"]): p for p in net["referral_pairs"]}
    assert pairs[("PRV-A01", "FAC-B01")]["shared_members"] == 15
    assert pairs[("PRV-A01", "FAC-B01")]["flagged"] and pairs[("PRV-A01", "FAC-B01")]["self_referral"]
    assert net["member_count"] == 15


def test_ring_pack_prediction_confidence_limits_and_action(ring_pack):
    pack = ring_pack
    assert pack.prediction["available"] and pack.prediction["provider_id"] == "PRV-A01"
    assert pack.prediction["risk_band"] == "Low" and pack.prediction["horizon_days"] == 30
    assert pack.detectors_fired == ["anomaly", "graph"]
    assert pack.confidence["level"] == "Medium"  # 2 of 3 detector groups
    assert pack.limitations == [evidence.SYNTHETIC_LIMITATION]
    assert pack.recommended_action["tier"] == "full-review" and pack.worst_severity == "high"
    json.dumps(pack.to_dict())  # serialisable for the LLM prompt and for storage


# ------------------------------------------------------------ pack: provider cases


def test_single_detector_provider_case_is_low_confidence(world):
    pack = build_pack(world["by_primary"]["PRV-005"], db_path=world["db"])
    assert pack.detectors_fired == ["rules"] and pack.confidence["level"] == "Low"
    assert evidence.SINGLE_DETECTOR_LIMITATION in pack.limitations
    assert pack.limitations[0] == evidence.SYNTHETIC_LIMITATION
    assert pack.recommended_action["tier"] == "verify"
    assert any(e.detector == "upcoding" for e in pack.evidence)


def test_repeat_offender_pack_shows_prior_investigation_and_escalated_band(world):
    pack = build_pack(world["by_primary"]["PRV-010"], db_path=world["db"])
    kinds = {t.kind for t in pack.timeline}
    assert "prior investigation" in kinds and "repeat_history claims" in kinds
    prior = next(t for t in pack.timeline if t.kind == "prior investigation")
    assert prior.description.startswith("Investigation CASE-") and prior.date < "2026-01-01"
    assert pack.prediction["risk_band"] == "High" and pack.prediction["band_source"] == "escalated"


def test_phantom_pack_timeline_lists_the_inpatient_stays(world):
    pack = build_pack(world["by_primary"]["PRV-007"], db_path=world["db"])
    stays = [t for t in pack.timeline if t.kind == "inpatient stay"]
    assert len(stays) == 10 and all(t.date <= t.end_date for t in stays)


def test_unknown_case_raises(world):
    with pytest.raises(CaseNotFoundError):
        build_pack("CASE-9999", db_path=world["db"])


def test_missing_prediction_is_reported_as_insufficient_data(world, tmp_path):
    other = build_pack(world["ring"], horizon=60, db_path=world["db"])  # only 30-day exists
    assert not other.prediction["available"] and "Insufficient data" in other.prediction["reason"]
    assert evidence.NO_PREDICTION_LIMITATION in other.limitations
    assert "no prediction available" in other.confidence["reasons"]
    db = tmp_path / "nopred.db"
    shutil.copy(world["db"], db)
    con = sqlite3.connect(db)
    con.execute("DROP TABLE investigation_risk")
    con.commit()
    con.close()
    assert not build_pack(world["ring"], db_path=db).prediction["available"]


# ------------------------------------------------------------ confidence, limitations, action


@pytest.mark.parametrize(
    ("detectors", "band", "history", "level"),
    [
        (3, "High", 120, "High"),
        (3, "Medium", 120, "Medium"),  # all detectors but no High prediction
        (3, None, 120, "Medium"),
        (2, "High", 120, "Medium"),
        (2, "Low", 120, "Medium"),
        (1, "High", 120, "Low"),
        (0, None, 120, "Low"),
        (3, "High", 59, "Low"),  # short history overrides everything
        (2, "Low", 30, "Low"),
        (3, "High", 60, "High"),  # exactly 60 days is enough
    ],
)
def test_confidence_matrix(detectors, band, history, level):
    result = evidence.compute_confidence(detectors, band, history)
    assert result["level"] == level and result["reasons"]


def test_limitations_always_include_synthetic_and_add_conditionally():
    assert evidence.compute_limitations(2, 120, True) == [evidence.SYNTHETIC_LIMITATION]
    single = evidence.compute_limitations(1, 120, True)
    assert single == [evidence.SYNTHETIC_LIMITATION, evidence.SINGLE_DETECTOR_LIMITATION]
    both = evidence.compute_limitations(1, 30, True)
    assert both[1:] == [evidence.SINGLE_DETECTOR_LIMITATION, evidence.SHORT_HISTORY_LIMITATION]
    short = evidence.compute_limitations(3, 59, True)
    assert short == [evidence.SYNTHETIC_LIMITATION, evidence.SHORT_HISTORY_LIMITATION]
    assert evidence.NO_PREDICTION_LIMITATION in evidence.compute_limitations(3, 120, False)
    assert "ynthetic" in evidence.SYNTHETIC_LIMITATION


@pytest.mark.parametrize(
    ("severity", "detectors", "tier"),
    [
        ("high", 3, "full-review"), ("high", 2, "full-review"), ("high", 1, "verify"),
        ("medium", 2, "scheduled-review"), ("medium", 1, "routine-review"),
        ("low", 3, "monitor"), ("low", 1, "monitor"), ("critical", 1, "verify"),
    ],
)  # fmt: skip
def test_recommended_action_by_severity_and_evidence(severity, detectors, tier):
    action = evidence.recommend_action(severity, detectors)
    assert action["tier"] == tier
    assert "No claim should be denied and no payment blocked" in action["text"]
    assert not re.search(r"\b(deny|block|reject)\b(?! on)", action["text"].replace(
        "No claim should be denied and no payment blocked", ""
    ))  # advice never instructs a denial or a block


# ------------------------------------------------------------ template


def test_template_has_seven_sections_in_order_and_the_final_line(ring_pack):
    text = template.render_template(ring_pack)
    assert [t for t, _ in validator.parse_sections(text)] == template.SECTIONS
    assert len(template.SECTIONS) == 7
    assert text.rstrip().splitlines()[-1] == "Final decision rests with the assigned investigator."
    assert text.endswith("investigator.\n")


def test_template_is_valid_deterministic_and_cites_only_known_evidence(world, ring_pack):
    text = template.render_template(ring_pack)
    assert validator.validate_brief(text, ring_pack) == []
    assert template.render_template(build_pack(world["ring"], db_path=world["db"])) == text
    found = citations(text)
    assert found and set(found) <= set(ring_pack.keys)
    assert set(ring_pack.keys) <= set(found)  # every finding is actually cited
    lowered = text.lower()
    assert "suspicious" in lowered and "warrants review" in lowered
    assert "fraud" not in lowered and "guilty" not in lowered
    assert "Confidence level: Medium" in text and "Synthetic data" in text


def test_every_case_gets_a_valid_template_brief(world):
    for case in world["cases"]:
        pack = build_pack(case.case_id, db_path=world["db"])
        text = template.render_template(pack)
        assert validator.validate_brief(text, pack) == [], case.case_id
        assert f"Confidence level: {pack.confidence['level']}" in text


# ------------------------------------------------------------ validator


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_validator_rejects_bad_briefs(ring_pack, name):
    good = template.render_template(ring_pack)
    bad = MUTATIONS[name](good)
    assert bad != good
    assert validator.validate_brief(bad, ring_pack), name


def test_validator_names_the_unknown_citation(ring_pack):
    bad = template.render_template(ring_pack).replace("[E1]", "[E99]", 1)
    assert "unknown citation [E99]" in validator.validate_brief(bad, ring_pack)


def test_banned_words_are_matched_case_insensitively(ring_pack):
    bad = template.render_template(ring_pack).replace("suspicious", "GUILTY", 1)
    assert any("guilty" in r for r in validator.validate_brief(bad, ring_pack))


# ------------------------------------------------------------ generation


@pytest.mark.parametrize(
    ("env", "why"),
    [
        ({}, "LLM_PROVIDER is none"),  # the default: no LLM at all
        ({"LLM_PROVIDER": "none", "LLM_API_KEY": "k"}, "LLM_PROVIDER is none"),  # a key alone does nothing
        ({"LLM_PROVIDER": "anthropic"}, "no LLM_API_KEY"),
        ({"LLM_PROVIDER": "xai", "LLM_API_KEY": ""}, "no LLM_API_KEY"),
        ({"LLM_PROVIDER": "groq", "LLM_API_KEY": "   "}, "no LLM_API_KEY"),
        ({"LLM_PROVIDER": "bogus", "LLM_API_KEY": "k"}, "unknown LLM_PROVIDER"),
    ],
)
def test_without_a_provider_or_a_key_the_template_is_used(world, ring_pack, env, why):
    brief = generate.generate_brief(world["ring"], db_path=world["db"], env=env)
    assert brief.source == "template" and why in brief.fallback_reason
    assert brief.provider is None and brief.masked is False
    assert brief.text == template.render_template(ring_pack)


def test_a_valid_llm_brief_is_used(world, ring_pack):
    drafted = template.render_template(ring_pack).replace(
        "# Investigation brief", "# Investigation brief (drafted)"
    )
    client = FakeClient(reply=drafted)
    brief = generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=CLAUDE)
    assert brief.source == "llm" and brief.fallback_reason is None
    assert brief.provider == "anthropic" and brief.masked is True
    assert brief.text.startswith("# Investigation brief (drafted)")
    assert set(citations(brief.text)) <= set(ring_pack.keys)
    assert brief.model == "claude-opus-5-5" and client.calls[0]["model"] == "claude-opus-5-5"


def test_the_model_can_be_overridden(world, ring_pack):
    client = FakeClient(reply=template.render_template(ring_pack))
    env = {**CLAUDE, "LLM_MODEL": "claude-sonnet-5-5"}
    generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=env)
    assert client.calls[0]["model"] == "claude-sonnet-5-5"


def test_the_prompt_is_built_from_the_pack_and_states_the_rules(world, ring_pack):
    client = FakeClient(reply=template.render_template(ring_pack))
    generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=CLAUDE)
    call = client.calls[0]
    system, user = call["system"], call["messages"][0]["content"]
    for rule in ("Use only the evidence pack", "[E1]", "suspicious", "Never state or imply guilt",
                 "Do not change the confidence level or the limitations", "PERSON_1",
                 template.FINAL_LINE):  # fmt: skip
        assert rule in system, rule
    assert "Confidence level: Medium" in user and evidence.SYNTHETIC_LIMITATION in user
    assert '"evidence_key": "E1"' in user and '"count": 180' in user
    assert user.count("CLM-") < 40 and len(user) < 60_000  # claim lists are trimmed
    assert call["max_tokens"] >= 4000


@pytest.mark.parametrize("name", list(MUTATIONS))
def test_invalid_llm_text_falls_back_to_the_template(world, ring_pack, name):
    good = template.render_template(ring_pack)
    client = FakeClient(reply=MUTATIONS[name](good))
    brief = generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=CLAUDE)
    assert brief.source == "template" and brief.text == good
    assert "LLM text rejected" in brief.fallback_reason


@pytest.mark.parametrize(
    "client",
    [
        FakeClient(reply=""),
        FakeClient(reply="short", stop_reason="refusal"),
        FakeClient(reply="cut off", stop_reason="max_tokens"),
        FakeClient(exc=RuntimeError("boom")),
        FakeClient(exc=anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x.test"))),
        FakeClient(exc=anthropic.APITimeoutError(request=httpx2.Request("POST", "https://x.test"))),
    ],
    ids=["empty", "refusal", "truncated", "runtime error", "connection error", "timeout"],
)
def test_llm_problems_fall_back_to_the_template(world, ring_pack, client):
    brief = generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=CLAUDE)
    assert brief.source == "template" and brief.text == template.render_template(ring_pack)
    assert brief.fallback_reason


def test_an_invalid_api_key_still_returns_the_template_brief(world, ring_pack):
    client = sdk_client(rejected_401)  # the real SDK, answered with a 401
    brief = generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=CLAUDE)
    assert brief.source == "template"
    assert "AuthenticationError" in brief.fallback_reason and "401" in brief.fallback_reason
    assert brief.text == template.render_template(ring_pack)
    assert validator.validate_brief(brief.text, ring_pack) == []


def test_a_good_reply_through_the_real_sdk_is_accepted(world, ring_pack):
    text = template.render_template(ring_pack)
    client = sdk_client(lambda request: httpx2.Response(200, json=message_json(text)))
    brief = generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=CLAUDE)
    assert brief.source == "llm" and brief.text == text


def test_the_ring_brief_has_only_valid_citations_with_or_without_the_llm(world, ring_pack):
    for client, env in ((None, {}), (sdk_client(rejected_401), CLAUDE)):
        brief = generate.generate_brief(world["ring"], db_path=world["db"], client=client, env=env)
        found = citations(brief.text)
        assert found and set(found) <= set(ring_pack.keys)
        assert not re.findall(r"\[E(?!\d+\])", brief.text)  # no malformed citations
        assert brief.text.rstrip().endswith(template.FINAL_LINE)


def test_the_api_key_is_only_given_to_the_client_and_never_leaks(world, ring_pack, monkeypatch, caplog):
    seen = {}

    def fake_anthropic(**kwargs):
        seen.update(kwargs)
        return sdk_client(rejected_401)

    monkeypatch.setattr(generate.anthropic, "Anthropic", fake_anthropic)
    with caplog.at_level(logging.DEBUG):
        brief = generate.generate_brief(
            world["ring"], db_path=world["db"],
            env={"LLM_PROVIDER": "anthropic", "LLM_API_KEY": SECRET},
        )
    assert seen["api_key"] == SECRET and seen["max_retries"] == 0 and seen["timeout"] == 15.0
    assert brief.source == "template"
    assert SECRET not in brief.text and SECRET not in (brief.fallback_reason or "")
    assert SECRET not in caplog.text
    prompt = generate.build_prompt(llm_payload.prepare(ring_pack)[0], ring_pack)
    assert SECRET not in prompt and "LLM_API_KEY" not in prompt


def test_env_file_is_read_but_the_environment_wins(tmp_path):
    env_file = tmp_path / ".env"
    env_file.write_text("# comment\nLLM_API_KEY='from-file'\nLLM_MODEL=claude-haiku-5-5\n", "utf-8")
    values = generate.load_env(env_file)
    assert values == {"LLM_API_KEY": "from-file", "LLM_MODEL": "claude-haiku-5-5"}
    assert generate.load_env(tmp_path / "missing.env") == {}
    assert generate.resolve_setting("LLM_API_KEY", {"LLM_API_KEY": " x "}) == "x"
    assert generate.resolve_setting("LLM_API_KEY", {}) == ""


def test_unknown_case_is_not_hidden_by_the_fallback(world):
    with pytest.raises(CaseNotFoundError):
        generate.generate_brief("CASE-9999", db_path=world["db"], env={})
