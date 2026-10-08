"""API additions for the review UI: citation keys, titles, evidence rows and priority overrides."""

import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api.main import create_app
from backend.audit import AuditLog
from backend.brief import generate

RING = "CASE-0001"
GOOD = {"action": "escalate_for_investigation", "reason": "Ring pattern looks consistent",
        "reviewer": "Asha Rao"}  # fmt: skip


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("m9") / "claimshield.db")


@pytest.fixture
def client(shared, tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr(generate, "load_env", lambda path=None: {})
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda path: shared)
    with TestClient(app) as test_client:
        yield test_client


def case_id_for(shared, primary):
    return next(c.case_id for c in shared.cases if c.primary_entity == primary)


def finding_key(client, case_id, detector):
    body = client.get(f"/cases/{case_id}").json()
    return next(f["key"] for f in body["findings"] if f["detector"] == detector)


# ------------------------------------------------------------ keys, titles, recommendation


def test_openapi_lists_the_new_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    assert set(paths["/cases/{case_id}/priority-override"]) == {"post"}
    assert set(paths["/cases/{case_id}/evidence/{key}"]) == {"get"}


def test_findings_carry_the_same_keys_as_the_brief(client, shared):
    body = client.get(f"/cases/{RING}").json()
    assert [f["key"] for f in body["findings"]] == ["E1", "E2"]
    assert [f["finding_id"] for f in body["findings"]] == [
        e.finding_id for e in shared.packs[RING].evidence
    ]
    brief = client.get(f"/cases/{RING}/brief").json()["brief"]
    assert "[E1]" in brief and "[E2]" in brief  # the keys the UI links to are the ones cited


def test_case_titles_are_readable_and_never_say_fraud(client, shared):
    queue = client.get("/queue", params={"capacity": 1000}).json()["scheduled"]
    titles = {i["case_id"]: i["title"] for i in queue}
    assert titles[RING] == "Referral network: RING-01 (5 linked entities)"
    assert titles[case_id_for(shared, "PRV-005")] == "Upcoding pattern: PRV-005"
    assert all(t and "fraud" not in t.lower() for t in titles.values())
    assert client.get(f"/cases/{RING}").json()["title"] == titles[RING]


def test_case_detail_carries_the_ai_recommendation(client):
    body = client.get(f"/cases/{RING}").json()
    action = body["recommended_action"]
    assert action["tier"] == "full-review" and "No claim should be denied" in action["text"]
    assert body["ai_priority"] == body["priority"] and body["override"] is None


# ------------------------------------------------------------ evidence rows


def test_evidence_rows_for_a_claim_specific_item(client):
    body = client.get(f"/cases/{RING}/evidence/E2").json()
    assert body["detector"] == "ring" and body["scope"] == "claim-specific"
    assert body["total_claims"] == 180 and body["shown"] == 180 and body["note"] is None
    dates = [c["service_date"] for c in body["claims"]]
    assert dates == sorted(dates) and body["claims"][0]["claim_id"].startswith("CLM-")
    assert set(body["claims"][0]) == {
        "claim_id", "service_date", "member_id", "provider_id", "facility_id",
        "referring_provider_id", "procedure_code", "code_level", "billed_amount", "claim_type",
    }  # fmt: skip
    assert client.get(f"/cases/{RING}/evidence/e2").json()["key"] == "E2"  # case-insensitive
    limited = client.get(f"/cases/{RING}/evidence/E2", params={"limit": 10}).json()
    assert limited["shown"] == 10 and limited["total_claims"] == 180
    assert "first 10 of 180" in limited["note"]


def test_provider_level_evidence_is_labelled_as_such(client):
    body = client.get(f"/cases/{RING}/evidence/E1").json()
    assert body["scope"] == "provider-level" and body["total_claims"] == 270
    assert body["shown"] == 200 and body["note"].startswith("Provider-level signal")


def test_evidence_rows_include_linked_stays_and_investigations(client, shared):
    phantom = case_id_for(shared, "PRV-007")
    findings = client.get(f"/cases/{phantom}").json()["findings"]
    assert sum(f["detector"] == "phantom" for f in findings) == 10  # one finding per claim
    key = finding_key(client, phantom, "phantom")
    body = client.get(f"/cases/{phantom}/evidence/{key}").json()
    assert body["total_claims"] == 1 and len(body["linked_records"]) == 1
    assert body["linked_records"][0]["kind"] == "inpatient stay"
    assert body["linked_records"][0]["id"].startswith("STAY-")
    repeat = case_id_for(shared, "PRV-010")
    rows = client.get(f"/cases/{repeat}/evidence/{finding_key(client, repeat, 'repeat_history')}").json()
    assert [r["kind"] for r in rows["linked_records"]] == ["prior investigation"]
    assert "confirmed" in rows["linked_records"][0]["description"]


def test_evidence_errors(client):
    assert client.get(f"/cases/{RING}/evidence/E9").status_code == 404
    assert client.get("/cases/CASE-9999/evidence/E1").status_code == 404
    assert client.get(f"/cases/{RING}/evidence/E1", params={"limit": 0}).status_code == 422
    assert client.get(f"/cases/{RING}/evidence/E1", params={"limit": 501}).status_code == 422


# ------------------------------------------------------------ priority override


def set_priority(client, case_id, priority, reason="Needs a closer look soon", reviewer="Asha Rao"):
    return client.post(
        f"/cases/{case_id}/priority-override",
        json={"priority": priority, "reason": reason, "reviewer": reviewer},
    )


def test_an_override_reorders_the_queue_but_keeps_the_ai_priority(client):
    before = client.get("/queue", params={"capacity": 1000}).json()["scheduled"]
    last = before[-1]
    response = set_priority(client, last["case_id"], 0.99)
    assert response.status_code == 201
    body = response.json()
    assert body["override_active"] and body["priority"] == 0.99
    assert body["ai_priority"] == last["ai_priority"]
    after = client.get("/queue", params={"capacity": 1000}).json()["scheduled"]
    assert after[0]["case_id"] == last["case_id"] and after[0]["priority"] == 0.99
    assert after[0]["ai_priority"] == last["ai_priority"]
    assert after[0]["override"]["reviewer"] == "Asha Rao"
    assert after[1]["case_id"] == RING and after[1]["override"] is None
    assert [i["rank"] for i in after] == list(range(1, 21))
    detail = client.get(f"/cases/{last['case_id']}").json()
    assert detail["rank"] == 1 and detail["priority"] == 0.99 and detail["override"]["reason"]
    assert detail["ai_priority"] == last["ai_priority"] and len(detail["overrides"]) == 1


def test_an_override_changes_what_gets_scheduled(client):
    last = client.get("/queue", params={"capacity": 1000}).json()["scheduled"][-1]
    set_priority(client, last["case_id"], 1.0)
    body = client.get("/queue", params={"capacity": 9}).json()
    # the pinned 4-hour case goes first, and the 9-hour ring behind it no longer fits
    assert [i["case_id"] for i in body["scheduled"]] == [last["case_id"]]
    assert body["scheduled_hours"] == 4.0


def test_an_override_can_also_lower_a_priority(client):
    set_priority(client, RING, 0.0, reason="Already under separate review")
    items = client.get("/queue", params={"capacity": 1000}).json()["scheduled"]
    assert items[-1]["case_id"] == RING and items[-1]["priority"] == 0.0


def test_clearing_an_override_restores_the_ai_order(client):
    set_priority(client, RING, 0.0)
    cleared = set_priority(client, RING, None, reason="Review finished, back to normal")
    assert cleared.status_code == 201 and cleared.json()["override_active"] is False
    assert cleared.json()["priority"] == cleared.json()["ai_priority"]
    top = client.get("/queue").json()["scheduled"][0]
    assert top["case_id"] == RING and top["override"] is None
    actions = [e["action"] for e in client.get("/audit", params={"case_id": RING, "event_type": "priority_override"}).json()]
    assert actions == ["clear_priority", "set_priority"]


@pytest.mark.parametrize(
    "body",
    [
        {"priority": 0.5, "reviewer": "Asha"},  # no reason
        {"priority": 0.5, "reason": "ok", "reviewer": "Asha"},  # too short
        {"priority": 0.5, "reason": "     ", "reviewer": "Asha"},
        {"priority": 0.5, "reason": "Needs a closer look"},  # no reviewer
        {"priority": 0.5, "reason": "Needs a closer look", "reviewer": ""},
        {"priority": 1.5, "reason": "Needs a closer look", "reviewer": "Asha"},
        {"priority": -0.1, "reason": "Needs a closer look", "reviewer": "Asha"},
        {"reason": "Needs a closer look", "reviewer": "Asha"},  # priority missing (null clears)
    ],
)
def test_an_override_needs_a_valid_priority_a_reason_and_a_reviewer(client, body):
    assert client.post(f"/cases/{RING}/priority-override", json=body).status_code == 422, body
    assert [e for e in client.get("/audit").json() if e["event_type"] == "priority_override"] == []
    assert client.get("/queue").json()["scheduled"][0]["override"] is None


def test_an_override_is_audited_with_both_priorities(client):
    set_priority(client, RING, 0.42, reason="Waiting on provider records", reviewer="Ben Cole")
    entry = client.get("/audit").json()[0]
    assert entry["event_type"] == "priority_override" and entry["action"] == "set_priority"
    assert entry["reviewer"] == "Ben Cole" and entry["reason"] == "Waiting on provider records"
    assert entry["details"]["priority"] == 0.42 and 0 < entry["details"]["ai_priority"] <= 1


def test_overrides_survive_a_restart(shared, tmp_path):
    def make():
        return create_app(
            data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda p: shared
        )

    with TestClient(make()) as first:
        set_priority(first, RING, 0.1)
    with TestClient(make()) as second:
        scheduled = second.get("/queue", params={"capacity": 1000}).json()["scheduled"]
        item = next(i for i in scheduled if i["case_id"] == RING)
        assert item["priority"] == 0.1 and item["override"]["priority"] == 0.1


def test_override_on_an_unknown_case_is_404(client):
    assert set_priority(client, "CASE-9999", 0.5).status_code == 404


def test_the_audit_file_location_can_come_from_the_environment(shared, tmp_path, monkeypatch):
    target = tmp_path / "elsewhere" / "env_audit.db"
    monkeypatch.setenv("CLAIMSHIELD_AUDIT_PATH", str(target))
    with TestClient(create_app(data_dir=tmp_path, runner=lambda p: shared)) as c:
        c.post(f"/cases/{RING}/decision", json=GOOD)
    assert target.exists() and AuditLog(target).list()[0]["event_type"] == "decision"
