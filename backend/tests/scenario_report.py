"""Which detector caught which planted scenario:  python -m backend.tests.scenario_report

It builds the synthetic data and runs the whole pipeline in a temporary folder, then checks every
row of the ground truth (used only here and in tests) against what the detectors flagged. The
output is a Markdown table for the README. Nothing is written into the repository.
"""

from __future__ import annotations

import csv
import re
import sys
import tempfile
from collections import defaultdict
from pathlib import Path

from backend import pipeline
from backend.data import generator

HONEST = {"honest_specialist": "never flagged", "honest_followup": "not a duplicate"}
LABELS = {
    "ring": "Referral ring (self-referring owners)", "upcoder": "Upcoding provider",
    "double_billing": "Duplicate billing", "unbundling": "Unbundled panels", "phantom": "Phantom services (member in hospital)",
    "overutilizer": "Over-utilising members", "repeat_offender": "Repeat offender (rising volume)",
    "impossible_timing": "Impossible timing (30 hours in a day)", "honest_specialist": "Honest busy specialist (should NOT be flagged)",
    "honest_followup": "Honest same-day follow-ups (should NOT be flagged)",
}  # fmt: skip


def mentions(finding: dict, entity: str) -> bool:
    return (
        finding["entity_id"] == entity
        or entity in finding["evidence_ids"]
        or re.search(rf"\b{re.escape(entity)}\b", finding["reason"]) is not None
    )


def analyse(work: Path) -> dict:
    """Per scenario: how many planted entities were flagged, by which detectors, in which case."""
    truth_path = work / "truth.csv"
    generator.generate(work / "truth.db", truth_path)
    state = pipeline.run_all(work / "run.db")
    planted: dict[str, list[str]] = defaultdict(list)
    for row in csv.DictReader(truth_path.open(encoding="utf-8")):
        planted[row["scenario"]].append(row["entity_id"])
    out = {}
    for scenario, entities in planted.items():
        by_detector: dict[str, set[str]] = defaultdict(set)
        cases = set()
        for entity in entities:
            for finding in state.findings:
                if mentions(finding, entity):
                    by_detector[finding["detector"]].add(entity)
            for case in state.cases:
                if entity in case.entity_ids or entity in case.flagged_claim_ids or entity in case.affected_members:
                    cases.add(case.case_id)
        out[scenario] = {
            "planted": len(entities), "flagged": len(set().union(*by_detector.values())) if by_detector else 0,
            "detectors": {d: len(e) for d, e in sorted(by_detector.items())}, "cases": sorted(cases),
            "bands": sorted({c.investigation_band for c in state.cases if c.primary_entity in entities and c.investigation_band}),
        }  # fmt: skip
    return {"scenarios": out, "metrics": state.metrics, "cases": len(state.cases), "findings": len(state.findings)}


def markdown(result: dict) -> str:
    lines = ["| Planted scenario | Planted | Flagged | Caught by | Case |", "|---|---|---|---|---|"]
    for scenario, info in result["scenarios"].items():
        label = LABELS.get(scenario, scenario)
        detectors = ", ".join(f"{d} ({n})" for d, n in info["detectors"].items()) or "none"
        if scenario in HONEST:
            ok = "yes" if info["flagged"] == 0 else "NO"
            lines.append(f"| {label} | {info['planted']} | {info['flagged']} | none (correct: {ok}) | none |")
            continue
        band = f"; 30-day band {'/'.join(info['bands'])}" if info["bands"] else ""
        cases = ", ".join(info["cases"][:3]) + (" …" if len(info["cases"]) > 3 else "")
        lines.append(f"| {label} | {info['planted']} | {info['flagged']} | {detectors}{band} | {cases or 'none'} |")
    return "\n".join(lines)


def main() -> int:
    with tempfile.TemporaryDirectory() as folder:
        result = analyse(Path(folder))
    print(markdown(result))
    m = result["metrics"]
    print("\nModel-only metrics (time-split, last cut-off):")
    for key in ("precision_at_5", "precision_at_10", "recall", "pr_auc", "base_rate"):
        print(f"  {key}: {m.get(key)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
