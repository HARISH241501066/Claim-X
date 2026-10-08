"""Sign-in, case assignment, workload, report downloads and user/unit administration.

Every permission is decided in backend/access/permissions.py and enforced here through the
dependencies in api/deps.py, so the UI only reflects what the API already enforces. Each login,
assignment, refused attempt and download is written to the audit log; passwords and tokens never are.
"""

import re
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from pydantic import BaseModel, Field, StringConstraints

from backend.access.routing import route_cases
from backend.access.security import (
    MIN_PASSWORD_LENGTH,
    LoginThrottle,
    NotConfiguredError,
    create_token,
    hash_password,
    verify_password,
)  # fmt: skip
from backend.access.store import DuplicateError, User
from backend.api import scope, views
from backend.api.deps import (
    AdminDep,
    UserDep,
    authorize_unit,
    case_permission,
    deny,
    get_runtime,
    unit_permission,
)  # fmt: skip
from backend.cases import ranking
from backend.reports import unit_report
from backend.reports.case_report import build_case_report

Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]
PDF = "application/pdf"
USERNAME = re.compile(r"^[a-z0-9][a-z0-9_.-]{2,39}$")


class LoginIn(BaseModel):
    username: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=64)]
    password: Annotated[str, StringConstraints(min_length=1, max_length=200)]


class UserOut(BaseModel):
    id: int
    username: str
    display_name: str
    role: Literal["admin", "team_lead", "investigator"]
    unit_id: int | None = None
    unit_name: str | None = None
    active: bool = True


class TokenOut(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int
    user: UserOut


class AssignIn(BaseModel):
    assignee_user_id: int
    reason: Reason


class UnassignIn(BaseModel):
    reason: Reason


class AssignmentOut(BaseModel):
    case_id: str
    unit_id: int | None
    assignee_user_id: int | None
    assignee_name: str | None
    status: str
    assigned_at: str | None


class WorkloadPerson(BaseModel):
    user_id: int
    name: str
    assigned: int
    in_review: int
    closed: int
    decisions: int
    open_high_priority: int
    effort_hours: float
    capacity_hours: float


class WorkloadOut(BaseModel):
    unit_id: int
    unit_name: str
    unassigned: int
    overdue: int
    people: list[WorkloadPerson]


class UserCreate(BaseModel):
    username: str
    display_name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]
    role: Literal["admin", "team_lead", "investigator"]
    unit_id: int | None = None
    password: Annotated[str, StringConstraints(min_length=MIN_PASSWORD_LENGTH, max_length=72)]


class ActiveIn(BaseModel):
    active: bool


class UnitOut(BaseModel):
    id: int
    name: str
    region: list[str]


class UnitUpdate(BaseModel):
    region: Annotated[
        list[Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=40)]],
        Field(max_length=30),
    ]


def build_router(make_brief) -> APIRouter:
    router = APIRouter()
    throttle = LoginThrottle()
    RuntimeDep = Annotated[object, Depends(get_runtime)]

    def user_out(rt, user: User) -> UserOut:
        unit = rt.access.get_unit(user.unit_id) if user.unit_id else None
        return UserOut(id=user.id, username=user.username, display_name=user.display_name,
                       role=user.role, unit_id=user.unit_id, unit_name=unit["name"] if unit else None,
                       active=user.active)  # fmt: skip

    # ------------------------------------------------------------ sign-in

    @router.post("/auth/login", response_model=TokenOut)
    def login(body: LoginIn, request: Request, rt: RuntimeDep):
        """Exchange a username and password for an 8-hour token."""
        name = body.username.lower()
        wait = throttle.locked_for(name)
        if wait:
            rt.audit.append("login_failed", details={"username": name[:50], "reason": "locked"})
            raise HTTPException(429, f"Too many wrong passwords. Try again in {wait} seconds.",
                                headers={"Retry-After": str(wait)})  # fmt: skip
        found = rt.access.get_login(name)
        user, hashed = found if found else (None, None)
        ok = verify_password(body.password, hashed)  # runs a bcrypt check even for an unknown user
        if not ok or user is None or not user.active:
            throttle.fail(name)
            rt.audit.append("login_failed", details={"username": name[:50], "reason": "bad credentials"})
            raise HTTPException(401, "Wrong username or password.")
        try:
            token, expires = create_token(user)
        except NotConfiguredError as exc:
            raise HTTPException(503, str(exc)) from exc
        throttle.clear(name)
        rt.audit.append("login", reviewer=user.display_name,
                        details={"user_id": user.id, "role": user.role})  # fmt: skip
        return TokenOut(access_token=token, expires_in=expires, user=user_out(rt, user))

    @router.get("/auth/me", response_model=UserOut)
    def me(rt: RuntimeDep, user: UserDep):
        return user_out(rt, user)

    # ------------------------------------------------------------ assignment

    def assignment_out(rt, case_id: str) -> AssignmentOut:
        row = rt.access.get_assignment(case_id)
        assignee = rt.access.get_user(row["assignee_user_id"]) if row["assignee_user_id"] else None
        return AssignmentOut(case_id=case_id, unit_id=row["unit_id"],
                             assignee_user_id=row["assignee_user_id"],
                             assignee_name=assignee.display_name if assignee else None,
                             status=row["status"], assigned_at=row["assigned_at"])  # fmt: skip

    def tell(rt, user_id: int, case_id: str, message: str) -> None:
        note = rt.notify.add_notification(role="siu", type="assignment", severity="info",
                                          case_id=case_id, message=message, recipient_user_id=user_id)  # fmt: skip
        rt.audit.append("notification", case_id=case_id,
                        details={"id": note["id"], "type": "assignment", "severity": "info",
                                 "recipient_user_id": user_id})  # fmt: skip

    @router.post("/cases/{case_id}/assign", response_model=AssignmentOut)
    def assign(case_id: str, body: AssignIn, request: Request, rt: RuntimeDep,
               lead: Annotated[User, Depends(case_permission("assign"))]):  # fmt: skip
        """Give a case to an active investigator of your own unit (or hand it to someone else)."""
        row = rt.access.get_assignment(case_id)
        assignee = rt.access.get_user(body.assignee_user_id)
        if assignee is None:
            raise HTTPException(404, f"Unknown user {body.assignee_user_id}")
        if assignee.unit_id != lead.unit_id:
            deny(rt, lead, "assign", request, case_id)
        if assignee.role != "investigator" or not assignee.active:
            raise HTTPException(422, "Cases can only be assigned to an active investigator.")
        if row["status"] == "closed":
            raise HTTPException(409, "This case is closed. Record a new decision to reopen it first.")
        previous = row["assignee_user_id"]
        if previous == assignee.id:
            raise HTTPException(409, f"This case is already assigned to {assignee.display_name}.")
        rt.access.assign(case_id, assignee.id, lead.id, body.reason)
        rt.audit.append(
            "assignment", case_id=case_id, action="reassign" if previous else "assign",
            reason=body.reason, reviewer=lead.display_name,
            details={"assignee_user_id": assignee.id, "previous_assignee_id": previous,
                     "unit_id": row["unit_id"], "assigned_by": lead.id},
        )  # fmt: skip
        tell(rt, assignee.id, case_id, f"Case {case_id} assigned to you by {lead.display_name}.")
        if previous:
            old = rt.access.get_user(previous)
            tell(rt, previous, case_id,
                 f"Case {case_id} was reassigned from you to {assignee.display_name} by {lead.display_name}."
                 if old else f"Case {case_id} was reassigned by {lead.display_name}.")  # fmt: skip
        return assignment_out(rt, case_id)

    @router.post("/cases/{case_id}/unassign", response_model=AssignmentOut)
    def unassign(case_id: str, body: UnassignIn, rt: RuntimeDep,
                 lead: Annotated[User, Depends(case_permission("assign"))]):  # fmt: skip
        row = rt.access.get_assignment(case_id)
        previous = row["assignee_user_id"]
        if not previous:
            raise HTTPException(409, "This case is not assigned to anyone.")
        rt.access.unassign(case_id, lead.id, body.reason)
        rt.audit.append("assignment", case_id=case_id, action="unassign", reason=body.reason,
                        reviewer=lead.display_name,
                        details={"previous_assignee_id": previous, "unit_id": row["unit_id"],
                                 "assigned_by": lead.id})  # fmt: skip
        tell(rt, previous, case_id, f"Case {case_id} was taken off your list by {lead.display_name}.")
        return assignment_out(rt, case_id)

    @router.get("/units/{unit_id}/members", response_model=list[UserOut])
    def members(unit_id: int, rt: RuntimeDep, user: UserDep, request: Request):
        """Active investigators of a unit: who a case can be assigned to."""
        authorize_unit(rt, user, "workload", unit_id, request)
        return [user_out(rt, u) for u in rt.access.list_users(unit_id) if u.role == "investigator" and u.active]

    @router.get("/units/{unit_id}/workload", response_model=WorkloadOut)
    def workload(unit_id: int, rt: RuntimeDep, _: Annotated[User, Depends(unit_permission("workload"))]):
        """Per investigator: open cases, open high-priority cases, effort against capacity."""
        data = unit_report.unit_data(rt, unit_id)
        return WorkloadOut(
            unit_id=unit_id, unit_name=data["unit"]["name"],
            unassigned=sum(1 for r in data["rows"] if r["status"] == "unassigned"),
            overdue=len(data["overdue"]), people=data["people"],
        )  # fmt: skip

    # ------------------------------------------------------------ reports

    def log_download(rt, user: User, kind: str, case_id: str | None = None, unit_id: int | None = None) -> None:
        rt.audit.append("report_download", case_id=case_id, action=kind, reviewer=user.display_name,
                        details={"user_id": user.id, "role": user.role, "unit_id": unit_id,
                                 "report": kind})  # fmt: skip

    def attachment(content: bytes | str, media: str, name: str) -> Response:
        return Response(content=content, media_type=media,
                        headers={"Content-Disposition": f'attachment; filename="{name}"',
                                 "Cache-Control": "no-store"})  # fmt: skip

    @router.get("/cases/{case_id}/report.pdf")
    def case_report(case_id: str, rt: RuntimeDep, user: Annotated[User, Depends(case_permission("report_case"))]):
        """A printable report of one case, with the confidentiality footer on every page."""
        state = rt.state
        case = state.case(case_id)
        detail = scope.case_detail(rt, state, case, user)
        standing = views.build_queue(state, rt.decisions, state.team_hours, ranking.Weights(), True, rt.overrides)
        item = next((i for i in standing.scheduled + standing.backlog if i.case_id == case_id), None)
        now = datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")
        pdf = build_case_report(
            detail=detail, item=item, brief=make_brief(rt, state, case_id, 30),
            graph=views.build_case_graph(state, case), events=rt.audit.list(1000, case_id),
            access=detail.access, generated_by=f"{user.display_name} ({user.role.replace('_', ' ')})",
            generated_at=now,
        )  # fmt: skip
        log_download(rt, user, "case_pdf", case_id=case_id, unit_id=detail.access.unit_id)
        return attachment(pdf, PDF, f"{case_id}-report.pdf")

    def unit_file(rt, user: User, unit_id: int):
        data = unit_report.unit_data(rt, unit_id)
        return data, f"unit-{data['unit']['name'].lower().replace(' ', '-')}-report"

    @router.get("/units/{unit_id}/report.pdf")
    def unit_report_pdf(unit_id: int, rt: RuntimeDep, user: Annotated[User, Depends(unit_permission("report_unit"))]):
        data, name = unit_file(rt, user, unit_id)
        pdf = unit_report.unit_pdf(data, f"{user.display_name} ({user.role.replace('_', ' ')})")
        log_download(rt, user, "unit_pdf", unit_id=unit_id)
        return attachment(pdf, PDF, f"{name}.pdf")

    @router.get("/units/{unit_id}/report.csv")
    def unit_report_csv(unit_id: int, rt: RuntimeDep, user: Annotated[User, Depends(unit_permission("report_unit"))]):
        data, name = unit_file(rt, user, unit_id)
        log_download(rt, user, "unit_csv", unit_id=unit_id)
        return attachment(unit_report.unit_csv(data), "text/csv; charset=utf-8", f"{name}.csv")

    # ------------------------------------------------------------ administration

    @router.get("/admin/users", response_model=list[UserOut])
    def list_users(rt: RuntimeDep, _: AdminDep):
        return [user_out(rt, u) for u in rt.access.list_users()]

    @router.post("/admin/users", response_model=UserOut, status_code=201)
    def create_user(body: UserCreate, rt: RuntimeDep, admin: AdminDep):
        """Create a user with an initial password (stored only as a bcrypt hash)."""
        if not USERNAME.match(body.username.strip().lower()):
            raise HTTPException(422, "A username is 3 to 40 lowercase letters, digits, dots, dashes or underscores.")
        if body.role == "admin" and body.unit_id is not None:
            raise HTTPException(422, "An admin does not belong to a unit.")
        if body.role != "admin" and (body.unit_id is None or rt.access.get_unit(body.unit_id) is None):
            raise HTTPException(422, "Team leads and investigators need an existing unit.")
        try:
            created = rt.access.create_user(
                username=body.username.strip().lower(), display_name=body.display_name,
                role=body.role, unit_id=body.unit_id, password_hash=hash_password(body.password),
            )  # fmt: skip
        except DuplicateError as exc:
            raise HTTPException(409, str(exc)) from exc
        rt.audit.append("user_create", reviewer=admin.display_name,
                        details={"user_id": created.id, "role": created.role, "unit_id": created.unit_id,
                                 "username": created.username})  # fmt: skip
        return user_out(rt, created)

    @router.post("/admin/users/{user_id}/active", response_model=UserOut)
    def set_active(user_id: int, body: ActiveIn, rt: RuntimeDep, admin: AdminDep):
        target = rt.access.get_user(user_id)
        if target is None:
            raise HTTPException(404, f"Unknown user {user_id}")
        if target.id == admin.id and not body.active:
            raise HTTPException(409, "You cannot deactivate your own account.")
        try:
            rt.access.set_active(user_id, body.active)
        except DuplicateError as exc:
            raise HTTPException(409, str(exc)) from exc
        rt.audit.append("user_active", reviewer=admin.display_name,
                        details={"user_id": user_id, "active": body.active})  # fmt: skip
        return user_out(rt, rt.access.get_user(user_id))

    @router.get("/admin/units", response_model=list[UnitOut])
    def list_units(rt: RuntimeDep, _: AdminDep):
        return rt.access.list_units()

    @router.put("/admin/units/{unit_id}", response_model=UnitOut)
    def update_unit(unit_id: int, body: UnitUpdate, rt: RuntimeDep, admin: AdminDep):
        """Change the cities a unit covers, then route any unrouted case that now fits."""
        if rt.access.get_unit(unit_id) is None:
            raise HTTPException(404, f"Unknown unit {unit_id}")
        region = sorted({c.strip() for c in body.region if c.strip()})
        rt.access.set_region(unit_id, region)
        rt.audit.append("unit_update", reviewer=admin.display_name,
                        details={"unit_id": unit_id, "region": region})  # fmt: skip
        if rt.state is not None:
            route_cases(rt.state.cases, rt.state.db_path, rt.access)
        return rt.access.get_unit(unit_id)

    @router.get("/admin/unrouted", response_model=list[str])
    def unrouted(rt: RuntimeDep, _: AdminDep):
        """Cases no unit covers: only an admin sees them."""
        return rt.access.unrouted()

    return router
