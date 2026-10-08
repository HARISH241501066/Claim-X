"""Views of the pipeline state narrowed to what one user may see."""

from __future__ import annotations

from collections import Counter
from dataclasses import replace

from backend.access import permissions
from backend.access.store import User
from backend.api import views
from backend.api.deps import visible_cases
from backend.api.schemas import AccessOut, OverviewOut, QueueOut
from backend.cases import ranking
from backend.pipeline import PipelineState


def visible(rt, user: User, state: PipelineState, view: str = "all") -> list:
    """The cases this user may see; `view="unit"` is the investigator's read-only unit queue."""
    return visible_cases(rt, user, state, view)


def scoped_state(state: PipelineState, cases: list) -> PipelineState:
    """The same state with only these cases, so ranking and scheduling run over them alone."""
    return replace(state, cases=list(cases))


def overview_for(state: PipelineState, decisions: dict, overrides: dict, cases: list, scope: str) -> OverviewOut:
    """Headline numbers for a unit or one investigator's own cases."""
    ids = {c.case_id for c in cases}
    decided = len(ids & set(decisions))
    findings = {f["finding_id"]: f["detector"] for c in cases for f in c.findings}
    queue = views.build_queue(scoped_state(state, cases), decisions, state.team_hours,
                              ranking.Weights(), True, overrides)  # fmt: skip
    return OverviewOut(
        claims=state.claims_count, findings=len(findings), cases=len(cases),
        dollars_at_risk=sum(c.flagged_amount for c in cases),
        findings_per_rule=dict(sorted(Counter(findings.values()).items())),
        cases_awaiting_review=len(ids) - decided, cases_decided=decided,
        cases_scheduled=len(queue.scheduled), cases_backlog=len(queue.backlog),
        scheduled_hours=float(sum(i.effort_hours for i in queue.scheduled)),
        team_hours=state.team_hours, scope=scope,
    )  # fmt: skip


def names(rt) -> tuple[dict[int, str], dict[int, User]]:
    units = {u["id"]: u["name"] for u in rt.access.list_units()}
    users = {u.id: u for u in rt.access.list_users()}
    return units, users


def annotate_queue(rt, queue: QueueOut, user: User) -> QueueOut:
    """Add the unit, assignee, assignment status and 'may open' flag to every row."""
    assignments = rt.access.assignments()
    units, users = names(rt)

    def mark(item):
        row = assignments.get(item.case_id)
        assignee = users.get(row["assignee_user_id"]) if row and row["assignee_user_id"] else None
        return item.model_copy(update={
            "unit_name": units.get(row["unit_id"]) if row else None,
            "assignee_id": assignee.id if assignee else None,
            "assignee_name": assignee.display_name if assignee else None,
            "assignment_status": row["status"] if row else "unassigned",
            "can_open": user.role == "admin" or permissions.allowed(user, "view", row),
        })  # fmt: skip

    return queue.model_copy(update={
        "scheduled": [mark(i) for i in queue.scheduled], "backlog": [mark(i) for i in queue.backlog],
    })  # fmt: skip


def access_for(rt, user: User, case_id: str) -> AccessOut:
    row = rt.access.get_assignment(case_id)
    units, users = names(rt)
    assignee = users.get(row["assignee_user_id"]) if row and row["assignee_user_id"] else None
    return AccessOut(
        unit_id=row["unit_id"] if row else None,
        unit_name=units.get(row["unit_id"]) if row else None,
        assignee_id=assignee.id if assignee else None,
        assignee_name=assignee.display_name if assignee else None,
        assignment_status=row["status"] if row else "unassigned",
        assigned_at=row["assigned_at"] if row else None,
        can_decide=permissions.allowed(user, "decide", row),
        can_assign=permissions.allowed(user, "assign", row),
        can_outbound=permissions.allowed(user, "outbound", row),
        can_report=permissions.allowed(user, "report_case", row),
    )  # fmt: skip


def case_detail(rt, state: PipelineState, case, user: User):
    """The case screen's data, with who holds the case and what this user may do with it."""
    events = rt.audit.list(1000, case.case_id)
    history = [e for e in events if e["event_type"] == "decision"]
    changes = [e for e in events if e["event_type"] == "priority_override"]
    detail = views.build_case_detail(state, case, rt.decisions, history, rt.overrides, changes)
    return detail.model_copy(update={"access": access_for(rt, user, case.case_id)})
