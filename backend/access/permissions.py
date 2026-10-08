"""The permission matrix, in one place.

| Action        | admin | team_lead (own unit) | investigator    |
| view          | all   | unit cases           | assigned cases  |
| assign        | no    | yes                  | no              |
| decide        | no    | yes                  | assigned only   |
| outbound      | no    | yes                  | assigned only   |
| report_case   | yes   | unit cases           | assigned only   |
| report_unit   | yes   | own unit             | no              |
| admin         | yes   | no                   | no              |

`allowed` answers for a case through its assignment row; `allowed_unit` answers for a unit.
Every route goes through these two functions (see api/auth.py), so the rules live here only.
"""

from __future__ import annotations

from backend.access.store import User

CASE_ACTIONS = ("view", "assign", "decide", "outbound", "report_case")
ALL_ACTIONS = (*CASE_ACTIONS, "report_unit", "admin")


def allowed(user: User, action: str, assignment: dict | None) -> bool:
    """May this user do `action` on the case with this assignment row (None: no row)?"""
    if action not in CASE_ACTIONS:
        return False
    if user.role == "admin":
        return action in {"view", "report_case"}
    if assignment is None or assignment["unit_id"] is None:
        return False  # an unrouted case belongs to nobody but the admin
    if user.role == "team_lead":
        return assignment["unit_id"] == user.unit_id
    if user.role == "investigator":
        return (
            action in {"view", "decide", "outbound", "report_case"}
            and assignment["assignee_user_id"] == user.id
        )  # fmt: skip
    return False


def allowed_unit(user: User, action: str, unit_id: int) -> bool:
    if action in {"report_unit", "workload"}:
        return user.role == "admin" or (user.role == "team_lead" and user.unit_id == unit_id)
    if action == "view_unit":  # the unit's queue and workload
        return user.role == "admin" or (user.role in {"team_lead", "investigator"} and user.unit_id == unit_id)
    return False


def allowed_admin(user: User) -> bool:
    return user.role == "admin"


DENIED = {
    "view": "You do not have access to this case.",
    "assign": "Only the team lead of the case's unit can assign it.",
    "decide": "Only the assigned investigator or the unit's team lead can record a decision on this case.",
    "outbound": "Only the assigned investigator or the unit's team lead can draft messages for this case.",
    "report_case": "You do not have access to this case's report.",
    "report_unit": "Only the unit's team lead or an admin can download a unit report.",
    "view_unit": "You do not have access to this unit.",
    "workload": "Only the unit's team lead or an admin can see the unit's workload.",
    "admin": "This action is for administrators only.",
}
