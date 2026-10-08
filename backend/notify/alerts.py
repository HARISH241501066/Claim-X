"""Alerts raised after a pipeline run.

Everything a reviewer should know about appears in the platform. Only urgent cases also send an
email to the SIU team: a case with a critical finding (a phantom service), an investigation risk
band of High, or a place in the top 3 of the queue. A case in the top 5 that meets none of those
gets a high-priority notification in the platform but no email. One email per case per 24 hours.
Nothing here can stop a pipeline run: the caller wraps it, and email failures are recorded.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from backend.audit import now_iso
from backend.notify import emails
from backend.notify.notifier import Message, Notifier, NotifyConfig
from backend.notify.store import NotifyStore

log = logging.getLogger("claimshield.alerts")
CRITICAL_DETECTORS = {"phantom"}  # a service that never happened is treated as critical
EMAIL_TOP_RANKS = 3
HIGH_TOP_RANKS = 5
COOLDOWN = timedelta(hours=24)
PENDING_AFTER = timedelta(days=3)


@dataclass
class AlertResult:
    created: list[dict] = field(default_factory=list)
    emails: list[dict] = field(default_factory=list)  # {case_id, email_status} for every attempt


def _parse(stamp: str) -> datetime:
    return datetime.fromisoformat(stamp)


def has_critical_finding(case) -> bool:
    return any(f["detector"] in CRITICAL_DETECTORS and f["severity"] == "high" for f in case.findings)


def priority_reasons(case, rank: int) -> tuple[list[str], bool]:
    """(why the case is high priority, whether that earns an email)."""
    reasons, urgent = [], False
    if has_critical_finding(case):
        reasons.append("a critical finding")
        urgent = True
    if (case.investigation_band or "").lower() == "high":
        reasons.append("a high investigation-risk band")
        urgent = True
    if rank and rank <= EMAIL_TOP_RANKS:
        reasons.append(f"top {EMAIL_TOP_RANKS} of the queue")
        urgent = True
    elif rank and rank <= HIGH_TOP_RANKS:
        reasons.append(f"top {HIGH_TOP_RANKS} of the queue")
    return reasons, urgent


def run_alerts(
    *,
    cases: Sequence,
    ranks: dict[str, int],
    backlog_count: int,
    decided: set[str],
    store: NotifyStore,
    audit,
    email: Notifier | None,
    config: NotifyConfig,
    now: datetime | None = None,
) -> AlertResult:  # fmt: skip
    """Create this run's notifications. Repeating a run adds nothing that was already said."""
    now = now or datetime.now(UTC)
    stamp = now.isoformat(timespec="seconds").replace("+00:00", "Z")
    result = AlertResult()

    def add(role, type_, severity, case_id, message, key, email_status="not_required"):
        if key and store.has_dedupe_key(key):
            return None
        note = store.add_notification(role=role, type=type_, severity=severity, case_id=case_id,
                                      message=message, email_status=email_status,
                                      dedupe_key=key)  # fmt: skip
        audit.append("notification", case_id=case_id,
                     details={"id": note["id"], "type": type_, "severity": severity,
                              "role": role, "email_status": email_status})  # fmt: skip
        result.created.append(note)
        return note

    for case in cases:
        try:
            _one_case(case, ranks, decided, store, audit, email, config, now, stamp, result, add)
        except Exception:
            log.exception("alerts for %s failed", case.case_id)

    if backlog_count > 0:
        add("siu", "capacity", "warning", None,
            f"Capacity exceeded: {backlog_count} cases in backlog.", f"capacity:{backlog_count}")  # fmt: skip
    return result


def _one_case(case, ranks, decided, store, audit, email, config, now, stamp, result, add) -> None:
    rank = ranks.get(case.case_id, 0)
    current = {f["finding_id"] for f in case.findings}
    seen = store.get_seen(case.case_id)
    detectors = len(case.detectors_fired)
    if seen is None:
        severity = "warning" if case.worst_severity in {"high", "critical"} else "info"
        add("siu", "new_case", severity, case.case_id,
            f"New case {case.case_id} ({case.case_type}) awaits review: {detectors} of 3 "
            f"detectors agree, queue rank {rank}.", f"new_case:{case.case_id}")  # fmt: skip
        store.put_seen(case.case_id, sorted(current), stamp)
    else:
        added = sorted(current - seen["finding_ids"])
        if added:
            add("siu", "new_finding", "warning", case.case_id,
                f"{len(added)} new finding(s) on {case.case_id}.",
                f"new_finding:{case.case_id}:{','.join(added)}")  # fmt: skip
        if added or current != seen["finding_ids"]:
            store.put_seen(case.case_id, sorted(current))
        if case.case_id not in decided and now - _parse(seen["first_seen"]) > PENDING_AFTER:
            add("manager", "decision_pending", "warning", case.case_id,
                f"Decision pending over 3 days on {case.case_id}.",
                f"pending:{case.case_id}:{seen['first_seen'][:10]}")  # fmt: skip
    if case.case_id not in decided:
        _high_priority(case, rank, detectors, store, audit, email, config, now, stamp, result)


def _high_priority(case, rank, detectors, store, audit, email, config, now, stamp, result) -> None:
    reasons, urgent = priority_reasons(case, rank)
    if not reasons:
        return
    cid = case.case_id
    row = store.latest_with_key_prefix("high_priority", cid)
    recent = bool(row and now - _parse(row["created_at"]) < COOLDOWN)
    if not recent:
        row = store.add_notification(
            role="siu", type="high_priority", severity="high", case_id=cid,
            message=f"High priority: {cid} (rank {rank}) — {', '.join(reasons)}.",
            email_status="not_required" if not urgent else "disabled",
        )  # fmt: skip
        audit.append("notification", case_id=cid,
                     details={"id": row["id"], "type": "high_priority", "severity": "high",
                              "role": "siu"})  # fmt: skip
        result.created.append(row)
    if not urgent:
        return
    status, error = _email_status(case, rank, detectors, store, email, config, now, stamp, recent)
    if not recent or status != "skipped_cooldown":
        store.set_email_status(row["id"], status)
    result.emails.append({"case_id": cid, "email_status": status})
    audit.append("email_attempt", case_id=cid,
                 details={"status": status, "error": error, "notification_id": row["id"]})  # fmt: skip


def _email_status(case, rank, detectors, store, email, config, now, stamp, recent) -> tuple[str, str | None]:
    cid = case.case_id
    if email is None:
        return "disabled", None
    seen = store.get_seen(cid)
    last = seen["last_email_at"] if seen else None
    if last and now - _parse(last) < COOLDOWN:
        return "skipped_cooldown", None
    subject, body = emails.build_email(cid, rank, detectors, config.base_url)
    try:
        emails.validate_email(subject, body, cid, rank, detectors, config.base_url)
    except emails.EmailBlocked as exc:
        log.error("email for %s blocked: %s", cid, exc)
        return "failed", f"blocked: {exc}"
    status = email.send(Message(subject, body, cid))
    if status == "sent":
        store.set_last_email(cid, stamp or now_iso())
        return "sent", None
    return "failed", getattr(email, "last_error", None) or "not sent"
