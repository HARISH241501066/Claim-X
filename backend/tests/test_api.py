import json
import logging
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.api import main as api_main
from backend.api import views
from backend.api.main import create_app
from backend.api.schemas import STATUS_BY_ACTION
from backend.audit import AuditLog
from backend.brief import generate
from backend.brief.generate import Brief
from backend.cases import ranking
from backend.pipeline import PipelineError
from backend.tests.auth_helpers import login

RING = "CASE-0001"
GOOD = {"action": "escalate_for_investigation", "reason": "Ring pattern looks consistent",
        "reviewer": "Asha Rao"}  # fmt: skip
PATHS = {
    "/health", "/overview", "/queue", "/cases/{case_id}", "/cases/{case_id}/graph",
    "/cases/{case_id}/brief", "/cases/{case_id}/decision", "/audit", "/admin/rerun",
}  # fmt: skip


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    """One real pipeline run, reused by every test that does not need its own."""
    folder = tmp_path_factory.mktemp("m8")
    return pipeline.run_all(folder / "claimshield.db")


@pytest.fixture
def client(shared, tmp_path, monkeypatch):
    monkeypatch.delenv("LLM_API_KEY", raising=False)
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.delenv("LLM_MODEL", raising=False)
    monkeypatch.setattr(generate, "load_env", lambda path=None: {})  # never read a real .env
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda path: shared)
    with TestClient(app) as test_client:
        login(test_client)
        yield test_client


def case_id_for(shared, primary):
    return next(c.case_id for c in shared.cases if c.primary_entity == primary)


# ------------------------------------------------------------ docs and health


def test_docs_and_openapi_list_every_endpoint(client):
    assert client.get("/docs").status_code == 200
    spec = client.get("/openapi.json").json()
    assert PATHS <= set(spec["paths"])
    assert set(spec["paths"]["/cases/{case_id}/decision"]) == {"post"}
    assert set(spec["paths"]["/admin/rerun"]) == {"post"}
    for path in PATHS:
        for operation in spec["paths"][path].values():
            assert "200" in operation["responses"] or "201" in operation["responses"], path


def test_health_reports_status_and_stage_timings(client):
    body = client.get("/health").json()
    assert body["status"] == "ok" and body["ready"] is True
    assert [s["name"] for s in body["stages"]] == pipeline.STAGE_NAMES
    assert all(s["status"] == "ok" and s["seconds"] >= 0 for s in body["stages"])
    assert body["total_seconds"] > 0 and body["started_at"] and body["finished_at"]


def test_health_works_before_the_pipeline_is_ready_and_other_endpoints_say_503(tmp_path):
    app = create_app(run_on_startup=False, data_dir=tmp_path, audit_path=tmp_path / "audit.db")
    with TestClient(app) as c:
        assert c.get("/health").json() == {
            "status": "ok", "ready": False, "started_at": None, "finished_at": None,
            "total_seconds": None, "stages": [],
        }  # fmt: skip
        login(c)
        for path in ("/overview", "/queue", f"/cases/{RING}", f"/cases/{RING}/graph", "/audit"):
            assert c.get(path).status_code == 503, path
        assert c.post(f"/cases/{RING}/decision", json=GOOD).status_code == 503


def test_a_failed_startup_reports_error_and_a_later_rerun_recovers(shared, tmp_path):
    calls = {"n": 0}

    def flaky(path):
        calls["n"] += 1
        if calls["n"] == 1:
            raise PipelineError("stage 'data' failed: RuntimeError")
        return shared

    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=flaky)
    with TestClient(app) as c:
        login(c, "admin")
        assert c.get("/health").json()["status"] == "error"
        assert c.get("/queue").status_code == 503
        assert c.post("/admin/rerun").status_code == 200
        assert c.get("/health").json()["ready"] is True and c.get("/queue").status_code == 200


# ------------------------------------------------------------ overview


def test_overview_numbers(client, shared):
    body = client.get("/overview").json()
    assert body["claims"] == 5000 and body["cases"] == 20 == len(shared.cases)
    assert body["findings"] == len(shared.findings) == sum(body["findings_per_rule"].values())
    assert body["dollars_at_risk"] == shared.dollars_at_risk > 0
    assert {"duplicate", "unbundling", "phantom", "upcoding", "ring", "anomaly"} <= set(
        body["findings_per_rule"]
    )
    assert body["cases_awaiting_review"] == 20 and body["cases_decided"] == 0
    assert body["cases_scheduled"] + body["cases_backlog"] == 20
    assert body["scheduled_hours"] <= body["team_hours"] == 40.0


def test_overview_tracks_decisions(client):
    client.post(f"/cases/{RING}/decision", json=GOOD)
    body = client.get("/overview").json()
    assert body["cases_decided"] == 1 and body["cases_awaiting_review"] == 19


# ------------------------------------------------------------ queue


def test_queue_default_ranking_and_schedule(client, shared):
    body = client.get("/queue").json()
    items = body["scheduled"] + body["backlog"]
    assert len(items) == 20 and items[0]["case_id"] == RING and items[0]["case_type"] == "ring"
    assert [i["rank"] for i in items] == list(range(1, 21))
    assert body["capacity_hours"] == 40.0 and body["scheduled_hours"] <= 40.0
    assert body["scheduled_hours"] == sum(i["effort_hours"] for i in body["scheduled"])
    assert max(i["rank"] for i in body["scheduled"]) < min(i["rank"] for i in body["backlog"])
    expected = ranking.rank_cases(shared.cases).case_id.tolist()
    assert [i["case_id"] for i in items] == expected  # same ordering as the M5 ranking
    first = items[0]
    assert first["status"] == "Awaiting human review" and first["summary"]
    assert all(0 <= first["factors"][k] <= 1 for k in first["factors"])
    assert "fraud" not in json.dumps(body).lower()


def test_queue_capacity(client):
    none = client.get("/queue", params={"capacity": 0}).json()
    assert none["scheduled"] == [] and len(none["backlog"]) == 20
    everything = client.get("/queue", params={"capacity": 1000}).json()
    assert len(everything["scheduled"]) == 20 and everything["backlog"] == []
    nine = client.get("/queue", params={"capacity": 9}).json()  # exactly the ring's 9 hours
    assert [i["case_id"] for i in nine["scheduled"]] == [RING] and nine["scheduled_hours"] == 9.0


@pytest.mark.parametrize(
    "weights",
    ["risk:0.2,dollars:0.2,impact:0.2,severity:0.2,evidence:0.2",
     "risk=0.2, dollars=0.2, impact=0.2, severity=0.2, evidence=0.2",
     '{"risk": 0.2, "dollars": 0.2, "impact": 0.2, "severity": 0.2, "evidence": 0.2}'],
)  # fmt: skip
def test_queue_accepts_weights_in_three_formats(client, weights):
    body = client.get("/queue", params={"weights": weights}).json()
    assert body["weights"] == pytest.approx({k: 0.2 for k in views.WEIGHT_KEYS})
    assert len(body["scheduled"]) + len(body["backlog"]) == 20


def test_weights_reorder_the_queue(client, shared):
    impact_only = "risk:0,dollars:0,impact:1,severity:0,evidence:0"
    body = client.get("/queue", params={"weights": impact_only}).json()
    top = body["scheduled"][0]
    assert top["case_id"] != RING and top["n_members"] == max(len(c.affected_members) for c in shared.cases)
    default = client.get("/queue").json()
    assert default["scheduled"][0]["case_id"] == RING  # the default weights are untouched


@pytest.mark.parametrize(
    "params",
    [
        {"weights": "risk:0.5,dollars:0.5"},  # fills the rest with defaults, so it sums past 1
        {"weights": "risk:0.9,dollars:0.9,impact:0,severity:0,evidence:0"},
        {"weights": "colour:1"},
        {"weights": "risk"},
        {"weights": "risk:abc"},
        {"weights": "{not json"},
        {"capacity": -1},
        {"capacity": 5000},
        {"capacity": "lots"},
    ],
)
def test_queue_rejects_bad_parameters(client, params):
    response = client.get("/queue", params=params)
    assert response.status_code == 422, params
    assert response.json()["detail"]


def test_decided_cases_leave_the_queue_unless_asked(client):
    client.post(f"/cases/{RING}/decision", json=GOOD)
    body = client.get("/queue").json()
    items = body["scheduled"] + body["backlog"]
    assert len(items) == 19 and all(i["case_id"] != RING for i in items)
    assert body["decided_excluded"] == 1
    full = client.get("/queue", params={"include_decided": True}).json()
    ring = next(i for i in full["scheduled"] + full["backlog"] if i["case_id"] == RING)
    assert ring["status"] == "Escalated for investigation" and full["decided_excluded"] == 0


def test_queue_is_fast(client):
    client.get("/queue")  # warm up
    times = []
    for i in range(30):
        started = time.perf_counter()
        response = client.get("/queue", params={"capacity": 20 + i, "weights": "risk:0.4,dollars:0.15,impact:0.15,severity:0.15,evidence:0.15"})
        times.append(time.perf_counter() - started)
        assert response.status_code == 200
    assert max(times) < 0.3, max(times)
    assert sorted(times)[15] < 0.1  # the typical call is far quicker


# ------------------------------------------------------------ case detail


def test_case_detail_for_the_ring(client):
    body = client.get(f"/cases/{RING}").json()
    assert body["case_type"] == "ring" and body["rank"] == 1 and body["queue"] == "scheduled"
    assert body["status"] == "Awaiting human review" and body["decisions"] == []
    assert set(body["entity_ids"]) == {"PRV-A01", "FAC-B01", "FAC-C01", "OWN-001", "OWN-002"}
    assert {f["detector"] for f in body["findings"]} == {"anomaly", "ring"}
    for f in body["findings"]:
        assert f["reason"] and f["evidence_ids"] and f["finding_id"].startswith("FND-")
    assert len(body["timeline"]) == 6 and body["timeline"][0]["evidence_keys"]
    assert body["prediction"]["available"] and body["prediction"]["horizon_days"] == 30
    assert body["confidence"]["level"] == "Medium" and body["limitations"]
    assert len(body["affected_members"]) == 15 and body["flagged_amount"] == 345703


def test_case_detail_shows_the_repeat_offenders_escalated_band(client, shared):
    body = client.get(f"/cases/{case_id_for(shared, 'PRV-010')}").json()
    assert body["prediction"]["risk_band"] == "High" and body["prediction"]["band_source"] == "escalated"
    assert any(t["kind"] == "prior investigation" for t in body["timeline"])


def test_unknown_case_is_404_everywhere(client):
    for path in ("/cases/CASE-9999", "/cases/CASE-9999/graph", "/cases/CASE-9999/brief"):
        assert client.get(path).status_code == 404, path
    assert client.post("/cases/CASE-9999/decision", json=GOOD).status_code == 404


# ------------------------------------------------------------ graph


def test_ring_graph_flags_the_suspicious_entities_and_links(client):
    body = client.get(f"/cases/{RING}/graph").json()
    nodes = {n["id"]: n for n in body["nodes"]}
    ring = {"PRV-A01", "FAC-B01", "FAC-C01", "OWN-001", "OWN-002"}
    assert all(nodes[e]["suspicious"] for e in ring)
    members = [n for n in body["nodes"] if n["type"] == "member"]
    assert body["member_count"] == 19 and not body["members_collapsed"] and len(members) == 19
    assert sum(n["suspicious"] for n in members) == 15  # the 15 members on the flagged claims
    assert all(not n["suspicious"] for n in body["nodes"] if n["id"] not in ring and n["type"] != "member")
    ids = set(nodes)
    assert all(link["source"] in ids and link["target"] in ids for link in body["links"])
    links = {(link["source"], link["target"], link["type"]): link for link in body["links"]}
    assert links[("PRV-A01", "FAC-B01", "referred_to")]["suspicious"]
    assert links[("OWN-001", "OWN-002", "related_to")]["suspicious"]
    assert links[("FAC-B01", "OWN-001", "owned_by")]["suspicious"]
    assert {n["label"] for n in body["nodes"] if n["id"] == "PRV-A01"} == {"PRV-A01 (General Medicine)"}


def test_large_member_sets_collapse_into_one_node(client, shared):
    body = client.get(f"/cases/{case_id_for(shared, 'PRV-005')}/graph").json()
    group = [n for n in body["nodes"] if n["type"] == "member_group"]
    assert body["members_collapsed"] and len(group) == 1 and group[0]["count"] == body["member_count"] > 20
    assert group[0]["suspicious"] and not any(n["type"] == "member" for n in body["nodes"])
    provider = next(n for n in body["nodes"] if n["id"] == "PRV-005")
    assert provider["suspicious"]


def test_every_case_graph_is_well_formed(client, shared):
    for case in shared.cases:
        body = client.get(f"/cases/{case.case_id}/graph").json()
        ids = {n["id"] for n in body["nodes"]}
        assert case.primary_entity in ids or case.case_type == "ring", case.case_id
        assert all(link["source"] in ids and link["target"] in ids for link in body["links"])
        assert all(isinstance(n["suspicious"], bool) for n in body["nodes"])


# ------------------------------------------------------------ brief


def test_brief_uses_the_template_without_a_key_and_caches(client):
    first = client.get(f"/cases/{RING}/brief", params={"horizon": 30}).json()
    assert first["source"] == "template" and first["cached"] is False and first["horizon_days"] == 30
    assert first["fallback_reason"] and "LLM_PROVIDER is none" in first["fallback_reason"]
    assert first["provider"] is None and first["provider_label"] == "Template" and first["masked"] is False
    for heading in ("## Summary", "## Evidence", "## Timeline", "## Network context",
                    "## Confidence", "## Limitations", "## Recommended human-review action"):  # fmt: skip
        assert heading in first["brief"]
    assert first["brief"].rstrip().endswith("Final decision rests with the assigned investigator.")
    second = client.get(f"/cases/{RING}/brief").json()
    assert second["cached"] is True and second["brief"] == first["brief"]


def test_brief_horizon_is_validated_and_passed_through(client):
    assert client.get(f"/cases/{RING}/brief", params={"horizon": 0}).status_code == 422
    assert client.get(f"/cases/{RING}/brief", params={"horizon": 400}).status_code == 422
    sixty = client.get(f"/cases/{RING}/brief", params={"horizon": 60}).json()
    assert sixty["horizon_days"] == 60 and "Insufficient data" not in sixty["brief"]  # 60 days is modelled
    other = client.get(f"/cases/{RING}/brief", params={"horizon": 120}).json()
    assert other["horizon_days"] == 120 and "Insufficient data" in other["brief"]  # 30, 60 and 90 only


def test_brief_reports_when_the_llm_wrote_it(client, monkeypatch):
    monkeypatch.setattr(
        api_main, "generate_brief",
        lambda case_id, horizon, db_path, **kw: Brief(
            case_id, "TEXT\n", "llm", None, "claude-opus-5-5", "anthropic", True
        ),
    )  # fmt: skip
    body = client.get(f"/cases/{RING}/brief").json()
    assert body["source"] == "llm" and body["model"] == "claude-opus-5-5" and body["fallback_reason"] is None
    assert body["provider"] == "anthropic" and body["provider_label"] == "Claude" and body["masked"] is True


def test_an_invalid_key_still_gives_a_template_brief(client, monkeypatch):
    monkeypatch.setenv("LLM_PROVIDER", "anthropic")
    monkeypatch.setenv("LLM_API_KEY", "sk-ant-invalid")
    monkeypatch.setattr(
        generate.anthropic, "Anthropic",
        lambda **kw: type("C", (), {"messages": type("M", (), {"create": staticmethod(_raise_401)})()})(),
    )  # fmt: skip
    body = client.get(f"/cases/{RING}/brief").json()
    assert body["source"] == "template" and "LLM call failed" in body["fallback_reason"]
    assert "sk-ant-invalid" not in json.dumps(body)


def _raise_401(**kwargs):
    raise RuntimeError("rejected")


def test_brief_generation_is_logged_once(client):
    client.get(f"/cases/{RING}/brief")
    client.get(f"/cases/{RING}/brief")
    events = [e for e in client.get("/audit").json() if e["event_type"] == "brief"]
    assert len(events) == 1 and events[0]["details"]["source"] == "template"


# ------------------------------------------------------------ decisions and audit


def test_a_decision_is_recorded_and_changes_the_status(client):
    response = client.post(f"/cases/{RING}/decision", json=GOOD)
    assert response.status_code == 201
    body = response.json()
    assert body["action"] == GOOD["action"] and body["reviewer"] == "Kavya Menon"  # the signed-in user
    assert body["case_status"] == "Escalated for investigation" and body["decided_at"].endswith("Z")
    detail = client.get(f"/cases/{RING}").json()
    assert detail["status"] == "Escalated for investigation"
    assert [d["reviewer"] for d in detail["decisions"]] == ["Kavya Menon"]
    assert detail["decisions"][0]["reason"] == GOOD["reason"]


@pytest.mark.parametrize(
    "body",
    [
        {"action": "monitor", "reviewer": "Asha"},  # no reason
        {"action": "monitor", "reason": "", "reviewer": "Asha"},
        {"action": "monitor", "reason": "     ", "reviewer": "Asha"},
        {"action": "monitor", "reason": "ok", "reviewer": "Asha"},  # too short
        {"reason": "Looks routine", "reviewer": "Asha"},  # no action
        {"action": "deny_claim", "reason": "Looks routine", "reviewer": "Asha"},
        {"action": "block_payment", "reason": "Looks routine", "reviewer": "Asha"},
    ],
)
def test_a_decision_needs_an_action_and_a_reason(client, body):
    assert client.post(f"/cases/{RING}/decision", json=body).status_code == 422, body
    assert client.get(f"/cases/{RING}").json()["status"] == "Awaiting human review"
    assert [e for e in client.get("/audit").json() if e["event_type"] == "decision"] == []


def test_no_action_can_deny_a_claim_or_block_a_payment(client):
    spec = client.get("/openapi.json").json()
    actions = spec["components"]["schemas"]["DecisionIn"]["properties"]["action"]["enum"]
    assert sorted(actions) == sorted(STATUS_BY_ACTION)
    assert not any(word in a for a in actions for word in ("deny", "block", "reject", "pay"))


def test_the_latest_decision_wins_and_all_are_kept(client):
    client.post(f"/cases/{RING}/decision", json={**GOOD, "action": "monitor", "reason": "Watch for now"})
    client.post(f"/cases/{RING}/decision", json=GOOD)
    detail = client.get(f"/cases/{RING}").json()
    assert detail["status"] == "Escalated for investigation"
    assert [d["action"] for d in detail["decisions"]] == ["escalate_for_investigation", "monitor"]


def test_audit_is_latest_first_filterable_and_limited(client, shared):
    other = case_id_for(shared, "PRV-005")
    client.post(f"/cases/{RING}/decision", json=GOOD)
    client.post(f"/cases/{other}/decision", json={**GOOD, "action": "dismiss", "reason": "Benign coding"})
    login(client, "admin")  # the full log, including entries that belong to no case
    entries = client.get("/audit", params={"limit": 1000}).json()
    ids = [e["audit_id"] for e in entries]
    assert ids == sorted(ids, reverse=True)
    assert [e["event_type"] for e in entries if e["event_type"] == "decision"] == ["decision", "decision"]
    newest = next(e for e in entries if e["event_type"] == "decision")
    assert newest["case_id"] == other and newest["reason"] == "Benign coding"
    assert entries[-1]["event_type"] == "pipeline_run" and entries[-1]["details"]["trigger"] == "startup"
    assert {e["case_id"] for e in client.get("/audit", params={"case_id": RING}).json()} == {RING}
    assert len(client.get("/audit", params={"event_type": "decision"}).json()) == 2
    assert len(client.get("/audit", params={"limit": 1}).json()) == 1
    assert client.get("/audit", params={"limit": 0}).status_code == 422


def test_the_audit_log_is_append_only(client, tmp_path):
    client.post(f"/cases/{RING}/decision", json=GOOD)
    con = sqlite3.connect(tmp_path / "audit.db")
    try:
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("UPDATE audit_log SET reason = 'changed'")
        with pytest.raises(sqlite3.DatabaseError, match="append-only"):
            con.execute("DELETE FROM audit_log")
    finally:
        con.close()
    assert AuditLog(tmp_path / "audit.db").list()[0]["reason"] == GOOD["reason"]


def test_decisions_survive_a_restart(shared, tmp_path):
    def make():
        return create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda p: shared)

    with TestClient(make()) as first:
        login(first)
        first.post(f"/cases/{RING}/decision", json={**GOOD, "action": "dismiss", "reason": "Cleared"})
    with TestClient(make()) as second:
        login(second, "admin")
        assert second.get(f"/cases/{RING}").json()["status"] == "Dismissed by reviewer"
        assert second.get("/overview").json()["cases_decided"] == 1


# ------------------------------------------------------------ rerun, failures, CORS


def test_rerun_swaps_database_files_and_keeps_the_audit(tmp_path):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db")
    with TestClient(app) as c:
        rt = app.state.runtime
        assert rt.active == 0 and (tmp_path / "claimshield.db").exists()
        login(c)
        c.post(f"/cases/{RING}/decision", json=GOOD)
        c.get(f"/cases/{RING}/brief")
        first_run = rt.state.finished_at
        login(c, "admin")
        response = c.post("/admin/rerun")
        assert response.status_code == 200 and response.json()["status"] == "ok"
        assert rt.active == 1 and (tmp_path / "claimshield.alt.db").exists()
        assert rt.state.finished_at >= first_run  # a fresh run replaced the served state
        assert rt.briefs == {}  # cached briefs are dropped
        assert c.get(f"/cases/{RING}").json()["status"] == "Escalated for investigation"
        runs = [e for e in c.get("/audit").json() if e["event_type"] == "pipeline_run"]
        assert [r["details"]["trigger"] for r in runs] == ["rerun", "startup"]
        assert c.post("/admin/rerun").status_code == 200 and rt.active == 0  # and back again


def test_a_rerun_already_in_progress_gives_409(client):
    runtime = client.app.state.runtime
    login(client, "admin")
    assert runtime.rerun_lock.acquire(blocking=False)
    try:
        assert client.post("/admin/rerun").status_code == 409
    finally:
        runtime.rerun_lock.release()


def test_a_failed_rerun_keeps_the_previous_state(shared, tmp_path):
    calls = {"n": 0}

    def runner(path):
        calls["n"] += 1
        if calls["n"] > 1:
            raise PipelineError("stage 'data' failed: RuntimeError")
        return shared

    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=runner)
    with TestClient(app) as c:
        login(c, "admin")
        response = c.post("/admin/rerun")
        assert response.status_code == 500 and "previous state kept" in response.json()["detail"]
        assert c.get("/queue").status_code == 200 and app.state.runtime.active == 0
        assert not app.state.runtime.rerun_lock.locked()


def test_a_failing_stage_is_skipped_and_reported_as_degraded(tmp_path, monkeypatch, caplog):
    def boom(*args, **kwargs):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(pipeline.risk_model, "run_all", boom)
    with caplog.at_level(logging.ERROR, logger="claimshield.pipeline"):
        state = pipeline.run_all(tmp_path / "x.db")
    assert state.status == "degraded"
    failed = [t for t in state.timings if t.status == "failed"]
    assert [(t.name, t.error) for t in failed] == [("prediction", "RuntimeError")]
    assert len(state.cases) == 20 and state.ranked is not None  # the rest still ran
    assert all(c.investigation_risk is None for c in state.cases)  # shown as missing, not guessed
    assert "stage prediction failed" in caplog.text
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda p: state)
    with TestClient(app) as c:
        login(c, "admin")
        health = c.get("/health").json()
        assert health["status"] == "degraded"
        assert c.get(f"/cases/{RING}").json()["prediction"]["available"] is False


def test_a_failing_data_stage_is_fatal(tmp_path, monkeypatch):
    monkeypatch.setattr(pipeline.generator, "generate", lambda *a, **k: (_ for _ in ()).throw(OSError("disk")))
    with pytest.raises(PipelineError, match="data"):
        pipeline.run_all(tmp_path / "x.db")


def test_pipeline_logs_timings_and_writes_no_ground_truth(shared, tmp_path_factory, caplog):
    assert [t.name for t in shared.timings] == pipeline.STAGE_NAMES
    assert shared.total_seconds >= sum(t.seconds for t in shared.timings) - 0.5
    assert not list(shared.db_path.parent.glob("*.csv"))  # the test-only ground truth is not made
    folder = tmp_path_factory.mktemp("log")
    with caplog.at_level(logging.INFO, logger="claimshield.pipeline"):
        pipeline.run_all(folder / "y.db")
    assert all(f"stage {name}" in caplog.text for name in pipeline.STAGE_NAMES)


def test_cors_allows_only_the_vite_origin(client):
    ask = {"Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "content-type"}
    ok = client.options(f"/cases/{RING}/decision", headers={"Origin": "http://localhost:5173", **ask})
    assert ok.status_code == 200 and ok.headers["access-control-allow-origin"] == "http://localhost:5173"
    assert "POST" in ok.headers["access-control-allow-methods"]
    other = client.get("/health", headers={"Origin": "http://evil.example"})
    assert "access-control-allow-origin" not in other.headers
    same = client.get("/health", headers={"Origin": "http://127.0.0.1:5173"})
    assert same.headers["access-control-allow-origin"] == "http://127.0.0.1:5173"
