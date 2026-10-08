"""Shared FastAPI dependencies: who is calling, and what they may do.

Every protected route depends on `current_user`. Case and unit routes add a permission
dependency built by `case_permission` / `unit_permission`; the rules themselves are in
backend/access/permissions.py. A refusal is a 403 with a plain message, and it is written to the
audit log (who, what, which case) before the error is returned.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Depends, HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

from backend.access import permissions
from backend.access.security import NotConfiguredError, decode_token
from backend.access.store import User
from backend.pipeline import PipelineState

bearer = HTTPBearer(auto_error=False, description="The token from POST /auth/login")


def get_runtime(request: Request):
    return request.app.state.runtime


def get_state(runtime: Annotated[object, Depends(get_runtime)]) -> PipelineState:
    if runtime.state is None:
        raise HTTPException(503, "The pipeline is not ready yet. Check /health.")
    return runtime.state


def current_user(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    rt: Annotated[object, Depends(get_runtime)],
) -> User:
    """The signed-in user, re-read from the database so a deactivation applies at once."""
    unauthorized = HTTPException(401, "Sign in to continue.", headers={"WWW-Authenticate": "Bearer"})
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise unauthorized
    try:
        claims = decode_token(credentials.credentials)
    except NotConfiguredError as exc:
        raise HTTPException(503, str(exc)) from exc
    if claims is None:
        raise HTTPException(401, "Your session has expired or is not valid. Sign in again.",
                            headers={"WWW-Authenticate": "Bearer"})  # fmt: skip
    try:
        user = rt.access.get_user(int(claims["sub"]))
    except (TypeError, ValueError):
        user = None
    if user is None or not user.active:
        raise unauthorized
    return user


UserDep = Annotated[User, Depends(current_user)]


def deny(rt, user: User, action: str, request: Request | None, case_id: str | None = None) -> None:
    """Record the refused attempt, then answer 403."""
    rt.audit.append(
        "access_denied", case_id=case_id, action=action, reviewer=user.display_name,
        details={"user_id": user.id, "role": user.role, "path": request.url.path if request else None,
                 "method": request.method if request else None},
    )  # fmt: skip
    raise HTTPException(403, permissions.DENIED.get(action, "You are not allowed to do that."))


def authorize_case(rt, user: User, action: str, case_id: str, request: Request | None = None) -> dict | None:
    """Check `action` on a case. Returns the assignment row (None for an unrouted case)."""
    state = rt.state
    if state is None:
        raise HTTPException(503, "The pipeline is not ready yet. Check /health.")
    if state.case(case_id) is None:
        raise HTTPException(404, f"Unknown case {case_id}")
    row = rt.access.get_assignment(case_id)
    if not permissions.allowed(user, action, row):
        deny(rt, user, action, request, case_id)
    return row


def authorize_unit(rt, user: User, action: str, unit_id: int, request: Request | None = None) -> dict:
    unit = rt.access.get_unit(unit_id)
    if unit is None:
        raise HTTPException(404, f"Unknown unit {unit_id}")
    if not permissions.allowed_unit(user, action, unit_id):
        deny(rt, user, action, request)
    return unit


def case_permission(action: str):
    """A dependency: the signed-in user may do `action` on the case in the path."""

    def check(case_id: str, request: Request, user: UserDep, rt: Annotated[object, Depends(get_runtime)]) -> User:
        authorize_case(rt, user, action, case_id, request)
        return user

    return check


def unit_permission(action: str):
    def check(unit_id: int, request: Request, user: UserDep, rt: Annotated[object, Depends(get_runtime)]) -> User:
        authorize_unit(rt, user, action, unit_id, request)
        return user

    return check


def require_admin(request: Request, user: UserDep, rt: Annotated[object, Depends(get_runtime)]) -> User:
    if not permissions.allowed_admin(user):
        deny(rt, user, "admin", request)
    return user


AdminDep = Annotated[User, Depends(require_admin)]


def visible_cases(rt, user: User, state: PipelineState, scope: str = "all") -> list:
    """The cases this user may open (scope "all"), or for an investigator the whole unit's list
    (scope "unit", read-only rows)."""
    assignments = rt.access.assignments()
    out = []
    for case in state.cases:
        row = assignments.get(case.case_id)
        if user.role == "admin":
            out.append(case)
        elif scope == "unit" and user.role == "investigator":
            if row and row["unit_id"] == user.unit_id:
                out.append(case)
        elif permissions.allowed(user, "view", row):
            out.append(case)
    return out
