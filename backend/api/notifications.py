"""Notification and outbound-draft endpoints.

Nothing here denies a claim or blocks a payment, and nothing is ever sent to a provider or member:
approving a draft only marks it `sent_simulated`. Every create, edit, approval and email attempt is
written to the audit log.
"""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, StringConstraints

from backend.notify import emails, outbound
from backend.notify.notifier import Message, SnsEmailNotifier, email_channel, load_config

Role = Literal["siu", "manager"]
Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]
Person = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]


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
    created_by: Person = "reviewer"


class OutboundEdit(BaseModel):
    subject: Annotated[str, StringConstraints(strip_whitespace=True, max_length=200)]
    body: Annotated[str, StringConstraints(strip_whitespace=True, max_length=4000)]


class OutboundApprove(BaseModel):
    approved_by: Person
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


def build_router(get_runtime, get_state) -> APIRouter:
    router = APIRouter()
    RuntimeDep = Annotated[object, Depends(get_runtime)]
    StateDep = Annotated[object, Depends(get_state)]

    def find_case(state, case_id: str):
        case = state.case(case_id)
        if case is None:
            raise HTTPException(404, f"Unknown case {case_id}")
        return case

    def find_outbound(rt, outbound_id: int) -> dict:
        row = rt.notify.get_outbound(outbound_id)
        if row is None:
            raise HTTPException(404, f"Unknown message {outbound_id}")
        return row

    @router.get("/notifications", response_model=NotificationsOut)
    def notifications(
        rt: RuntimeDep,
        role: Annotated[Role | None, Query(description="siu or manager")] = None,
        unread_only: bool = False,
    ):
        """Newest first within each level, high priority on top."""
        return NotificationsOut(
            notifications=rt.notify.list_notifications(role, unread_only),
            unread_count=rt.notify.unread_count(role),
        )

    @router.post("/notifications/read-all")
    def read_all(rt: RuntimeDep, role: Role | None = None):
        return {"marked": rt.notify.mark_all_read(role)}

    @router.post("/notifications/{notification_id}/read", response_model=NotificationOut)
    def read_one(notification_id: int, rt: RuntimeDep):
        if not rt.notify.mark_read(notification_id):
            raise HTTPException(404, f"Unknown notification {notification_id}")
        return rt.notify.get_notification(notification_id)

    @router.post("/admin/test-email", response_model=TestEmailOut)
    def test_email(rt: RuntimeDep):
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
        rt.audit.append("email_attempt", details={"test": True, "status": result.status,
                                                  "ok": result.ok})  # fmt: skip
        return result

    @router.post("/cases/{case_id}/outbound", response_model=OutboundOut, status_code=201)
    def create_outbound(case_id: str, body: OutboundCreate, rt: RuntimeDep, state: StateDep):
        """A DRAFT only. A person must edit and approve it, and even then it is only simulated."""
        case = find_case(state, case_id)
        spec = outbound.TEMPLATES[body.template]
        try:
            if spec["recipient_type"] != body.recipient_type:
                raise outbound.OutboundError(
                    f"The {body.template} template is for a {spec['recipient_type']}."
                )
            subject, text = outbound.draft_for(case, state.db_path, body.template, body.recipient_id)
            outbound.validate_text(subject, text)
        except outbound.OutboundError as exc:
            raise HTTPException(422, str(exc)) from exc
        row = rt.notify.add_outbound(
            case_id=case_id, recipient_type=body.recipient_type, recipient_id=body.recipient_id,
            template=body.template, subject=subject, body=text, created_by=body.created_by,
        )  # fmt: skip
        rt.audit.append("outbound_create", case_id=case_id, reviewer=body.created_by,
                        details={"id": row["id"], "template": body.template,
                                 "recipient_type": body.recipient_type,
                                 "recipient_id": body.recipient_id})  # fmt: skip
        return row

    @router.put("/outbound/{outbound_id}", response_model=OutboundOut)
    def edit_outbound(outbound_id: int, body: OutboundEdit, rt: RuntimeDep):
        row = find_outbound(rt, outbound_id)
        if row["status"] != "draft":
            raise HTTPException(409, "Only a draft can be edited.")
        try:
            outbound.validate_text(body.subject, body.body)
        except outbound.OutboundError as exc:
            raise HTTPException(422, str(exc)) from exc
        rt.notify.update_outbound(outbound_id, body.subject, body.body)
        rt.audit.append("outbound_edit", case_id=row["case_id"],
                        details={"id": outbound_id})  # fmt: skip
        return rt.notify.get_outbound(outbound_id)

    @router.post("/outbound/{outbound_id}/approve", response_model=OutboundOut)
    def approve_outbound(outbound_id: int, body: OutboundApprove, rt: RuntimeDep):
        """Marks the message sent_simulated. Nothing leaves the platform."""
        row = find_outbound(rt, outbound_id)
        if row["status"] != "draft":
            raise HTTPException(409, "This message has already been approved.")
        try:
            outbound.validate_text(row["subject"], row["body"])  # checked once more at approval
        except outbound.OutboundError as exc:
            raise HTTPException(422, str(exc)) from exc
        rt.notify.approve_outbound(outbound_id, body.approved_by)
        rt.audit.append("outbound_approve", case_id=row["case_id"], action="sent_simulated",
                        reason=body.reason, reviewer=body.approved_by,
                        details={"id": outbound_id, "template": row["template"]})  # fmt: skip
        return rt.notify.get_outbound(outbound_id)

    @router.get("/cases/{case_id}/outbound", response_model=list[OutboundOut])
    def list_outbound(case_id: str, rt: RuntimeDep, state: StateDep):
        find_case(state, case_id)
        return rt.notify.list_outbound(case_id)

    return router
