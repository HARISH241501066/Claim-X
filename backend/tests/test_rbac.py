"""Role-based access: sign-in, the permission matrix, units, assignment and reports.

Every rule is checked against the API itself (not the screens). The demo seed has two units, so the
cross-unit cases are real: Unit South covers Chennai, Bengaluru, Hyderabad; Unit North the rest.
"""

import csv
import io
import json
import os
import re
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import jwt
import pypdf
import pytest
from fastapi.testclient import TestClient

from backend import pipeline
from backend.access import security
from backend.access.permissions import ALL_ACTIONS, allowed, allowed_unit
from backend.access.routing import route_cases
from backend.access.seed import seed_demo
from backend.access.store import User
from backend.api.main import create_app
from backend.reports import common, unit_report
from backend.tests.auth_helpers import PASSWORD, SECRET

REASON = "Matches the unit's current workload"
BANNED = ("fraud probability", "fraudster", "guilty")


@pytest.fixture(scope="module")
def shared(tmp_path_factory):
    return pipeline.run_all(tmp_path_factory.mktemp("m9c") / "claimx.db")


class World:
    """A running API with the two demo units, and helpers to call it as any seeded user."""

    def __init__(self, app, client):
        self.app, self.client, self.rt = app, client, app.state.runtime
        self.tokens: dict[str, str] = {}
        self.units = {u["name"]: u["id"] for u in self.rt.access.list_units()}

    def headers(self, username: str) -> dict:
        if username not in self.tokens:
            r = self.client.post("/auth/login", json={"username": username, "password": PASSWORD})
            assert r.status_code == 200, r.text
            self.tokens[username] = r.json()["access_token"]
        return {"Authorization": f"Bearer {self.tokens[username]}"}

    def get(self, who, url, **kw):
        return self.client.get(url, headers=self.headers(who), **kw)

    def post(self, who, url, body=None, **kw):
        return self.client.post(url, json=body, headers=self.headers(who), **kw)

    def put(self, who, url, body=None):
        return self.client.put(url, json=body, headers=self.headers(who))

    def uid(self, username: str) -> int:
        return self.rt.access.get_login(username)[0].id

    def cases_of(self, unit: str) -> list[str]:
        rows = self.rt.access.assignments(self.units[unit])
        return sorted(rows)

    def audit(self, **params):
        return self.get("admin", "/audit", params={"limit": 1000, **params}).json()

    def assign(self, case, investigator, lead="south_lead", reason=REASON):
        return self.post(lead, f"/cases/{case}/assign",
                         {"assignee_user_id": self.uid(investigator), "reason": reason})  # fmt: skip


@pytest.fixture
def world(shared, tmp_path):
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda p: shared,
                     seed=seed_demo)  # fmt: skip
    with TestClient(app) as client:
        yield World(app, client)


# ------------------------------------------------------------ sign-in


def test_login_returns_a_token_and_the_user(world):
    r = world.client.post("/auth/login", json={"username": "south_lead", "password": PASSWORD})
    body = r.json()
    assert r.status_code == 200 and body["token_type"] == "bearer" and body["expires_in"] == 8 * 3600
    assert body["user"] == {"id": world.uid("south_lead"), "username": "south_lead",
                            "display_name": "Kavya Menon", "role": "team_lead",
                            "unit_id": world.units["Unit South"], "unit_name": "Unit South",
                            "active": True}  # fmt: skip
    claims = jwt.decode(body["access_token"], SECRET, algorithms=["HS256"])
    assert claims["sub"] == str(world.uid("south_lead")) and claims["role"] == "team_lead"
    assert claims["unit_id"] == world.units["Unit South"]
    assert timedelta(hours=7, minutes=59) < datetime.fromtimestamp(claims["exp"], UTC) - datetime.now(UTC) <= timedelta(hours=8)
    me = world.get("south_lead", "/auth/me").json()
    assert me["display_name"] == "Kavya Menon" and "password" not in json.dumps(me)


def test_a_wrong_password_or_unknown_user_is_rejected_the_same_way(world):
    wrong = world.client.post("/auth/login", json={"username": "south_lead", "password": "nope-nope"})
    unknown = world.client.post("/auth/login", json={"username": "nobody", "password": PASSWORD})
    assert wrong.status_code == unknown.status_code == 401
    assert wrong.json() == unknown.json() == {"detail": "Wrong username or password."}
    failed = [e for e in world.audit(event_type="login_failed")]
    assert len(failed) == 2 and all("password" not in json.dumps(e) for e in failed)


PROTECTED = [
    ("get", "/overview"), ("get", "/queue"), ("get", "/cases/CASE-0001"), ("get", "/cases/CASE-0001/graph"),
    ("get", "/cases/CASE-0001/brief"), ("get", "/cases/CASE-0001/evidence/E1"), ("get", "/audit"),
    ("get", "/notifications"), ("post", "/notifications/read-all"), ("get", "/auth/me"),
    ("post", "/cases/CASE-0001/decision"), ("post", "/cases/CASE-0001/priority-override"),
    ("post", "/cases/CASE-0001/assign"), ("post", "/cases/CASE-0001/unassign"),
    ("post", "/cases/CASE-0001/outbound"), ("get", "/cases/CASE-0001/outbound"),
    ("get", "/cases/CASE-0001/report.pdf"), ("get", "/units/1/report.pdf"), ("get", "/units/1/report.csv"),
    ("get", "/units/1/workload"), ("get", "/units/1/members"), ("post", "/admin/rerun"),
    ("post", "/admin/prewarm-briefs"), ("post", "/admin/test-email"), ("get", "/admin/users"),
    ("get", "/admin/units"), ("get", "/admin/unrouted"), ("put", "/outbound/1"),
    ("post", "/outbound/1/approve"),
]  # fmt: skip


@pytest.mark.parametrize(("method", "path"), PROTECTED)
def test_every_endpoint_but_health_needs_a_token(world, method, path):
    assert getattr(world.client, method)(path).status_code == 401, path
    bad = {"Authorization": "Bearer not-a-token"}
    assert getattr(world.client, method)(path, headers=bad).status_code == 401, path


def test_health_stays_open(world):
    assert world.client.get("/health").status_code == 200


def test_a_tampered_expired_or_unsigned_token_is_refused(world):
    user = world.uid("south_lead")
    now = datetime.now(UTC)
    claims = {"sub": str(user), "role": "team_lead", "unit_id": 1, "iat": now}
    forged = jwt.encode({**claims, "exp": now + timedelta(hours=1)}, "x" * 40, algorithm="HS256")
    expired = jwt.encode({**claims, "exp": now - timedelta(seconds=5)}, SECRET, algorithm="HS256")
    no_exp = jwt.encode(claims, SECRET, algorithm="HS256")
    none_alg = jwt.encode({**claims, "exp": now + timedelta(hours=1)}, None, algorithm="none")
    for token in (forged, expired, no_exp, none_alg):
        r = world.client.get("/queue", headers={"Authorization": f"Bearer {token}"})
        assert r.status_code == 401, token[:20]


def test_a_token_cannot_claim_a_role_the_database_does_not_give(world):
    """The role and unit in the token are not trusted: the user is re-read on every request."""
    now = datetime.now(UTC)
    token = jwt.encode({"sub": str(world.uid("south_inv1")), "role": "admin", "unit_id": None,
                        "iat": now, "exp": now + timedelta(hours=1)}, SECRET, algorithm="HS256")  # fmt: skip
    r = world.client.post("/admin/rerun", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 403


def test_a_deactivated_user_loses_access_at_once(world):
    assert world.get("south_inv1", "/auth/me").status_code == 200
    assert world.post("admin", f"/admin/users/{world.uid('south_inv1')}/active", {"active": False}).status_code == 200
    assert world.get("south_inv1", "/auth/me").status_code == 401  # the old token no longer works
    assert world.client.post("/auth/login", json={"username": "south_inv1", "password": PASSWORD}).status_code == 401


def test_five_wrong_passwords_lock_the_username_for_a_while(world):
    for _ in range(5):
        assert world.client.post("/auth/login", json={"username": "north_inv1", "password": "wrong-one"}).status_code == 401
    locked = world.client.post("/auth/login", json={"username": "north_inv1", "password": PASSWORD})
    assert locked.status_code == 429 and int(locked.headers["Retry-After"]) > 0
    assert world.client.post("/auth/login", json={"username": "north_inv2", "password": PASSWORD}).status_code == 200


def test_signing_in_is_refused_when_no_secret_is_configured(world, monkeypatch):
    monkeypatch.delenv("JWT_SECRET")
    monkeypatch.setattr(security, "resolve_setting", lambda name, env=None: os.environ.get(name, ""))
    r = world.client.post("/auth/login", json={"username": "admin", "password": PASSWORD})
    assert r.status_code == 503 and "JWT_SECRET" in r.json()["detail"]
    monkeypatch.setenv("JWT_SECRET", "too-short")
    assert world.client.post("/auth/login", json={"username": "admin", "password": PASSWORD}).status_code == 503


def test_passwords_are_stored_only_as_bcrypt_hashes_and_no_secret_is_in_the_repo(world):
    con = sqlite3.connect(world.rt.access.path)
    try:
        hashes = [r[0] for r in con.execute("SELECT password_hash FROM users")]
    finally:
        con.close()
    assert len(hashes) == 7 and all(h.startswith("$2") and PASSWORD not in h for h in hashes)
    example = (Path(__file__).resolve().parents[2] / ".env.example").read_text(encoding="utf-8")
    for name in ("JWT_SECRET", "DEMO_PASSWORD"):
        assert re.search(rf"^{name}=\s*$", example, re.MULTILINE), f"{name} must be blank in .env.example"
    root = Path(__file__).resolve().parents[1]
    for path in [*root.glob("access/*.py"), *root.glob("api/*.py"), *root.glob("reports/*.py")]:
        text = path.read_text(encoding="utf-8")
        assert PASSWORD not in text and SECRET not in text, path.name


def test_no_users_are_created_without_a_demo_password(shared, tmp_path, monkeypatch):
    monkeypatch.delenv("DEMO_PASSWORD")
    from backend.access import seed

    monkeypatch.setattr(seed, "resolve_setting", lambda name, env=None: os.environ.get(name, ""))
    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "audit.db", runner=lambda p: shared)
    assert app.state.runtime.access.user_count() == 0


# ------------------------------------------------------------ routing and the permission matrix


def test_cases_are_routed_by_their_main_providers_city(world, shared):
    rows = world.rt.access.assignments()
    assert set(rows) == {c.case_id for c in shared.cases}
    con = sqlite3.connect(shared.db_path)
    try:
        city = dict(con.execute("SELECT provider_id, city FROM providers"))
    finally:
        con.close()
    south = {"Chennai", "Bengaluru", "Hyderabad"}
    from backend.access.routing import main_provider

    for case in shared.cases:
        expected = "Unit South" if city[main_provider(case, shared.db_path)] in south else "Unit North"
        assert rows[case.case_id]["unit_id"] == world.units[expected], case.case_id
        assert rows[case.case_id]["status"] == "unassigned" and rows[case.case_id]["assignee_user_id"] is None
    assert world.cases_of("Unit South") and world.cases_of("Unit North")


def test_a_case_whose_city_no_unit_covers_is_unrouted_and_only_the_admin_sees_it(shared, tmp_path):
    def south_only(store, password):
        unit = store.create_unit("Unit South", ["Chennai"])
        h = security.hash_password(password)
        store.create_user(username="admin", display_name="System Admin", role="admin", unit_id=None, password_hash=h)
        store.create_user(username="south_lead", display_name="Kavya Menon", role="team_lead",
                          unit_id=unit["id"], password_hash=h)  # fmt: skip

    app = create_app(data_dir=tmp_path, audit_path=tmp_path / "a.db", runner=lambda p: shared, seed=south_only)
    with TestClient(app) as c:
        w = World.__new__(World)
        w.client, w.rt, w.tokens = c, app.state.runtime, {}
        unrouted = w.get("admin", "/admin/unrouted").json()
        assert unrouted and set(unrouted) < {x.case_id for x in shared.cases}
        lead_ids = {i["case_id"] for i in w.get("south_lead", "/queue", params={"capacity": 1000}).json()["scheduled"]}
        assert lead_ids.isdisjoint(unrouted)
        assert w.get("south_lead", f"/cases/{unrouted[0]}").status_code == 403
        assert w.get("admin", f"/cases/{unrouted[0]}").status_code == 200
        # extending the unit's region routes the waiting cases
        every = ["Chennai", "Bengaluru", "Hyderabad", "Delhi", "Mumbai", "Kolkata"]
        assert w.put("admin", f"/admin/units/{w.rt.access.list_units()[0]['id']}", {"region": every}).status_code == 200
        assert w.get("admin", "/admin/unrouted").json() == []


def test_assignments_survive_a_pipeline_rerun(world):
    case = world.cases_of("Unit South")[0]
    assert world.assign(case, "south_inv1").status_code == 200
    assert world.post("admin", "/admin/rerun").status_code == 200
    row = world.rt.access.get_assignment(case)
    assert row["assignee_user_id"] == world.uid("south_inv1") and row["status"] == "assigned"
    assert set(world.rt.access.assignments()) == {c.case_id for c in world.rt.state.cases}
    route_cases(world.rt.state.cases, world.rt.state.db_path, world.rt.access)  # routing again changes nothing
    assert world.rt.access.get_assignment(case)["assignee_user_id"] == world.uid("south_inv1")


def test_the_permission_matrix_in_one_table():
    admin = User(1, "a", "A", "admin", None)
    lead = User(2, "l", "L", "team_lead", 10)
    inv = User(3, "i", "I", "investigator", 10)
    mine = {"unit_id": 10, "assignee_user_id": 3, "status": "assigned"}
    other_inv = {"unit_id": 10, "assignee_user_id": 9, "status": "assigned"}
    other_unit = {"unit_id": 20, "assignee_user_id": 8, "status": "assigned"}
    none = {"unit_id": 10, "assignee_user_id": None, "status": "unassigned"}
    unrouted = {"unit_id": None, "assignee_user_id": None, "status": "unassigned"}
    table = {  # action: (admin, lead, investigator) for a case assigned to this investigator
        "view": (True, True, True), "assign": (False, True, False), "decide": (False, True, True),
        "outbound": (False, True, True), "report_case": (True, True, True),
    }  # fmt: skip
    for action, expected in table.items():
        assert tuple(allowed(u, action, mine) for u in (admin, lead, inv)) == expected, action
    for action in ("view", "decide", "outbound", "report_case"):
        assert not allowed(inv, action, other_inv) and not allowed(inv, action, none)
        assert not allowed(lead, action, other_unit) and not allowed(inv, action, other_unit)
        assert not allowed(lead, action, unrouted) and not allowed(lead, action, None)
    assert allowed(lead, "view", none) and allowed(lead, "decide", other_inv)
    assert allowed(admin, "view", unrouted) and not allowed(admin, "decide", unrouted)
    assert allowed_unit(admin, "report_unit", 20) and allowed_unit(lead, "report_unit", 10)
    assert not allowed_unit(lead, "report_unit", 20) and not allowed_unit(inv, "report_unit", 10)
    assert not allowed(admin, "made-up", mine) and "report_unit" in ALL_ACTIONS


def test_an_investigator_cannot_open_an_unassigned_or_other_unit_case(world):
    south = world.cases_of("Unit South")
    north = world.cases_of("Unit North")
    world.assign(south[0], "south_inv1")
    for case in (south[1], north[0]):  # unassigned in their unit, and another unit's
        for path in ("", "/graph", "/brief", "/evidence/E1", "/outbound", "/report.pdf"):
            r = world.get("south_inv1", f"/cases/{case}{path}")
            assert r.status_code == 403, (case, path)
            assert "access" in r.json()["detail"].lower()
    assert world.get("south_inv1", f"/cases/{south[0]}").status_code == 200  # theirs
    assert world.get("south_inv2", f"/cases/{south[0]}").status_code == 403  # a colleague's


def test_an_investigator_cannot_assign_or_download_a_unit_report(world):
    case = world.cases_of("Unit South")[0]
    body = {"assignee_user_id": world.uid("south_inv2"), "reason": REASON}
    assert world.post("south_inv1", f"/cases/{case}/assign", body).status_code == 403
    assert world.post("south_inv1", f"/cases/{case}/unassign", {"reason": REASON}).status_code == 403
    unit = world.units["Unit South"]
    for suffix in ("report.pdf", "report.csv", "workload", "members"):
        assert world.get("south_inv1", f"/units/{unit}/{suffix}").status_code == 403, suffix
    assert world.rt.access.get_assignment(case)["assignee_user_id"] is None


def test_a_team_lead_cannot_touch_another_units_case_or_investigators(world):
    south = world.cases_of("Unit South")
    north = world.cases_of("Unit North")
    # a case of another unit
    assert world.assign(north[0], "south_inv1").status_code == 403
    assert world.get("south_lead", f"/cases/{north[0]}").status_code == 403
    assert world.post("south_lead", f"/cases/{north[0]}/decision", {"action": "monitor", "reason": REASON}).status_code == 403
    # an investigator of another unit, on their own case
    other = world.assign(south[0], "north_inv1")
    assert other.status_code == 403
    assert world.rt.access.get_assignment(south[0])["assignee_user_id"] is None
    # another unit's reports
    assert world.get("south_lead", f"/units/{world.units['Unit North']}/report.pdf").status_code == 403
    assert world.get("south_lead", f"/units/{world.units['Unit North']}/workload").status_code == 403
    assert world.get("south_lead", f"/cases/{north[0]}/report.pdf").status_code == 403


def test_a_team_lead_assigns_within_their_unit_and_the_assignee_is_told(world):
    case = world.cases_of("Unit South")[0]
    r = world.assign(case, "south_inv1")
    assert r.status_code == 200
    assert r.json() | {"assigned_at": None} == {"case_id": case, "unit_id": world.units["Unit South"],
        "assignee_user_id": world.uid("south_inv1"), "assignee_name": "Arjun Nair",
        "status": "assigned", "assigned_at": None}  # fmt: skip
    notes = world.get("south_inv1", "/notifications").json()["notifications"]
    mine = [n for n in notes if n["type"] == "assignment"]
    assert [n["message"] for n in mine] == [f"Case {case} assigned to you by Kavya Menon."]
    assert mine[0]["case_id"] == case and not mine[0]["read"]
    assert not [n for n in world.get("south_inv2", "/notifications").json()["notifications"] if n["type"] == "assignment"]
    entry = world.audit(event_type="assignment")[0]
    assert entry["case_id"] == case and entry["action"] == "assign" and entry["reason"] == REASON
    assert entry["reviewer"] == "Kavya Menon" and entry["details"]["assignee_user_id"] == world.uid("south_inv1")
    queue = world.get("south_inv1", "/queue").json()
    assert [i["case_id"] for i in queue["scheduled"] + queue["backlog"]] == [case]  # "My Cases"
    detail = world.get("south_inv1", f"/cases/{case}").json()["access"]
    assert detail["assignee_name"] == "Arjun Nair" and detail["can_decide"] and not detail["can_assign"]


def test_reassigning_tells_both_people_and_unassigning_tells_the_one_who_lost_it(world):
    case = world.cases_of("Unit South")[1]
    world.assign(case, "south_inv1")
    again = world.assign(case, "south_inv1")
    assert again.status_code == 409
    assert world.assign(case, "south_inv2", reason="Arjun is at capacity").status_code == 200
    old = [n["message"] for n in world.get("south_inv1", "/notifications").json()["notifications"] if n["type"] == "assignment"]
    new = [n["message"] for n in world.get("south_inv2", "/notifications").json()["notifications"] if n["type"] == "assignment"]
    assert any("reassigned from you to Divya Reddy by Kavya Menon" in m for m in old)
    assert new == [f"Case {case} assigned to you by Kavya Menon."]
    assert world.audit(event_type="assignment")[0]["action"] == "reassign"
    assert world.get("south_inv1", f"/cases/{case}").status_code == 403  # the old assignee lost access
    r = world.post("south_lead", f"/cases/{case}/unassign", {"reason": "Taking it back for review"})
    assert r.status_code == 200 and r.json()["status"] == "unassigned" and r.json()["assignee_user_id"] is None
    assert any("taken off your list" in n["message"] for n in world.get("south_inv2", "/notifications").json()["notifications"])
    assert world.audit(event_type="assignment")[0]["action"] == "unassign"
    assert world.post("south_lead", f"/cases/{case}/unassign", {"reason": "Again please"}).status_code == 409


def test_assignment_needs_a_reason_and_a_real_investigator(world):
    case = world.cases_of("Unit South")[0]
    assert world.assign(case, "south_inv1", reason="ok").status_code == 422
    assert world.post("south_lead", f"/cases/{case}/assign", {"assignee_user_id": world.uid("south_inv1")}).status_code == 422
    assert world.assign(case, "south_lead").status_code == 422  # a team lead is not an investigator
    assert world.post("south_lead", f"/cases/{case}/assign", {"assignee_user_id": 9999, "reason": REASON}).status_code == 404
    world.post("admin", f"/admin/users/{world.uid('south_inv2')}/active", {"active": False})
    inactive = world.post("south_lead", f"/cases/{case}/assign", {"assignee_user_id": world.uid("south_inv2"), "reason": REASON})
    assert inactive.status_code == 422
    assert world.assign(case, "south_inv1").status_code == 200
    assert world.post("admin", f"/cases/{case}/assign", {"assignee_user_id": world.uid("south_inv1"), "reason": REASON}).status_code == 403


def test_an_investigator_decides_only_on_assigned_cases_and_the_user_is_recorded(world):
    south = world.cases_of("Unit South")
    world.assign(south[0], "south_inv1")
    body = {"action": "escalate_for_investigation", "reason": "Pattern needs a closer look"}
    assert world.post("south_inv1", f"/cases/{south[1]}/decision", body).status_code == 403
    assert world.post("south_inv2", f"/cases/{south[0]}/decision", body).status_code == 403
    assert world.post("south_inv1", f"/cases/{south[0]}/priority-override", {"priority": 0.9, "reason": REASON}).status_code == 201
    r = world.post("south_inv1", f"/cases/{south[0]}/decision", {**body, "reviewer": "Somebody Else"})
    assert r.status_code == 201 and r.json()["reviewer"] == "Arjun Nair"  # not what was typed
    entry = world.audit(event_type="decision")[0]
    assert entry["reviewer"] == "Arjun Nair" and entry["details"] == {"user_id": world.uid("south_inv1"), "role": "investigator"}
    assert world.rt.access.get_assignment(south[0])["status"] == "in_review"
    assert world.post("south_inv1", f"/cases/{south[0]}/decision", {"action": "monitor", "reason": ""}).status_code == 422
    assert world.post("south_inv1", f"/cases/{south[0]}/decision", {"action": "dismiss", "reason": "Benign coding pattern"}).status_code == 201
    assert world.rt.access.get_assignment(south[0])["status"] == "closed"
    assert world.assign(south[0], "south_inv2").status_code == 409  # closed: reopen it first
    assert world.post("south_lead", f"/cases/{south[0]}/decision", {"action": "monitor", "reason": "Reopened for follow up"}).status_code == 201
    assert world.rt.access.get_assignment(south[0])["status"] == "in_review"


def test_a_team_lead_decides_on_any_case_of_their_unit_and_the_admin_on_none(world):
    case = world.cases_of("Unit South")[2]
    body = {"action": "monitor", "reason": "Watching this provider"}
    assert world.post("south_lead", f"/cases/{case}/decision", body).status_code == 201
    assert world.post("admin", f"/cases/{case}/decision", body).status_code == 403
    assert world.post("admin", f"/cases/{case}/priority-override", {"priority": 0.5, "reason": REASON}).status_code == 403
    assert world.post("admin", f"/cases/{case}/outbound", {"recipient_type": "provider", "recipient_id": "PRV-001",
                      "template": "records_request"}).status_code == 403  # fmt: skip
    assert world.get("admin", f"/cases/{case}").status_code == 200  # but the admin can look


def test_outbound_drafts_follow_the_same_rules(world, shared):
    case_id = world.cases_of("Unit South")[0]
    case = shared.case(case_id)
    provider = next(p for p in case.entity_ids if p.startswith("PRV-"))
    draft = {"recipient_type": "provider", "recipient_id": provider, "template": "records_request"}
    assert world.post("south_inv1", f"/cases/{case_id}/outbound", draft).status_code == 403  # not theirs yet
    world.assign(case_id, "south_inv1")
    made = world.post("south_inv1", f"/cases/{case_id}/outbound", draft)
    if made.status_code == 201:  # the case's provider may have no claims to ask about
        assert made.json()["created_by"] == "Arjun Nair"
        out_id = made.json()["id"]
        assert world.post("south_inv2", f"/outbound/{out_id}/approve", {"reason": REASON}).status_code == 403
        done = world.post("south_inv1", f"/outbound/{out_id}/approve", {"reason": REASON})
        assert done.status_code == 200 and done.json()["approved_by"] == "Arjun Nair"
        assert world.audit(event_type="outbound_approve")[0]["reviewer"] == "Arjun Nair"
    assert world.get("south_inv2", f"/cases/{case_id}/outbound").status_code == 403


def test_the_admin_runs_system_tasks_and_nobody_else_can(world):
    for who in ("south_lead", "south_inv1", "north_lead"):
        assert world.post(who, "/admin/rerun").status_code == 403
        assert world.post(who, "/admin/prewarm-briefs").status_code == 403
        assert world.post(who, "/admin/test-email").status_code == 403
        assert world.get(who, "/admin/users").status_code == 403
        assert world.get(who, "/admin/unrouted").status_code == 403
    assert world.post("admin", "/admin/test-email").status_code == 200
    assert world.post("admin", "/admin/prewarm-briefs?top=1").status_code == 200
    assert world.post("admin", "/admin/rerun").status_code == 200


def test_denied_attempts_are_written_to_the_audit_log(world):
    case = world.cases_of("Unit North")[0]
    world.get("south_inv1", f"/cases/{case}")
    world.post("south_lead", f"/cases/{case}/decision", {"action": "monitor", "reason": REASON})
    world.post("south_inv1", "/admin/rerun")
    denied = world.audit(event_type="access_denied")
    assert [(e["action"], e["reviewer"]) for e in denied] == [
        ("admin", "Arjun Nair"), ("decide", "Kavya Menon"), ("view", "Arjun Nair")]  # fmt: skip
    first = denied[2]
    assert first["case_id"] == case and first["details"]["role"] == "investigator"
    assert first["details"]["path"] == f"/cases/{case}" and first["details"]["method"] == "GET"
    assert denied[0]["case_id"] is None and denied[0]["details"]["path"] == "/admin/rerun"
    assert len(world.audit(event_type="login")) == 3  # the three users who signed in


# ------------------------------------------------------------ what each role sees


def test_each_role_sees_only_its_cases_in_the_queue_and_overview(world, shared):
    south, north = world.cases_of("Unit South"), world.cases_of("Unit North")

    def ids(who, **params):
        q = world.get(who, "/queue", params={"capacity": 1000, "include_decided": True, **params}).json()
        return sorted(i["case_id"] for i in q["scheduled"] + q["backlog"])

    assert ids("admin") == sorted(c.case_id for c in shared.cases)
    assert ids("south_lead") == south and ids("north_lead") == north
    assert ids("south_inv1") == [] and ids("south_inv1", view="unit") == south
    world.assign(south[0], "south_inv1")
    assert ids("south_inv1") == [south[0]] and ids("south_inv2") == []
    rows = world.get("south_inv1", "/queue", params={"view": "unit", "capacity": 1000}).json()
    flags = {i["case_id"]: i["can_open"] for i in rows["scheduled"] + rows["backlog"]}
    assert flags[south[0]] is True and all(not v for k, v in flags.items() if k != south[0])
    assert next(i for i in rows["scheduled"] + rows["backlog"] if i["case_id"] == south[0])["assignee_name"] == "Arjun Nair"
    assert ids("south_lead")  # and the lead still sees the case, now with its assignee
    lead_rows = world.get("south_lead", "/queue", params={"capacity": 1000}).json()
    assert {i["case_id"]: i["assignee_name"] for i in lead_rows["scheduled"] + lead_rows["backlog"]}[south[0]] == "Arjun Nair"
    overview = {w: world.get(w, "/overview").json() for w in ("admin", "south_lead", "south_inv1", "south_inv2")}
    assert overview["admin"]["cases"] == len(shared.cases) and overview["admin"]["scope"] == "all"
    assert overview["south_lead"]["cases"] == len(south) and overview["south_lead"]["scope"] == "unit"
    assert overview["south_inv1"]["cases"] == 1 and overview["south_inv1"]["scope"] == "mine"
    assert overview["south_inv2"]["cases"] == 0 and overview["south_inv2"]["dollars_at_risk"] == 0
    detail = world.get("south_lead", f"/cases/{south[0]}").json()["access"]
    assert detail["can_assign"] and detail["can_decide"] and detail["can_report"]
    assert world.get("admin", f"/cases/{south[0]}").json()["access"]["can_decide"] is False


def test_notifications_are_limited_to_what_each_user_may_see_and_read_state_is_personal(world):
    south, north = world.cases_of("Unit South"), world.cases_of("Unit North")

    def notes(who):
        return world.get(who, "/notifications").json()

    lead = notes("south_lead")["notifications"]
    assert lead and {n["case_id"] for n in lead if n["case_id"]} <= set(south)
    assert {n["case_id"] for n in notes("north_lead")["notifications"] if n["case_id"]} <= set(north)
    assert notes("south_inv1")["notifications"] == []  # nothing is theirs yet
    admin_ids = {n["case_id"] for n in notes("admin")["notifications"] if n["case_id"]}
    assert set(south) <= admin_ids and set(north) <= admin_ids
    world.assign(south[0], "south_inv1")
    inv = notes("south_inv1")["notifications"]
    assert {n["case_id"] for n in inv} == {south[0]} and len(inv) >= 2  # its alerts and the assignment
    first = lead[0]
    assert world.post("south_lead", f"/notifications/{first['id']}/read").json()["read"] is True
    assert notes("south_lead")["unread_count"] == len(lead) - 1
    assert next(n for n in notes("admin")["notifications"] if n["id"] == first["id"])["read"] is False
    assert world.post("south_inv1", f"/notifications/{first['id']}/read").status_code == 404
    marked = world.post("south_inv1", "/notifications/read-all").json()["marked"]
    assert marked == len(inv) and notes("south_inv1")["unread_count"] == 0
    assert notes("south_lead")["unread_count"] == len(lead) - 1  # the lead's own state is untouched


def test_the_audit_log_endpoint_is_scoped(world):
    south, north = world.cases_of("Unit South"), world.cases_of("Unit North")
    world.post("south_lead", f"/cases/{south[0]}/decision", {"action": "monitor", "reason": REASON})
    world.post("north_lead", f"/cases/{north[0]}/decision", {"action": "monitor", "reason": REASON})
    seen = world.get("south_lead", "/audit", params={"limit": 1000}).json()
    assert seen and {e["case_id"] for e in seen} <= set(south)
    assert world.get("south_lead", "/audit", params={"case_id": north[0]}).json() == []
    assert {e["case_id"] for e in world.get("admin", "/audit", params={"limit": 1000}).json() if e["event_type"] == "decision"} == {south[0], north[0]}
    assert world.get("south_inv1", "/audit", params={"limit": 1000}).json() == []


# ------------------------------------------------------------ workload and users


def test_unit_workload_counts_cases_high_priority_and_effort(world):
    south = world.cases_of("Unit South")
    for case in south[:3]:
        world.assign(case, "south_inv1")
    world.assign(south[3], "south_inv2")
    world.post("south_inv1", f"/cases/{south[0]}/decision", {"action": "monitor", "reason": "Needs more data"})
    world.post("south_inv1", f"/cases/{south[1]}/decision", {"action": "dismiss", "reason": "Benign coding pattern"})
    body = world.get("south_lead", f"/units/{world.units['Unit South']}/workload").json()
    people = {p["name"]: p for p in body["people"]}
    a, b = people["Arjun Nair"], people["Divya Reddy"]
    assert (a["assigned"], a["in_review"], a["closed"], a["decisions"]) == (2, 1, 1, 2)
    assert (b["assigned"], b["in_review"], b["closed"]) == (1, 0, 0)
    assert a["capacity_hours"] == b["capacity_hours"] == unit_report.INVESTIGATOR_HOURS
    assert a["effort_hours"] > 0 and 0 <= a["open_high_priority"] <= a["assigned"]
    assert body["unassigned"] == len(south) - 4 and body["unit_name"] == "Unit South"
    members = world.get("south_lead", f"/units/{world.units['Unit South']}/members").json()
    assert sorted(m["display_name"] for m in members) == ["Arjun Nair", "Divya Reddy"]
    assert all("password" not in json.dumps(m) for m in members)


def test_the_admin_manages_users_and_units(world):
    south = world.units["Unit South"]
    new = {"username": "south_inv3", "display_name": "Latha Iyer", "role": "investigator",
           "unit_id": south, "password": "a-fresh-password"}  # fmt: skip
    r = world.post("admin", "/admin/users", new)
    assert r.status_code == 201 and "password" not in json.dumps(r.json())
    assert world.client.post("/auth/login", json={"username": "south_inv3", "password": "a-fresh-password"}).status_code == 200
    assert world.post("admin", "/admin/users", new).status_code == 409  # username taken
    lead_again = {**new, "username": "south_lead2", "role": "team_lead"}
    assert world.post("admin", "/admin/users", lead_again).status_code == 409  # one lead per unit
    assert world.post("admin", "/admin/users", {**new, "username": "x1", "unit_id": None}).status_code == 422
    assert world.post("admin", "/admin/users", {**new, "username": "Bad Name!"}).status_code == 422
    assert world.post("admin", "/admin/users", {**new, "username": "short-pw", "password": "short"}).status_code == 422
    assert world.post("admin", f"/admin/users/{world.uid('admin')}/active", {"active": False}).status_code == 409
    assert len(world.get("admin", "/admin/users").json()) == 8
    units = {u["name"]: u for u in world.get("admin", "/admin/units").json()}
    assert units["Unit South"]["region"] == ["Chennai", "Bengaluru", "Hyderabad"]
    kinds = {e["event_type"] for e in world.audit()}
    assert {"user_create", "login"} <= kinds
    assert world.put("south_lead", f"/admin/units/{south}", {"region": ["Delhi"]}).status_code == 403


# ------------------------------------------------------------ reports


def pdf_pages(data: bytes) -> list[str]:
    return [" ".join(page.extract_text().split()) for page in pypdf.PdfReader(io.BytesIO(data)).pages]


FOOTER = " ".join(common.FOOTER.split())


def test_the_case_report_is_a_pdf_with_the_case_id_the_footer_and_the_sections(world):
    case = world.cases_of("Unit South")[0]
    world.assign(case, "south_inv1")
    world.post("south_inv1", f"/cases/{case}/decision", {"action": "monitor", "reason": "Needs more data"})
    r = world.get("south_inv1", f"/cases/{case}/report.pdf")
    assert r.status_code == 200 and r.headers["content-type"] == "application/pdf"
    assert r.content.startswith(b"%PDF") and f"{case}-report.pdf" in r.headers["content-disposition"]
    pages = pdf_pages(r.content)
    assert pages and all(FOOTER in page for page in pages)  # the footer is on every page
    text = " ".join(pages)
    assert case in text
    for heading in ("Priority", "Evidence", "Timeline", "Network summary", "Investigation risk", "Brief",
                    "Decisions and audit history"):  # fmt: skip
        assert heading in text, heading
    assert "Unit South" in text and "Arjun Nair" in text and "Source: Template" in text
    assert "monitor" in text and "FND-" in text and "CLM-" in text  # the evidence and claim IDs
    assert not [p for p in BANNED if p in text.lower()]
    log = world.audit(event_type="report_download")[0]
    assert (log["case_id"], log["action"], log["reviewer"]) == (case, "case_pdf", "Arjun Nair")
    assert log["details"]["user_id"] == world.uid("south_inv1")


def test_who_may_download_a_case_report(world):
    case = world.cases_of("Unit South")[0]
    world.assign(case, "south_inv1")
    got = {who: world.get(who, f"/cases/{case}/report.pdf").status_code
           for who in ("admin", "south_lead", "south_inv1", "south_inv2", "north_lead", "north_inv1")}  # fmt: skip
    assert got == {"admin": 200, "south_lead": 200, "south_inv1": 200, "south_inv2": 403,
                   "north_lead": 403, "north_inv1": 403}  # fmt: skip
    assert len(world.audit(event_type="report_download")) == 3  # only the downloads that happened
    assert len(world.audit(event_type="access_denied")) == 3


def test_the_unit_report_pdf_and_csv(world):
    south = world.cases_of("Unit South")
    world.assign(south[0], "south_inv1")
    world.post("south_inv1", f"/cases/{south[0]}/decision", {"action": "monitor", "reason": "Needs more data"})
    unit = world.units["Unit South"]
    pdf = world.get("south_lead", f"/units/{unit}/report.pdf")
    assert pdf.status_code == 200 and pdf.content.startswith(b"%PDF")
    pages = pdf_pages(pdf.content)
    assert all(FOOTER in page for page in pages)
    text = " ".join(pages)
    for heading in ("Unit report: Unit South", "Cases by status and priority", "Assignments per investigator",
                    "Decisions made", "Overdue cases"):  # fmt: skip
        assert heading in text, heading
    assert "Arjun Nair" in text and south[0] in text and not [p for p in BANNED if p in text.lower()]
    csv_response = world.get("south_lead", f"/units/{unit}/report.csv")
    assert csv_response.status_code == 200 and csv_response.headers["content-type"].startswith("text/csv")
    assert "attachment" in csv_response.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(csv_response.text)))
    assert list(rows[0]) == unit_report.CSV_COLUMNS == [
        "case_id", "title", "rank", "priority", "priority_band", "status", "assignee", "latest_decision",
        "decision_date", "decided_by", "amount_at_risk_rs", "pending_days", "overdue"]  # fmt: skip
    assert sorted(r["case_id"] for r in rows) == south
    mine = next(r for r in rows if r["case_id"] == south[0])
    assert mine["status"] == "in_review" and mine["assignee"] == "Arjun Nair"
    assert mine["latest_decision"] == "monitor" and mine["decided_by"] == "Arjun Nair"
    assert mine["decision_date"] == datetime.now(UTC).date().isoformat() and mine["overdue"] == "false"
    assert {r["priority_band"] for r in rows} <= {"high", "medium", "low"}
    assert [int(r["rank"]) for r in rows] == sorted(int(r["rank"]) for r in rows)
    logged = [(e["action"], e["reviewer"]) for e in world.audit(event_type="report_download")]
    assert logged == [("unit_csv", "Kavya Menon"), ("unit_pdf", "Kavya Menon")]
    assert world.get("admin", f"/units/{world.units['Unit North']}/report.csv").status_code == 200
    assert world.get("north_lead", f"/units/{unit}/report.csv").status_code == 403
    assert world.get("south_inv1", f"/units/{unit}/report.pdf").status_code == 403


def test_pending_cases_become_overdue_after_three_days(world):
    south = world.cases_of("Unit South")
    world.post("south_lead", f"/cases/{south[0]}/decision", {"action": "monitor", "reason": REASON})
    later = datetime.now(UTC) + timedelta(days=4)
    data = unit_report.unit_data(world.rt, world.units["Unit South"], now=later)
    by_case = {r["case_id"]: r for r in data["rows"]}
    assert by_case[south[0]]["overdue"] is False and by_case[south[0]]["pending_days"] == ""  # decided
    others = [r for c, r in by_case.items() if c != south[0]]
    assert others and all(r["overdue"] and r["pending_days"] >= 3 for r in others)
    assert len(data["overdue"]) == len(others) and unit_report.OVERDUE_DAYS == 3
    pdf = unit_report.unit_pdf(data, "Test")
    assert "Days pending" in " ".join(pdf_pages(pdf))


def test_report_text_never_contains_the_banned_phrases():
    dirty = "A Fraud Probability of 90%, a fraudster, guilty."
    assert not [p for p in BANNED if p in common.clean(dirty).lower()]
    assert common.clean("a→b") == "a?b"  # a glyph the standard font lacks is replaced, not drawn as a box
    assert unit_report._csv_safe("=1+1") == "'=1+1" and unit_report._csv_safe("plain") == "plain"
    assert unit_report.priority_band(0.6) == "high" and unit_report.priority_band(0.35) == "medium"
    assert unit_report.priority_band(0.349) == "low"
