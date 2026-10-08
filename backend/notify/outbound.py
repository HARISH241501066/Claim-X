"""Drafts of messages to providers and members. Nothing here is ever really sent.

A reviewer asks for a draft, edits it, and approves it. Approval only marks the message
`sent_simulated`. The wording is checked on creation, on every edit and again at approval, so a
draft can never hint at suspicion or an investigation, and the recipient is never told why.
"""

from __future__ import annotations

import re
import sqlite3
from pathlib import Path

CLAIM_LIMIT = 10
BANNED = re.compile(
    r"\b(?:fraud\w*|suspic\w*|investigat\w*|risks?|flag(?:ged|s|ging)?|siu|scores?)\b", re.IGNORECASE
)  # fmt: skip
TEMPLATES = {
    "records_request": {
        "recipient_type": "provider",
        "subject": "Request for records",
        "intro": "As part of a routine documentation review, please submit records for the "
                 "following claims: {items}. Kindly respond within 15 days.",
    },
    "service_verification": {
        "recipient_type": "member",
        "subject": "Please confirm your recent services",
        "intro": "Please confirm whether you received the following services: {items}. "
                 "Reply Yes or No for each.",
    },
}  # fmt: skip


class OutboundError(ValueError):
    """The draft or the request is not acceptable; the message is safe to show a reviewer."""


def banned_words(*texts: str) -> list[str]:
    found: list[str] = []
    for text in texts:
        for match in BANNED.finditer(text):
            word = match.group(0).lower()
            if word not in found:
                found.append(word)
    return found


def validate_text(subject: str, body: str) -> None:
    if not subject.strip() or not body.strip():
        raise OutboundError("A subject and a message are both required.")
    words = banned_words(subject, body)
    if words:
        raise OutboundError(
            "This wording is not allowed in a message to a provider or member: "
            + ", ".join(f"“{w}”" for w in words) + ". Remove it and try again."
        )


def draft_for(case, state_db: Path, template: str, recipient_id: str) -> tuple[str, str]:
    """The template filled from the case's claims. The recipient must belong to the case."""
    spec = TEMPLATES.get(template)
    if spec is None:
        raise OutboundError(f"Unknown template '{template}'.")
    if spec["recipient_type"] == "provider":
        if recipient_id not in case.entity_ids or not recipient_id.startswith("PRV-"):
            raise OutboundError("That provider is not part of this case.")
    elif recipient_id not in case.affected_members:
        raise OutboundError("That member is not part of this case.")
    claim_ids = sorted(case.flagged_claim_ids)
    con = sqlite3.connect(state_db)
    try:
        if spec["recipient_type"] == "provider":
            rows = _rows(con, claim_ids, "c.provider_id = ?", recipient_id,
                         "c.claim_id, c.service_date")  # fmt: skip
            items = "; ".join(f"{claim} ({day})" for claim, day in rows)
        else:
            rows = _rows(con, claim_ids, "c.member_id = ?", recipient_id,
                         "p.description, c.service_date, c.provider_id", join=True)  # fmt: skip
            items = "; ".join(f"{what}, {day}, {prov}" for what, day, prov in rows)
    finally:
        con.close()
    if not items:
        raise OutboundError("There are no claims for that recipient in this case.")
    return spec["subject"], spec["intro"].format(items=items)


def _rows(con, claim_ids, condition, value, columns, join=False):
    if not claim_ids:
        return []
    marks = ",".join("?" * len(claim_ids))
    source = "claims c JOIN procedure_codes p ON p.code = c.procedure_code" if join else "claims c"
    return con.execute(
        f"SELECT {columns} FROM {source} WHERE c.claim_id IN ({marks}) AND {condition} "
        f"ORDER BY c.service_date, c.claim_id LIMIT {CLAIM_LIMIT}",
        (*claim_ids, value),
    ).fetchall()
