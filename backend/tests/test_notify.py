"""Notifications, the urgent email, and drafted outbound messages (AWS SNS is mocked)."""

import re
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api import views
from backend.api.main import create_app
from backend.audit import AuditLog
from backend.cases import ranking
from backend.notify import alerts, emails, notifier, outbound
from backend.notify.notifier import NotifyConfig, SnsEmailNotifier
from backend.notify.store import NotifyStore

ARN = "arn:aws:sns:ap-south-1:123456789012:claimshield-test"
BASE = "https://app.example.test"
CONFIG = NotifyConfig(email_enabled=True, topic_arn=ARN, region="ap-south-1", base_url=BASE)
T0 = datetime(2026, 10, 8, 9, 0, tzinfo=UTC)
RING = "CASE-0001"
RECORD_ID = re.compile(r"\b(?:PRV|MEM|OWN|FAC|CLM)-[A-Z0-9]+\b")
GOOD = {"approved_by": "Asha Rao", "reason": "Checked the wording"}


class FakeSns:
    """Stands in for the boto3 SNS client and remembers every publish."""

    def __init__(self, exc=None):
        self.calls, self.exc = [], exc

    def publish(self, **kwargs):
        self.calls.append(kwargs)
        if self.exc:
            raise self.exc
        return {"MessageId": "m-1"}


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("m9b") / "claimshield.db")


@pytest.fixture
def parts(tmp_path):
    path = tmp_path / "audit.db"
    return NotifyStore(path), AuditLog(path)


def run(shared, parts, sns=None, now=T0, config=CONFIG, decided=frozenset()):
    store, audit = parts
    queue = views.build_queue(shared, {}, 40.0, ranking.Weights(), False, {})
    email = SnsEmailNotifier(config, lambda: sns) if sns is not None else None
    return alerts.run_alerts(
        cases=shared.cases, ranks={i.case_id: i.rank for i in queue.scheduled + queue.backlog},
        backlog_count=len(queue.backlog), decided=set(decided), store=store, audit=audit,
        email=email, config=config, now=now,
    )


def urgent_cases(shared):
    queue = views.build_queue(shared, {}, 40.0, ranking.Weights(), False, {})
    rank = {i.case_id: i.rank for i in queue.scheduled + queue.backlog}
    return {c.case_id for c in shared.cases if alerts.priority_reasons(c, rank[c.case_id])[1]}


# ------------------------------------------------------------ in-app notifications


def test_a_new_case_creates_an_in_app_notification_for_each_case(shared, parts):
    run(shared, parts)
    store, _ = parts
    new = [n for n in store.list_notifications() if n["type"] == "new_case"]
    assert sorted(n["case_id"] for n in new) == sorted(c.case_id for c in shared.cases)
    assert {n["recipient_role"] for n in new} == {"siu"} and not any(n["read"] for n in new)


def test_a_rerun_does_not_repeat_what_was_already_announced(shared, parts):
    run(shared, parts)
    before = len(parts[0].list_notifications())
    run(shared, parts, now=T0 + timedelta(hours=1))
    assert len(parts[0].list_notifications()) == before


def test_a_new_finding_on_a_known_case_creates_a_warning(shared, parts):
    run(shared, parts)
    store, _ = parts
    case = shared.cases[0]
    store.put_seen(case.case_id, sorted(case.finding_ids)[1:])  # as if the last run knew one fewer
    result = run(shared, parts, now=T0 + timedelta(hours=1))
    found = [n for n in result.created if n["type"] == "new_finding"]
    assert len(found) == 1 and found[0]["severity"] == "warning" and found[0]["case_id"] == case.case_id


def test_capacity_and_pending_alerts(shared, parts):
    result = run(shared, parts)
    queue = views.build_queue(shared, {}, 40.0, ranking.Weights(), False, {})
    capacity = [n for n in result.created if n["type"] == "capacity"]
    if queue.backlog:
        assert capacity[0]["message"] == f"Capacity exceeded: {len(queue.backlog)} cases in backlog."
        assert capacity[0]["severity"] == "warning"
    later = run(shared, parts, now=T0 + timedelta(days=4), decided={RING})
    pending = [n for n in later.created if n["type"] == "decision_pending"]
    assert pending and all(n["recipient_role"] == "manager" and n["severity"] == "warning" for n in pending)
    assert RING not in {n["case_id"] for n in pending}  # a decided case is not pending
    assert not [n for n in run(shared, parts, now=T0 + timedelta(days=4), decided={RING}).created
                if n["type"] == "decision_pending"]  # said once  # fmt: skip


def test_high_priority_notifications_come_first_and_only_urgent_ones_email(shared, parts):
    sns = FakeSns()
    run(shared, parts, sns)
    store, _ = parts
    listed = store.list_notifications("siu")
    severities = [n["severity"] for n in listed]
    assert severities == sorted(severities, key={"high": 0, "warning": 1, "info": 2}.get)
    assert severities[0] == "high"
    for n in (n for n in listed if n["type"] == "high_priority"):
        urgent = n["case_id"] in urgent_cases(shared)
        assert n["email_status"] == ("sent" if urgent else "not_required")


# ------------------------------------------------------------ the email


def test_each_urgent_case_publishes_exactly_one_email_and_a_rerun_within_24h_sends_none(shared, parts):
    sns = FakeSns()
    first = run(shared, parts, sns)
    urgent = urgent_cases(shared)
    assert len(sns.calls) == len(urgent) > 0
    assert sorted(e["case_id"] for e in first.emails) == sorted(urgent)
    assert all(e["email_status"] == "sent" for e in first.emails)
    second = run(shared, parts, sns, now=T0 + timedelta(hours=23))
    assert len(sns.calls) == len(urgent)  # nothing more was published
    assert {e["email_status"] for e in second.emails} == {"skipped_cooldown"}
    run(shared, parts, sns, now=T0 + timedelta(hours=25))  # a day later it may email again
    assert len(sns.calls) == 2 * len(urgent)


def test_the_email_holds_only_the_case_id_rank_detector_count_and_link(shared, parts):
    sns = FakeSns()
    run(shared, parts, sns)
    queue = views.build_queue(shared, {}, 40.0, ranking.Weights(), False, {})
    rank = {i.case_id: i.rank for i in queue.scheduled + queue.backlog}
    for call in sns.calls:
        assert call["TopicArn"] == ARN
        subject, body = call["Subject"], call["Message"]
        case_id = re.search(r"CASE-\d{4}", subject).group(0)
        case = shared.case(case_id)
        assert subject == f"ClaimShield: high-priority case {case_id} awaiting review"
        assert f"Case: {case_id}" in body and f"Priority rank: {rank[case_id]}" in body
        assert f"Detectors agreeing: {len(case.detectors_fired)}" in body
        assert f"{BASE}/cases/{case_id}" in body
        text = re.sub(r"https?://\S+", "", subject + body)
        assert not RECORD_ID.search(text)
        assert not re.search(r"₹|\bRs\b|\d+\.\d+|\d,\d{3}", text)
        assert not re.search(r"fraud|guilty", text, re.IGNORECASE)
        for private in [*case.affected_members, *case.entity_ids, *case.flagged_claim_ids]:
            assert private not in text
        assert set(re.findall(r"CASE-\d{4}", text)) == {case_id}


@pytest.mark.parametrize(
    "tamper",
    [
        lambda s, b: (s, b + "Member MEM-00012 is involved.\n"),
        lambda s, b: (s, b + "Claim CLM-0000123 is involved.\n"),
        lambda s, b: (s, b + "Amount at stake: Rs 45,000\n"),
        lambda s, b: (s, b + "Risk 0.82\n"),
        lambda s, b: (s.replace("high-priority", "fraud"), b),
        lambda s, b: (s, b.replace("awaiting human review", "guilty")),
    ],
    ids=["member", "claim", "amount", "score", "fraud-word", "guilty-word"],
)
def test_the_email_validator_blocks_personal_or_forbidden_content(tamper):
    subject, body = emails.build_email(RING, 2, 3, BASE)
    emails.validate_email(subject, body, RING, 2, 3, BASE)  # the template itself is fine
    bad_subject, bad_body = tamper(subject, body)
    with pytest.raises(emails.EmailBlocked):
        emails.validate_email(bad_subject, bad_body, RING, 2, 3, BASE)


def test_a_blocked_email_is_not_sent_and_is_logged(shared, parts, monkeypatch):
    monkeypatch.setattr(emails, "build_email", lambda *a: ("fraud alert", "MEM-00001"))
    sns = FakeSns()
    result = run(shared, parts, sns)
    assert not sns.calls and {e["email_status"] for e in result.emails} == {"failed"}
    attempts = [e for e in parts[1].list(500) if e["event_type"] == "email_attempt"]
    assert attempts and all("blocked" in e["details"]["error"] for e in attempts)


def test_an_sns_error_keeps_the_in_app_notification_and_marks_the_email_failed(shared, parts):
    sns = FakeSns(exc=RuntimeError("AccessDenied for arn:aws:iam::123456789012:user/x"))
    result = run(shared, parts, sns)
    assert {e["email_status"] for e in result.emails} == {"failed"}
    high = [n for n in parts[0].list_notifications() if n["type"] == "high_priority"]
    assert high and all(n["severity"] == "high" for n in high)
    assert {n["email_status"] for n in high if n["case_id"] in urgent_cases(shared)} == {"failed"}
    logged = [e for e in parts[1].list(500) if e["event_type"] == "email_attempt"]
    assert all(e["details"]["error"] == "RuntimeError" for e in logged)  # class only: no account ids
    retry = run(shared, parts, FakeSns(), now=T0 + timedelta(hours=1))  # a failed email is retried
    assert {e["email_status"] for e in retry.emails} == {"sent"}


def test_email_off_or_unconfigured_never_crashes_and_keeps_the_in_app_alerts(shared, parts):
    off = run(shared, parts, None, config=NotifyConfig(email_enabled=False))
    assert {e["email_status"] for e in off.emails} == {"disabled"}
    assert [n for n in off.created if n["type"] == "high_priority"]
    unconfigured = notifier.email_channel(NotifyConfig(email_enabled=True))
    assert isinstance(unconfigured, notifier.ConsoleNotifier)
    assert unconfigured.send(notifier.Message("s", "b")) == "failed"
    assert notifier.email_channel(NotifyConfig(email_enabled=False)) is None
    assert isinstance(notifier.email_channel(CONFIG), SnsEmailNotifier)


def test_the_sns_client_uses_a_five_second_timeout_and_no_retries(monkeypatch):
    seen = {}

    def fake_client(service, **kwargs):
        seen.update(kwargs, service=service)
        return FakeSns()

    monkeypatch.setattr(notifier.boto3, "client", fake_client)
    assert SnsEmailNotifier(CONFIG).send(notifier.Message("s", "b")) == "sent"
    assert seen["service"] == "sns" and seen["region_name"] == "ap-south-1"
    assert seen["config"].connect_timeout == 5 and seen["config"].read_timeout == 5
    assert seen["config"].retries["max_attempts"] == 0


def test_a_timeout_is_a_failed_email_not_a_crash():
    class Slow(Exception):
        pass

    Slow.__name__ = "ReadTimeoutError"
    channel = SnsEmailNotifier(CONFIG, lambda: FakeSns(exc=Slow("timed out")))
    assert channel.send(notifier.Message("s", "b")) == "failed" and channel.last_error == "ReadTimeoutError"


# ------------------------------------------------------------ through the API


@pytest.fixture
def api(shared, tmp_path, monkeypatch):
    sns = FakeSns()
    monkeypatch.setattr(notifier.boto3, "client", lambda *a, **k: sns)
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SNS_TOPIC_ARN", ARN)
    monkeypatch.setenv("AWS_REGION", "ap-south-1")
    monkeypatch.setenv("APP_BASE_URL", BASE)
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda path: shared)
    with TestClient(app) as client:
        yield client, app, sns


def test_a_pipeline_run_fills_the_bell_with_high_priority_first_and_one_email_per_urgent_case(api, shared):
    client, _app, sns = api
    body = client.get("/notifications", params={"role": "siu"}).json()
    notes = body["notifications"]
    assert body["unread_count"] == len(notes) > 0
    assert notes[0]["severity"] == "high"
    assert [n["severity"] for n in notes] == sorted((n["severity"] for n in notes),
                                                    key={"high": 0, "warning": 1, "info": 2}.get)  # fmt: skip
    assert len(sns.calls) == len(urgent_cases(shared))
    client.post("/admin/rerun")  # the same findings again: nothing new, no second email
    assert len(sns.calls) == len(urgent_cases(shared))
    assert len(client.get("/notifications").json()["notifications"]) == len(notes)


def test_an_sns_outage_does_not_stop_the_api_or_the_in_app_alerts(shared, tmp_path, monkeypatch):
    broken = FakeSns(exc=ConnectionError("unreachable"))
    monkeypatch.setattr(notifier.boto3, "client", lambda *a, **k: broken)
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "true")
    monkeypatch.setenv("SNS_TOPIC_ARN", ARN)
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda path: shared)
    with TestClient(app) as client:
        assert client.get("/health").json()["ready"] is True
        notes = client.get("/notifications").json()["notifications"]
        assert notes and {n["email_status"] for n in notes if n["severity"] == "high"} >= {"failed"}
        assert client.get("/queue").status_code == 200


def test_with_email_disabled_the_alerts_still_work(shared, tmp_path, monkeypatch):
    monkeypatch.setattr(notifier.boto3, "client", lambda *a, **k: pytest.fail("AWS must not be called"))
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda path: shared)
    with TestClient(app) as client:
        notes = client.get("/notifications").json()["notifications"]
        assert notes and {n["email_status"] for n in notes if n["severity"] == "high"} <= {
            "disabled", "not_required"}  # fmt: skip


def test_reading_notifications(api):
    client, _, _ = api
    notes = client.get("/notifications").json()["notifications"]
    first = notes[0]["id"]
    assert client.post(f"/notifications/{first}/read").json()["read"] is True
    unread = client.get("/notifications", params={"unread_only": True}).json()
    assert first not in {n["id"] for n in unread["notifications"]}
    assert unread["unread_count"] == len(notes) - 1
    assert client.post("/notifications/999999/read").status_code == 404
    assert client.post("/notifications/read-all").json()["marked"] == len(notes) - 1
    assert client.get("/notifications").json()["unread_count"] == 0
    assert client.get("/notifications", params={"role": "nobody"}).status_code == 422


def test_the_test_email_endpoint_reports_success_and_the_error(api, monkeypatch):
    client, _, sns = api
    ok = client.post("/admin/test-email").json()
    assert ok["ok"] is True and ok["status"] == "sent"
    sent = sns.calls[-1]
    assert sent["Subject"] == "ClaimShield: test email" and not RECORD_ID.search(sent["Message"])
    assert "CASE-" not in sent["Message"]
    sns.exc = RuntimeError("secret detail")
    bad = client.post("/admin/test-email").json()
    assert bad["ok"] is False and bad["status"] == "failed" and "RuntimeError" in bad["detail"]
    assert "secret detail" not in bad["detail"]
    monkeypatch.setenv("NOTIFY_EMAIL_ENABLED", "false")
    assert client.post("/admin/test-email").json()["status"] == "disabled"


def test_everything_is_written_to_the_audit_log(api):
    client, _app, _ = api
    kinds = {e["event_type"] for e in client.get("/audit", params={"limit": 1000}).json()}
    assert {"notification", "email_attempt", "pipeline_run"} <= kinds


# ------------------------------------------------------------ outbound drafts


def recipients(shared, case_id=RING):
    case = shared.case(case_id)
    provider = next(p for p in case.entity_ids if p.startswith("PRV-")
                    and _drafts(case, shared, "records_request", p))  # fmt: skip
    member = next(m for m in case.affected_members if _drafts(case, shared, "service_verification", m))
    return provider, member


def _drafts(case, shared, template, who):
    try:
        outbound.draft_for(case, shared.db_path, template, who)
    except outbound.OutboundError:
        return False
    return True


def test_a_draft_is_created_from_the_template_and_never_sent(api, shared):
    client, _app, sns = api
    provider, member = recipients(shared)
    calls = len(sns.calls)
    made = client.post(f"/cases/{RING}/outbound", json={
        "recipient_type": "provider", "recipient_id": provider, "template": "records_request",
        "created_by": "Asha Rao"})  # fmt: skip
    assert made.status_code == 201
    draft = made.json()
    assert draft["status"] == "draft" and draft["approved_by"] is None and draft["sent_at"] is None
    assert draft["body"].startswith("As part of a routine documentation review, please submit records")
    assert "Kindly respond within 15 days." in draft["body"] and "CLM-" in draft["body"]
    assert outbound.banned_words(draft["subject"], draft["body"]) == []
    verify = client.post(f"/cases/{RING}/outbound", json={
        "recipient_type": "member", "recipient_id": member, "template": "service_verification"}).json()
    assert verify["body"].startswith("Please confirm whether you received the following services")
    assert "Reply Yes or No for each." in verify["body"]
    assert len(sns.calls) == calls  # drafting sends nothing at all
    listed = client.get(f"/cases/{RING}/outbound").json()
    assert [m["status"] for m in listed] == ["draft", "draft"] and listed[0]["id"] > listed[1]["id"]


def test_bad_recipients_and_templates_are_rejected(api, shared):
    client, _, _ = api
    provider, member = recipients(shared)
    url = f"/cases/{RING}/outbound"
    assert client.post(url, json={"recipient_type": "member", "recipient_id": member,
                                  "template": "records_request"}).status_code == 422  # fmt: skip
    assert client.post(url, json={"recipient_type": "provider", "recipient_id": "PRV-NOPE",
                                  "template": "records_request"}).status_code == 422  # fmt: skip
    assert client.post(url, json={"recipient_type": "provider", "recipient_id": provider,
                                  "template": "anything"}).status_code == 422  # fmt: skip
    assert client.post("/cases/CASE-9999/outbound", json={
        "recipient_type": "provider", "recipient_id": provider, "template": "records_request"
    }).status_code == 404


@pytest.mark.parametrize("word", ["fraud", "suspicious", "investigation", "risk", "flagged", "SIU", "score",
                                  "Fraudulent", "Risks"])
def test_a_draft_with_a_banned_word_is_rejected(api, shared, word):
    client, _, _ = api
    provider, _ = recipients(shared)
    draft = client.post(f"/cases/{RING}/outbound", json={
        "recipient_type": "provider", "recipient_id": provider, "template": "records_request"}).json()
    edit = client.put(f"/outbound/{draft['id']}", json={
        "subject": draft["subject"], "body": f"{draft['body']} This relates to a {word} matter."})
    assert edit.status_code == 422 and word.lower().rstrip("s") in edit.json()["detail"].lower()
    assert client.get(f"/cases/{RING}/outbound").json()[0]["body"] == draft["body"]  # unchanged
    assert client.put(f"/outbound/{draft['id']}", json={"subject": "Records", "body": ""}).status_code == 422


def test_approval_needs_a_reason_and_only_marks_the_message_sent_simulated(api, shared):
    client, _app, sns = api
    provider, _ = recipients(shared)
    draft = client.post(f"/cases/{RING}/outbound", json={
        "recipient_type": "provider", "recipient_id": provider, "template": "records_request"}).json()
    url = f"/outbound/{draft['id']}/approve"
    assert client.post(url, json={"approved_by": "Asha Rao"}).status_code == 422
    assert client.post(url, json={"approved_by": "Asha Rao", "reason": "  "}).status_code == 422
    assert client.post(url, json={"approved_by": "", "reason": "Wording checked"}).status_code == 422
    assert client.get(f"/cases/{RING}/outbound").json()[0]["status"] == "draft"  # still not approved
    edited = client.put(f"/outbound/{draft['id']}", json={
        "subject": "Records for a routine review", "body": draft["body"] + " Thank you."}).json()
    assert edited["subject"] == "Records for a routine review"
    calls = len(sns.calls)
    done = client.post(url, json=GOOD).json()
    assert done["status"] == "sent_simulated" and done["approved_by"] == "Asha Rao" and done["sent_at"]
    assert len(sns.calls) == calls  # nothing real was sent
    assert client.post(url, json=GOOD).status_code == 409
    assert client.put(f"/outbound/{draft['id']}", json={
        "subject": "x", "body": "y"}).status_code == 409  # an approved message is final
    assert client.post("/outbound/999999/approve", json=GOOD).status_code == 404
    events = client.get("/audit", params={"case_id": RING, "limit": 1000}).json()
    by_type = {e["event_type"]: e for e in events if e["event_type"].startswith("outbound")}
    assert set(by_type) == {"outbound_create", "outbound_edit", "outbound_approve"}
    approve = by_type["outbound_approve"]
    assert approve["reviewer"] == "Asha Rao" and approve["reason"] == "Checked the wording"
    assert all("body" not in e["details"] for e in by_type.values())  # no message text in the log


def test_banned_words_are_matched_as_words(shared):
    assert outbound.banned_words("a scoreboard", "an siux tag", "mrisk") == []
    assert outbound.banned_words("Flagged claims", "the SIU team") == ["flagged", "siu"]
    for spec in outbound.TEMPLATES.values():  # the approved wording itself is clean
        assert outbound.banned_words(spec["subject"], spec["intro"]) == []


def test_the_new_audit_filter_returns_only_that_event_type(api):
    client, _, _ = api
    entries = client.get("/audit", params={"event_type": "notification", "limit": 1000}).json()
    assert entries and {e["event_type"] for e in entries} == {"notification"}


def test_the_store_enforces_its_allowed_values(parts):
    store, _ = parts
    with pytest.raises(Exception, match="CHECK"):
        store.add_notification(role="intern", type="x", severity="info", case_id=None, message="m")
    with pytest.raises(Exception, match="CHECK"):
        store.add_notification(role="siu", type="x", severity="info", case_id=None, message="m",
                               email_status="bounced")  # fmt: skip


def test_a_timeout_that_carries_an_empty_response_is_still_just_a_failed_email():
    class ReadTimeout(Exception):
        response = None  # botocore's timeout errors have the attribute but no value

    channel = SnsEmailNotifier(CONFIG, lambda: FakeSns(exc=ReadTimeout("slow")))
    assert channel.send(notifier.Message("s", "b")) == "failed" and channel.last_error == "ReadTimeout"


def test_one_failing_case_does_not_stop_the_alerts_for_the_others(shared, parts, monkeypatch):
    real = alerts._high_priority
    first = shared.cases[0].case_id

    def flaky(case, *args):
        if case.case_id == first:
            raise RuntimeError("boom")
        return real(case, *args)

    monkeypatch.setattr(alerts, "_high_priority", flaky)
    result = run(shared, parts, FakeSns())
    done = {n["case_id"] for n in result.created if n["type"] == "new_case"}
    assert done == {c.case_id for c in shared.cases}  # every case was announced
    assert first not in {e["case_id"] for e in result.emails}
    assert len(result.emails) == len(urgent_cases(shared) - {first})


def test_aws_keys_kept_in_dot_env_are_passed_to_the_client_without_being_logged(monkeypatch, caplog):
    seen = {}
    monkeypatch.setattr(notifier.boto3, "client", lambda *a, **k: seen.update(k) or FakeSns())
    for name, value in (("AWS_ACCESS_KEY_ID", "AKIAFAKE"), ("AWS_SECRET_ACCESS_KEY", "fake-secret")):
        monkeypatch.setenv(name, value)
    assert SnsEmailNotifier(CONFIG).send(notifier.Message("s", "b")) == "sent"
    assert seen["aws_access_key_id"] == "AKIAFAKE" and seen["aws_secret_access_key"] == "fake-secret"
    assert "AKIAFAKE" not in caplog.text and "fake-secret" not in caplog.text
    monkeypatch.delenv("AWS_SECRET_ACCESS_KEY")
    monkeypatch.setattr(notifier, "resolve_setting", lambda name, env: "AKIAFAKE" if name == "AWS_ACCESS_KEY_ID" else "")
    seen.clear()
    SnsEmailNotifier(CONFIG).send(notifier.Message("s", "b"))
    assert "aws_access_key_id" not in seen  # half a key pair is ignored; boto3's usual sources apply
