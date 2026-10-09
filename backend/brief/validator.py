"""Brief validator: reject text that cites unknown evidence, drops sections or accuses anyone."""

from __future__ import annotations

import re

from backend.brief.evidence import Pack
from backend.brief.labels import describe, rule_label
from backend.brief.template import FINAL_LINE, SECTIONS

BANNED_WORDS = ("guilty", "fraudster", "committed fraud", "fraudulent", "criminal")
CITATION = re.compile(r"\[(E[^\]]*)\]")
HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)


def parse_sections(text: str) -> list[tuple[str, str]]:
    """Split the brief at its '## ' headings into (title, body) pairs."""
    matches = list(HEADING.finditer(text))
    sections = []
    for i, m in enumerate(matches):
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        sections.append((m.group(1), text[m.end() : end]))
    return sections


def _squash(text: str) -> str:
    return " ".join(text.split())


def validate_brief(text: str, pack: Pack) -> list[str]:
    """Return the reasons the brief is unacceptable; an empty list means it is valid."""
    reasons: list[str] = []
    sections = parse_sections(text)
    titles = [t for t, _ in sections]
    missing = [s for s in SECTIONS if s not in titles]
    if missing:
        reasons.append(f"missing sections: {', '.join(missing)}")
    elif titles != SECTIONS:
        reasons.append("sections are out of order or duplicated")
    body = dict(sections)

    valid_keys = set(pack.keys)
    for token in CITATION.findall(text):
        if not re.fullmatch(r"E\d+", token):
            reasons.append(f"malformed citation [{token}]")
        elif token not in valid_keys:
            reasons.append(f"unknown citation [{token}]")
    for needed in ("Summary", "Evidence"):
        found = [t for t in CITATION.findall(body.get(needed, "")) if t in valid_keys]
        if needed in body and not found:
            reasons.append(f"{needed} has no valid [E#] citation")

    if "Evidence" in body:  # every evidence item must say what type of check found it
        evidence_text = body["Evidence"].lower()
        reasons += [
            f"Evidence section does not name the type of check for [{e.key}] (expected '{describe(e.detector)}')"
            for e in pack.evidence
            if rule_label(e.detector).lower() not in evidence_text
        ]

    lowered = text.lower()
    reasons += [f"banned word '{w}'" for w in BANNED_WORDS if w in lowered]

    lines = [ln.strip() for ln in text.strip().splitlines() if ln.strip()]
    if not lines or lines[-1] != FINAL_LINE:
        reasons.append("does not end with the required final line")

    level_line = f"Confidence level: {pack.confidence['level']}"
    if "Confidence" in body and level_line not in body["Confidence"]:
        reasons.append(f"confidence changed (expected '{level_line}')")
    if "Limitations" in body:
        squashed = _squash(body["Limitations"])
        reasons += [
            f"limitation changed or missing: {lim[:40]}..."
            for lim in pack.limitations
            if _squash(lim) not in squashed
        ]
    return reasons
