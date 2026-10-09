"""Plain names for what raised a finding, so a brief can say what type of check found it."""

from __future__ import annotations

RULE_LABELS = {
    "duplicate": "Duplicate billing",
    "upcoding": "Upcoding",
    "unbundling": "Unbundling",
    "phantom": "Billing during a hospital stay",
    "impossible_timing": "Impossible timing",
    "utilization": "Excess utilization",
    "repeat_history": "Repeat investigation history",
    "anomaly": "Provider profile anomaly",
    "ring": "Referral ring",
}
KINDS = {"anomaly": "anomaly model", "ring": "network analysis"}  # everything else is a claim rule


def rule_label(detector: str) -> str:
    """'duplicate' -> 'Duplicate billing'; a plug-in rule with no entry gets a tidied version of its name."""
    return RULE_LABELS.get(detector, detector.replace("_", " ").capitalize())


def rule_kind(detector: str) -> str:
    return KINDS.get(detector, "claim rule")


def describe(detector: str) -> str:
    """The label and the kind of check: 'Duplicate billing (claim rule)'."""
    return f"{rule_label(detector)} ({rule_kind(detector)})"
