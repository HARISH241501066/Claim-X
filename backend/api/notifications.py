"""Notification and outbound-draft endpoints.

Everything is scoped to the signed-in user. Nothing here denies a claim or blocks a payment, and
nothing is ever sent to a provider or member: approving a draft only marks it `sent_simulated`.
Every create, edit, approval and email attempt is written to the audit log.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, StringConstraints

from backend.api.deps import AdminDep, UserDep, authorize_case, get_runtime, visible_cases
from backend.notify import emails, outbound
from backend.notify.notifier import Message, SnsEmailNotifier, email_channel, load_config

Role = Literal["siu", "manager"]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]


class NotificationOut(BaseModel):
    id: int
    recipient_role: Role
    type: str
    severity: Literal["info", "warning", "high"]
    case_id: str | None = None
    message: str
    created_at: str
    read: bool
    email_status: Literal["not_required", "sent", "failed", "skipped_cooldown", "disabled"]


class NotificationsOut(BaseModel):
    notifications: list[NotificationOut]
    unread_count: int


class TestEmailOut(BaseModel):
    ok: bool
    status: str
    detail: str


class OutboundCreate(BaseModel):
    recipient_type: Literal["provider", "member"]
    recipient_id: Annotated[str, StringConstraints(strip_whitespace=True, min_length=3, max_length=40)]
    template: Literal["records_request", "service_verification"]


class OutboundEdit(BaseModel):
    subject: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


class OutboundApprove(BaseModel):
    reason: Reason


class OutboundOut(BaseModel):
    id: int
    case_id: str
    recipient_type: Literal["provider", "member"]
    recipient_id: str
    template: str
    subject: str
    body: str
    status: Literal["draft", "approved", "sent_simulated"]
    created_by: str
    approved_by: str | None = None
    created_at: str
    sent_at: str | None = None


def build_router() -> APIRouter:
    router = APIRouter()
    RuntimeDep = Annotated[object, Depends(get_runtime)]

    def seen_by(rt, user) -> list[dict]:
        """The notifications this user may see: their own messages, those about cases they may
        open, and the general ones (backlog) for admins and team leads."""
        state = rt.state
        allowed_cases = {c.case_id for c in visible_cases(rt, user, state)} if state else set()
        read = rt.access.read_ids(user.id)
        out = []
        for note in rt.notify.list_notifications(None, False, 1000):
            target = note.get("recipient_user_id")
            if target is not None:
                show = target == user.id
            elif note["case_id"] is None:
                show = user.role in {"admin", "team_lead"}
            else:
                show = note["case_id"] in allowed_cases
            if show:
                out.append({**note, "read": note["id"] in read})
        return out

    @router.get("/notifications", response_model=NotificationsOut)
    def notifications(rt: RuntimeDep, user: UserDep, unread_only: bool = False):
        """Your notifications, newest first within each level, high priority on top."""
        mine = seen_by(rt, user)
        shown = [n for n in mine if not n["read"]] if unread_only else mine
        return NotificationsOut(notifications=shown[:200],
                                unread_count=sum(1 for n in mine if not n["read"]))  # fmt: skip

    @router.post("/notifications/read-all")
    def read_all(rt: RuntimeDep, user: UserDep):
        unread = [n["id"] for n in seen_by(rt, user) if not n["read"]]
        return {"marked": rt.access.mark_read(user.id, unread)}

    @router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
    def read_one(notification_id: int, rt: RuntimeDep, user: UserDep):
        note = next((n for n in seen_by(rt, user) if n["id"] == notification_id), None)
        if note is None:
            raise HTTPException(404, f"Unknown notification {notification_id}")
        rt.access.mark_read(user.id, [notification_id])
        return {**note, "read": True}

    @router.post("/admin/test-email", response_model=TestEmailOut)
    def test_email(rt: RuntimeDep, user: AdminDep):
        """Send one test email with no case data and say exactly what happened."""
        config = load_config()
        channel = email_channel(config)
        if channel is None:
            result = TestEmailOut(ok=False, status="disabled", detail="NOTIFY_EMAIL_ENABLED is not true.")
        elif not isinstance(channel, SnsEmailNotifier):
            result = TestEmailOut(ok=False, status="failed", detail="SNS_TOPIC_ARN is not set.")
        else:
            status = channel.send(Message(emails.TEST_SUBJECT, emails.TEST_BODY))
            ok = status == "sent"
            result = TestEmailOut(
                ok=ok, status=status,
                detail="Test email published to the SNS topic." if ok else
                f"Sending failed: {channel.last_error or 'unknown error'}.",
            )  # fmt: skip
        rt.audit.append("email_attempt", reviewer=user.display_name,
                        details={"test": True, "status": result.status, "ok": result.ok})  # fmt: skip
        return result

    @router.post("/cases/{case_id}/outbound", response_model=OutboundOut, status_code=201)
    def create_outbound(case_id: str, body: OutboundCreate, request: Request, rt: RuntimeDep, user: UserDep):
        """A DRAFT only. A person must edit and approve it, and even then it is only simulated."""
        authorize_case(rt, user, "outbound", case_id, request)
        case = rt.state.case(case_id)
        spec = outbound.TEMPLATES[body.template]
        try:
            if spec["recipient_type"] != body.recipient_type:
                raise outbound.OutboundError(
                    f"The {body.template} template is for a {spec['recipient_type']}."
                )
            subject, text = outbound.draft_for(case, rt.state.db_path, body.template, body.recipient_id)
            outbound.validate_text(subject, text)
        except outbound.OutboundError as exc:
            raise HTTPException(422, str(exc)) from exc
        row = rt.notify.add_outbound(
            case_id=case_id, recipient_type=body.recipient_type, recipient_id=body.recipient_id,
            template=body.template, subject=subject, body=text, created_by=user.display_name,
        )  # fmt: skip
        rt.audit.append("outbound_create", case_id=case_id, reviewer=user.display_name,
                        details={"id": row["id"], "template": body.template, "user_id": user.id,
                                 "recipient_type": body.recipient_type,
                                 "recipient_id": body.recipient_id})  # fmt: skip
        return row

    def find_outbound(rt, outbound_id: int) -> dict:
        row = rt.notify.get_outbound(outbound_id)
        if row is None:
            raise HTTPException(404, f"Unknown message {outbound_id}")
        return row

    @router.put("/outbound/{outbound_id}", response_model=OutboundOut)
    def edit_outbound(outbound_id: int, body: OutboundEdit, request: Request, rt: RuntimeDep, user: UserDep):
        row = find_outbound(rt, outbound_id)
        authorize_case(rt, user, "outbound", row["case_id"], request)
        if row["status"] != "draft":
            raise HTTPException(409, "Only a draft can be edited.")
        try:
            outbound.validate_text(body.subject, body.body)
        except outbound.OutboundError as exc:
            raise HTTPException(422, str(exc)) from exc
        rt.notify.update_outbound(outbound_id, body.subject, body.body)
        rt.audit.append("outbound_edit", case_id=row["case_id"], reviewer=user.display_name,
                        details={"id": outbound_id, "user_id": user.id})  # fmt: skip
        return rt.notify.get_outbound(outbound_id)

    @router.post("/outbound/{outbound_id}/approve", response_model=OutboundOut)
    def approve_outbound(outbound_id: int, body: OutboundApprove, request: Request, rt: RuntimeDep, user: UserDep):
        """Marks the message sent_simulated. Nothing leaves the platform."""
        row = find_outbound(rt, outbound_id)
        authorize_case(rt, user, "outbound", row["case_id"], request)
        if row["status"] != "draft":
            raise HTTPException(409, "This message has already been approved.")
        try:
            outbound.validate_text(row["subject"], row["body"])  # checked once more at approval
        except outbound.OutboundError as exc:
            raise HTTPException(422, str(exc)) from exc
        rt.notify.approve_outbound(outbound_id, user.display_name)
        rt.audit.append("outbound_approve", case_id=row["case_id"], action="sent_simulated",
                        reason=body.reason, reviewer=user.display_name,
                        details={"id": outbound_id, "template": row["template"],
                                 "user_id": user.id})  # fmt: skip
        return rt.notify.get_outbound(outbound_id)

    @router.get("/cases/{case_id}/outbound", response_model=list[OutboundOut])
    def list_outbound(case_id: str, request: Request, rt: RuntimeDep, user: UserDep):
        authorize_case(rt, user, "view", case_id, request)
        return rt.notify.list_outbound(case_id)

    return router
