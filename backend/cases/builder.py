"""Case builder: one case per ring, then one per remaining provider with at least one finding.

A case groups every finding about the same subject so a human reviewer opens one file, not
many alerts. Cases only recommend: the system never denies a claim or blocks a payment.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path

from backend.detect.engine import load_findings

log = logging.getLogger("claimx.cases")
DB_PATH = Path(__file__).resolve().parents[1] / "claimx.db"

STATUS_AWAITING = "Awaiting human review"
RULES, ANOMALY, GRAPH = "rules", "anomaly", "graph"
DETECTOR_GROUPS = (RULES, ANOMALY, GRAPH)  # evidence factor = detectors fired / 3
SEVERITY_ORDER = {"low": 0, "medium": 1, "high": 2, "critical": 3}


def detector_group(detector: str) -> str:
    return {"anomaly": ANOMALY, "ring": GRAPH}.get(detector, RULES)


@dataclass
class Case:
    case_id: str
    case_type: str  # "ring" | "provider"
    primary_entity: str
    entity_ids: list[str]
    findings: list[dict] = field(default_factory=list)
    flagged_claim_ids: list[str] = field(default_factory=list)
    flagged_amount: int = 0
    affected_members: list[str] = field(default_factory=list)
    detectors_fired: list[str] = field(default_factory=list)
    rule_score: float = 0.0
    anomaly_score: float = 0.0
    graph_score: float = 0.0
    investigation_risk: float | None = None  # model probability; None when not available
    investigation_band: str | None = None
    band_source: str | None = None
    worst_severity: str = "low"
    summary: str = ""
    status: str = STATUS_AWAITING

    @property
    def finding_ids(self) -> list[str]:
        return [f["finding_id"] for f in self.findings]


def load_investigation_risk(db_path: str | Path) -> dict[str, tuple[float, str, str]]:
    """provider -> (investigation_risk, band, band_source); empty (and logged) when missing."""
    con = sqlite3.connect(db_path)
    try:
        rows = con.execute(
            "SELECT provider_id, investigation_risk, risk_band, band_source FROM investigation_risk "
            "WHERE horizon_days = 30"  # case priority and ranking use the 30-day window
        ).fetchall()
    except sqlite3.OperationalError:
        log.warning("Insufficient data: no investigation_risk table; case risk uses 3 scores")
        return {}
    finally:
        con.close()
    return {p: (r, b, s) for p, r, b, s in rows}


def _providers_for(finding: dict, provider_of: dict[str, str]) -> list[str]:
    """Providers a non-ring finding belongs to: its entity, or who billed most of its evidence."""
    if finding["entity_id"].startswith("PRV-"):
        return [finding["entity_id"]]
    counts = Counter(provider_of[e] for e in finding["evidence_ids"] if e in provider_of)
    if not counts:
        log.info("finding %s has no provider-linked evidence; skipped", finding["finding_id"])
        return []
    top = max(counts.values())
    return sorted(p for p, n in counts.items() if n == top)


def _finish(case: Case, claim_info: dict[str, tuple[str, int]]) -> Case:
    """Fill the derived fields once all findings are attached."""
    findings = sorted(case.findings, key=lambda f: f["finding_id"])
    case.findings = findings
    flagged = {
        e
        for f in findings
        if f["detector"] != "anomaly"  # anomaly cites a provider's whole book, not specific claims
        for e in f["evidence_ids"]
        if e in claim_info
    }
    case.flagged_claim_ids = sorted(flagged)
    case.flagged_amount = sum(claim_info[c][1] for c in flagged)
    case.affected_members = sorted({claim_info[c][0] for c in flagged})
    groups = {detector_group(f["detector"]) for f in findings}
    case.detectors_fired = [g for g in DETECTOR_GROUPS if g in groups]

    def best(group: str) -> float:
        return max((f["score"] for f in findings if detector_group(f["detector"]) == group), default=0.0)

    case.rule_score, case.anomaly_score, case.graph_score = best(RULES), best(ANOMALY), best(GRAPH)
    case.worst_severity = max((f["severity"] for f in findings), key=SEVERITY_ORDER.__getitem__)
    case.summary = max(findings, key=lambda f: (f["score"], f["finding_id"]))["reason"]
    return case


def build_cases(db_path: str | Path = DB_PATH) -> list[Case]:
    """Build cases from the findings table. IDs are CASE-0001+ in a stable build order."""
    con = sqlite3.connect(db_path)
    try:
        claim_rows = con.execute(
            "SELECT claim_id, provider_id, member_id, billed_amount FROM claims"
        ).fetchall()
        try:
            rings = con.execute(
                "SELECT ring_id, nodes FROM communities WHERE ring_id IS NOT NULL ORDER BY ring_id"
            ).fetchall()
        except sqlite3.OperationalError:
            log.warning("Insufficient data: no communities table; no ring cases")
            rings = []
    finally:
        con.close()
    try:
        findings = load_findings(db_path)
    except sqlite3.OperationalError:
        log.warning("Insufficient data: no findings table; no cases built")
        return []

    provider_of = {c: p for c, p, _, _ in claim_rows}
    claim_info = {c: (m, a) for c, _, m, a in claim_rows}

    ring_nodes = {rid: json.loads(nodes) for rid, nodes in rings}
    provider_ring = {
        n: rid for rid, nodes in ring_nodes.items() for n in nodes if n.startswith("PRV-")
    }
    buckets: dict[str, Case] = {}
    for rid in sorted(ring_nodes):
        buckets[rid] = Case("", "ring", rid, ring_nodes[rid])

    for f in findings:
        if f["detector"] == "ring":
            if f["entity_id"] in buckets:
                buckets[f["entity_id"]].findings.append(f)
            else:
                log.info("ring finding %s has no community; skipped", f["entity_id"])
            continue
        for provider in _providers_for(f, provider_of):
            key = provider_ring.get(provider, provider)
            case = buckets.setdefault(key, Case("", "provider", provider, [provider]))
            if f not in case.findings:
                case.findings.append(f)

    risk_map = load_investigation_risk(db_path)
    ordered = [buckets[k] for k in sorted(buckets, key=lambda k: (k.startswith("PRV-"), k)) if buckets[k].findings]
    for i, case in enumerate(ordered, 1):
        case.case_id = f"CASE-{i:04d}"
        _finish(case, claim_info)
        scored = [(risk_map[n], n) for n in case.entity_ids if n in risk_map]
        if scored:  # a ring takes its highest-risk provider
            (risk, band, source), _ = max(scored, key=lambda t: (t[0][0], t[1]))
            case.investigation_risk, case.investigation_band, case.band_source = risk, band, source
    return ordered
