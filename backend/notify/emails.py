"""The only email the platform sends, and the check that keeps it free of personal details.

A high-priority email carries four facts: the case ID, its priority rank, how many detectors
agree, and a link. It has no names, member or claim IDs, rupee amounts, diagnoses or risk scores.
Every body is rebuilt from the template and scanned before it is sent.
"""

from __future__ import annotations

import re

FORBIDDEN_WORDS = re.compile(r"\b(?:fraud\w*|guilty)\b", re.IGNORECASE)
RECORD_ID = re.compile(r"\b(?:PRV|MEM|OWN|FAC|CLM|FND|ENC|PRC)-[A-Z0-9]+\b")
CASE_ID = re.compile(r"\bCASE-\d{4}\b")
MONEY = re.compile(r"(?:₹|\bRs\.?|\bINR\b)\s*\d|\b\d{1,3}(?:,\d{2,3})+\b", re.IGNORECASE)
DECIMAL = re.compile(r"\d+\.\d+")  # a decimal could be a risk score
URL = re.compile(r"https?://\S+")


class EmailBlocked(ValueError):
    """The text broke an email rule, so nothing is sent."""


def build_email(case_id: str, rank: int, detectors: int, base_url: str) -> tuple[str, str]:
    subject = f"Claim-X: high-priority case {case_id} awaiting review"
    body = (
        "A case is awaiting human review.\n"
        f"Case: {case_id}\n"
        f"Priority rank: {rank}\n"
        f"Detectors agreeing: {detectors}\n"
        f"Open it here: {link(base_url, case_id)}\n"
    )
    return subject, body


def link(base_url: str, case_id: str) -> str:
    return f"{base_url.rstrip('/')}/cases/{case_id}"


def validate_email(subject: str, body: str, case_id: str, rank: int, detectors: int, base_url: str) -> None:
    """Raise EmailBlocked unless the text is exactly the template and holds nothing personal."""
    expected_subject, expected_body = build_email(case_id, rank, detectors, base_url)
    if (subject, body) != (expected_subject, expected_body):
        raise EmailBlocked("the text differs from the approved template")
    scan = URL.sub("", f"{subject}\n{body}")  # the link is checked by the template match above
    for name, pattern in (
        ("a forbidden word", FORBIDDEN_WORDS), ("a record identifier", RECORD_ID),
        ("a money amount", MONEY), ("a decimal number", DECIMAL),
    ):
        if pattern.search(scan):
            raise EmailBlocked(f"the text contains {name}")
    if set(CASE_ID.findall(scan)) != {case_id}:
        raise EmailBlocked("the text names a case other than the one it is about")


TEST_SUBJECT = "Claim-X: test email"
TEST_BODY = "This is a test message from Claim-X. It contains no case data.\n"
