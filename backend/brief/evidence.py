"""Evidence pack: everything a brief may say about one case, keyed so each statement can cite it.

The pack is built from stored tables only. It never decides anything: it gathers findings
(E1, E2, ...), a dated timeline, network context, the model's investigation risk, a computed
confidence level, rule-based limitations and a recommended human-review action.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import asdict, dataclass, field
from datetime import date
from pathlib import Path

from backend.cases.builder import DETECTOR_GROUPS, SEVERITY_ORDER, detector_group

DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"
DEFAULT_HORIZON = 30
MIN_HISTORY_DAYS = 60
SAMPLE_CLAIMS = 3  # claim IDs quoted in the brief text; the pack keeps them all

SYNTHETIC_LIMITATION = (
    "Synthetic data: all records are simulated and findings do not describe real people or claims."
)
SINGLE_DETECTOR_LIMITATION = (
    "Single detector: only one detector group flagged this case, so the pattern is not corroborated."
)
SHORT_HISTORY_LIMITATION = (
    "Short history: at least one provider has under 60 days of claim history, "
    "so trends and the prediction are unreliable."
)
NO_PREDICTION_LIMITATION = (
    "Prediction unavailable: Insufficient data for an investigation-risk estimate for this case."
)


class CaseNotFoundError(KeyError):
    """Raised when the requested case does not exist."""


@dataclass
class EvidenceItem:
    key: str  # E1, E2, ...
    finding_id: str
    detector: str
    entity_id: str
    severity: str
    score: float
    reason: str
    scope: str  # "claim-specific" or "provider-level"
    claim_ids: list[str] = field(default_factory=list)
    other_ids: list[str] = field(default_factory=list)  # stays and prior investigations


@dataclass
class TimelineEntry:
    date: str
    end_date: str
    kind: str
    description: str
    evidence_keys: list[str]
    count: int = 0
    amount: int = 0


@dataclass
class Pack:
    case_id: str
    case_type: str
    primary_entity: str
    entity_ids: list[str]
    rank: int
    priority: float
    queue: str
    status: str
    horizon_days: int
    flagged_amount: int
    n_members: int
    detectors_fired: list[str]
    worst_severity: str
    evidence: list[EvidenceItem]
    timeline: list[TimelineEntry]
    network: dict
    prediction: dict
    confidence: dict
    limitations: list[str]
    recommended_action: dict

    @property
    def keys(self) -> list[str]:
        return [e.key for e in self.evidence]

    def to_dict(self) -> dict:
        return asdict(self)

    def to_prompt_dict(self) -> dict:
        """The same pack with long claim lists shortened to a count and a sample."""
        data = self.to_dict()
        for item in data["evidence"]:
            ids = item["claim_ids"]
            item["claim_count"] = len(ids)
            item["claim_ids"] = ids[:SAMPLE_CLAIMS]
        return data


# ---------------------------------------------------------------- rule-based parts


def compute_confidence(
    n_detectors: int, band: str | None, min_history_days: int | None
) -> dict:
    """High = 3/3 detectors and a High prediction; Medium = 2/3; Low = 1/3 or short history."""
    reasons = [f"{n_detectors} of {len(DETECTOR_GROUPS)} detector groups fired"]
    reasons.append(f"prediction band {band}" if band else "no prediction available")
    if min_history_days is not None and min_history_days < MIN_HISTORY_DAYS:
        reasons.append(f"only {min_history_days} days of history (under {MIN_HISTORY_DAYS})")
        return {"level": "Low", "reasons": reasons}
    if n_detectors >= len(DETECTOR_GROUPS) and band == "High":
        level = "High"
    elif n_detectors >= 2:
        level = "Medium"
    else:
        level = "Low"
    return {"level": level, "reasons": reasons}


def compute_limitations(
    n_detectors: int, min_history_days: int | None, prediction_available: bool
) -> list[str]:
    limitations = [SYNTHETIC_LIMITATION]
    if n_detectors <= 1:
        limitations.append(SINGLE_DETECTOR_LIMITATION)
    if min_history_days is not None and min_history_days < MIN_HISTORY_DAYS:
        limitations.append(SHORT_HISTORY_LIMITATION)
    if not prediction_available:
        limitations.append(NO_PREDICTION_LIMITATION)
    return limitations


def recommend_action(worst_severity: str, n_detectors: int) -> dict:
    """Advisory next step by severity and evidence breadth. It never denies or blocks anything."""
    high = SEVERITY_ORDER.get(worst_severity, 0) >= SEVERITY_ORDER["high"]
    medium = worst_severity == "medium"
    corroborated = n_detectors >= 2
    if high and corroborated:
        tier, text = "full-review", (
            "Assign an investigator for a full review soon: verify the cited claims against source "
            "records, confirm the network and ownership links, and document the findings."
        )
    elif high:
        tier, text = "verify", (
            "Assign an investigator to verify the flagged claims against source records and look "
            "for corroborating evidence, because the signal rests on a single detector group."
        )
    elif medium and corroborated:
        tier, text = "scheduled-review", (
            "Schedule a documented review of the cited evidence and confirm the pattern persists "
            "before any escalation."
        )
    elif medium:
        tier, text = "routine-review", (
            "Add to the routine review queue and check the flagged claims for a benign "
            "explanation, such as clinical need or data entry."
        )
    else:
        tier, text = "monitor", "Monitor the entity; no action is needed unless new findings appear."
    text += " No claim should be denied and no payment blocked on the basis of this brief alone."
    return {"tier": tier, "text": text}


# ---------------------------------------------------------------- data access


def _chunks(items: list[str], size: int = 500):
    for i in range(0, len(items), size):
        yield items[i : i + size]


def _rows(con: sqlite3.Connection, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
    try:
        return con.execute(sql, params).fetchall()
    except sqlite3.OperationalError:
        return []


def _claims_by_id(con: sqlite3.Connection, claim_ids: list[str]) -> dict[str, sqlite3.Row]:
    found: dict[str, sqlite3.Row] = {}
    for chunk in _chunks(claim_ids):
        marks = ",".join("?" * len(chunk))
        for row in _rows(con, f"SELECT * FROM claims WHERE claim_id IN ({marks})", tuple(chunk)):
            found[row["claim_id"]] = row
    return found


def _evidence(con: sqlite3.Connection, finding_ids: list[str]) -> list[EvidenceItem]:
    marks = ",".join("?" * len(finding_ids))
    rows = _rows(con, f"SELECT * FROM findings WHERE finding_id IN ({marks})", tuple(finding_ids))
    rows.sort(
        key=lambda r: (-SEVERITY_ORDER.get(r["severity"], 0), -r["score"], r["detector"], r["finding_id"])
    )
    items = []
    for i, r in enumerate(rows, 1):
        ids = json.loads(r["evidence_ids"])
        items.append(
            EvidenceItem(
                key=f"E{i}", finding_id=r["finding_id"], detector=r["detector"],
                entity_id=r["entity_id"], severity=r["severity"], score=r["score"],
                reason=r["reason"],
                scope="provider-level" if r["detector"] == "anomaly" else "claim-specific",
                claim_ids=[x for x in ids if x.startswith("CLM-")],
                other_ids=[x for x in ids if not x.startswith("CLM-")],
            )
        )  # fmt: skip
    return items


def _timeline(con: sqlite3.Connection, evidence: list[EvidenceItem]) -> list[TimelineEntry]:
    """Dated events built only from evidence IDs, so every entry cites a finding."""
    entries: list[TimelineEntry] = []
    for item in evidence:
        if item.scope == "claim-specific" and item.claim_ids:
            claims = _claims_by_id(con, item.claim_ids)
            by_month: dict[str, list[sqlite3.Row]] = {}
            for c in claims.values():
                by_month.setdefault(c["service_date"][:7], []).append(c)
            for month in sorted(by_month):
                group = by_month[month]
                days = sorted(c["service_date"] for c in group)
                amount = sum(c["billed_amount"] for c in group)
                entries.append(
                    TimelineEntry(
                        date=days[0], end_date=days[-1], kind=f"{item.detector} claims",
                        description=f"{len(group)} {item.detector} claims worth Rs {amount:,} in {month}",
                        evidence_keys=[item.key], count=len(group), amount=amount,
                    )
                )  # fmt: skip
        for other in item.other_ids:
            if other.startswith("STAY-"):
                for s in _rows(con, "SELECT * FROM inpatient_stays WHERE stay_id = ?", (other,)):
                    entries.append(
                        TimelineEntry(
                            date=s["admit_date"], end_date=s["discharge_date"], kind="inpatient stay",
                            description=f"Inpatient stay {other} at {s['facility_id']} for {s['member_id']}",
                            evidence_keys=[item.key], count=1,
                        )
                    )  # fmt: skip
            elif other.startswith("CASE-"):
                for inv in _rows(con, "SELECT * FROM investigations WHERE case_id = ?", (other,)):
                    entries.append(
                        TimelineEntry(
                            date=inv["opened_date"], end_date=inv["closed_date"],
                            kind="prior investigation",
                            description=(
                                f"Investigation {other} on {inv['entity_id']} opened, "
                                f"closed {inv['closed_date']} as {inv['outcome']}"
                            ),
                            evidence_keys=[item.key], count=1,
                        )
                    )  # fmt: skip
    entries.sort(key=lambda e: (e.date, e.kind, e.description))
    return entries


def _history_days(con: sqlite3.Connection, providers: list[str]) -> int | None:
    """Days of claim history for the shortest-history provider in the case."""
    if not providers:
        return None
    last = con.execute("SELECT MAX(service_date) FROM claims").fetchone()[0]
    if last is None:
        return None
    days = []
    for p in providers:
        first = con.execute(
            "SELECT MIN(service_date) FROM claims WHERE provider_id = ?", (p,)
        ).fetchone()[0]
        if first:
            days.append((date.fromisoformat(last) - date.fromisoformat(first)).days + 1)
    return min(days) if days else None


def _prediction(con: sqlite3.Connection, providers: list[str], horizon: int) -> dict:
    if not providers:
        return {"available": False, "reason": "Insufficient data: no provider in this case"}
    marks = ",".join("?" * len(providers))
    rows = _rows(
        con,
        f"SELECT * FROM investigation_risk WHERE horizon_days = ? AND provider_id IN ({marks})",
        (horizon, *providers),
    )
    if not rows:
        return {
            "available": False,
            "reason": f"Insufficient data: no {horizon}-day investigation_risk for these providers",
        }
    best = max(rows, key=lambda r: (r["investigation_risk"], r["provider_id"]))
    drivers = [
        f"{d['label']} {round(d['value'], 2):g}" for d in json.loads(best["drivers"])
    ]
    return {
        "available": True, "horizon_days": horizon, "provider_id": best["provider_id"],
        "investigation_risk": best["investigation_risk"], "risk_band": best["risk_band"],
        "band_source": best["band_source"], "band_reason": best["band_reason"],
        "top_drivers": drivers, "history_days": best["history_days"],
        "low_confidence": bool(best["low_confidence"]), "providers_scored": len(rows),
    }  # fmt: skip


def predictions_for(
    db_path: str | Path, entity_ids: list[str], horizons: tuple[int, ...] = (30, 60, 90)
) -> dict[int, dict]:
    """The case's investigation risk for each window, each one 'available' or an Insufficient-data reason."""
    providers = [e for e in entity_ids if e.startswith("PRV-")]
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        return {h: _prediction(con, providers, h) for h in horizons}
    except sqlite3.OperationalError:  # no investigation_risk table at all
        return {h: {"available": False, "reason": "Insufficient data: no investigation_risk table"} for h in horizons}
    finally:
        con.close()


def _network(con: sqlite3.Connection, case: sqlite3.Row, entities: list[str]) -> dict:
    providers = {r["provider_id"]: r for r in _rows(con, "SELECT * FROM providers")}
    facilities = {r["facility_id"]: r for r in _rows(con, "SELECT * FROM facilities")}
    owners = {r["owner_id"]: r for r in _rows(con, "SELECT * FROM owners")}
    details = []
    for e in entities:
        if e in providers:
            p = providers[e]
            details.append({"id": e, "type": "provider", "specialty": p["specialty"],
                            "city": p["city"], "owner": p["owner_id"]})  # fmt: skip
        elif e in facilities:
            f = facilities[e]
            details.append({"id": e, "type": "facility", "facility_type": f["type"],
                            "city": f["city"], "owner": f["owner_id"]})  # fmt: skip
        elif e in owners:
            details.append({"id": e, "type": "owner", "related_to": owners[e]["related_to"]})
        else:
            details.append({"id": e, "type": "unknown"})
    owner_ids = sorted(
        {d["id"] for d in details if d["type"] == "owner"}
        | {d["owner"] for d in details if d.get("owner")}
    )
    owner_info = []
    for o in owner_ids:
        owns = sorted(d["id"] for d in details if d.get("owner") == o)
        related = owners[o]["related_to"] if o in owners else None
        owner_info.append({"owner_id": o, "owns": owns, "related_to": related})
    provider_ids = [e for e in entities if e in providers]
    pairs = []
    if provider_ids:
        marks = ",".join("?" * len(provider_ids))
        rows = _rows(
            con, f"SELECT * FROM referral_pairs WHERE referrer IN ({marks})", tuple(provider_ids)
        )
        if case["case_type"] == "ring":
            rows = [r for r in rows if r["receiver"] in entities]
        else:
            rows = [r for r in rows if r["flagged"] or r["self_referral"]]
        rows.sort(key=lambda r: (-r["flagged"], -r["shared"], r["referrer"], r["receiver"]))
        pairs = [
            {"referrer": r["referrer"], "receiver": r["receiver"], "shared_members": r["shared"],
             "jaccard": r["jaccard"], "flagged": bool(r["flagged"]),
             "self_referral": bool(r["self_referral"])}
            for r in rows[:6]
        ]  # fmt: skip
    return {
        "entities": details, "owners": owner_info, "referral_pairs": pairs,
        "member_count": case["n_members"],
    }


# ---------------------------------------------------------------- the pack


def build_pack(
    case_id: str, horizon: int = DEFAULT_HORIZON, db_path: str | Path = DB_PATH
) -> Pack:
    con = sqlite3.connect(db_path)
    con.row_factory = sqlite3.Row
    try:
        case = con.execute("SELECT * FROM cases WHERE case_id = ?", (case_id,)).fetchone()
        if case is None:
            raise CaseNotFoundError(case_id)
        entities = json.loads(case["entity_ids"])
        evidence = _evidence(con, json.loads(case["finding_ids"]))
        providers = [e for e in entities if e.startswith("PRV-")]
        prediction = _prediction(con, providers, horizon)
        history = _history_days(con, providers)
        fired = json.loads(case["detectors_fired"])
        worst = max(
            (e.severity for e in evidence), key=lambda s: SEVERITY_ORDER.get(s, 0), default="low"
        )
        band = prediction.get("risk_band") if prediction["available"] else None
        return Pack(
            case_id=case["case_id"], case_type=case["case_type"],
            primary_entity=case["primary_entity"], entity_ids=entities, rank=case["rank"],
            priority=case["priority"], queue=case["queue"], status=case["status"],
            horizon_days=horizon, flagged_amount=case["flagged_amount"],
            n_members=case["n_members"], detectors_fired=fired, worst_severity=worst,
            evidence=evidence, timeline=_timeline(con, evidence),
            network=_network(con, case, entities), prediction=prediction,
            confidence=compute_confidence(len(fired), band, history),
            limitations=compute_limitations(len(fired), history, prediction["available"]),
            recommended_action=recommend_action(worst, len(fired)),
        )  # fmt: skip
    finally:
        con.close()


__all__ = [
    "CaseNotFoundError",
    "EvidenceItem",
    "Pack",
    "TimelineEntry",
    "build_pack",
    "compute_confidence",
    "compute_limitations",
    "detector_group",
    "predictions_for",
    "recommend_action",
]  # fmt: skip
