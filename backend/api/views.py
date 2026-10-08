"""Pure functions that turn the in-memory pipeline state into API response models."""

from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter
from dataclasses import asdict

import pandas as pd
from fastapi import HTTPException

from backend.api.schemas import (
    AWAITING,
    STATUS_BY_ACTION,
    AuditEntry,
    CaseDetail,
    ClaimRow,
    EvidenceRows,
    FactorsOut,
    FindingOut,
    GraphLink,
    GraphNode,
    GraphOut,
    LinkedRecord,
    OverrideOut,
    OverviewOut,
    PredictionOut,
    QueueItem,
    QueueOut,
    RecommendedAction,
    TimelineOut,
)
from backend.brief.evidence import predictions_for
from backend.cases import ranking
from backend.cases.builder import SEVERITY_ORDER, Case
from backend.detect import graph as graph_module
from backend.pipeline import PipelineState

WEIGHT_KEYS = ("risk", "dollars", "impact", "severity", "evidence")
GRAPH_PARTNER_LIMIT = 5  # referral partners shown around a single-provider case


TITLE_BY_DETECTOR = {
    "duplicate": "Duplicate billing",
    "upcoding": "Upcoding pattern",
    "unbundling": "Unbundled panel billing",
    "phantom": "Billing during inpatient stay",
    "impossible_timing": "Impossible timing",
    "utilization": "Excess utilization",
    "repeat_history": "Repeat investigation pattern",
    "anomaly": "Unusual provider profile",
    "ring": "Referral network",
}


def case_title(case: Case) -> str:
    """A short readable name from the case's most serious finding. It never says 'fraud'."""
    if case.case_type == "ring":  # the case is the network, whichever finding scores highest
        return f"{TITLE_BY_DETECTOR['ring']}: {case.primary_entity} ({len(case.entity_ids)} linked entities)"
    top = max(case.findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 0), f["score"]))
    return f"{TITLE_BY_DETECTOR.get(top['detector'], 'Flagged pattern')}: {case.primary_entity}"


def override_out(entry: dict | None) -> OverrideOut | None:
    if entry is None:
        return None
    return OverrideOut(
        priority=entry["details"]["priority"], reason=entry["reason"],
        reviewer=entry["reviewer"], ts=entry["ts"],
    )


def case_status(case_id: str, decisions: dict[str, dict]) -> str:
    decision = decisions.get(case_id)
    return STATUS_BY_ACTION[decision["action"]] if decision else AWAITING


# ---------------------------------------------------------------- queue


def parse_weights(text: str | None) -> ranking.Weights:
    """'risk:0.3,dollars:0.25,...' or a JSON object. Missing keys keep their defaults."""
    if not text or not text.strip():
        return ranking.Weights()
    raw = text.strip()
    try:
        if raw.startswith("{"):
            given = {str(k): float(v) for k, v in json.loads(raw).items()}
        else:
            given = {}
            for part in filter(None, (p.strip() for p in raw.split(","))):
                key, value = re.split(r"[:=]", part, maxsplit=1)
                given[key.strip()] = float(value)
    except (ValueError, TypeError, AttributeError) as exc:
        raise HTTPException(
            422, "weights must look like risk:0.3,dollars:0.25,impact:0.15,severity:0.15,evidence:0.15"
        ) from exc
    unknown = sorted(set(given) - set(WEIGHT_KEYS))
    if unknown:
        raise HTTPException(422, f"unknown weight(s) {unknown}; use {list(WEIGHT_KEYS)}")
    try:
        return ranking.Weights(**{**asdict(ranking.Weights()), **given})
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc


def apply_overrides(ranked: pd.DataFrame, overrides: dict[str, dict]) -> pd.DataFrame:
    """Keep the AI priority, and rank by the reviewer's priority where one is set."""
    ranked = ranked.assign(ai_priority=ranked.priority)
    known = set(ranked.case_id)
    mine = {cid: o["details"]["priority"] for cid, o in overrides.items() if cid in known}
    if not mine:
        return ranked
    ranked["priority"] = [
        mine.get(cid, p) for cid, p in zip(ranked.case_id, ranked.priority, strict=True)
    ]
    ranked = ranked.sort_values(
        ["priority", "case_id"], ascending=[False, True], ignore_index=True
    )
    ranked["rank"] = range(1, len(ranked) + 1)
    return ranked


def build_queue(
    state: PipelineState,
    decisions: dict[str, dict],
    capacity: float,
    weights: ranking.Weights,
    include_decided: bool,
    overrides: dict[str, dict] | None = None,
) -> QueueOut:
    overrides = overrides or {}
    open_cases = [c for c in state.cases if include_decided or c.case_id not in decisions]
    excluded = len(state.cases) - len(open_cases)
    items: list[QueueItem] = []
    if open_cases:
        ordered = apply_overrides(ranking.rank_cases(open_cases, weights), overrides)
        ranked = ranking.schedule(ordered, open_cases, capacity)
        by_id = {c.case_id: c for c in open_cases}
        for r in ranked.itertuples():
            c = by_id[r.case_id]
            items.append(
                QueueItem(
                    rank=int(r.rank), case_id=c.case_id, title=case_title(c),
                    case_type=c.case_type, primary_entity=c.primary_entity,
                    priority=float(r.priority), ai_priority=float(r.ai_priority),
                    override=override_out(overrides.get(c.case_id)),
                    factors=FactorsOut(risk=r.risk, dollars=r.dollars, impact=r.impact,
                                       severity=r.severity, evidence=r.evidence),
                    flagged_amount=c.flagged_amount, n_members=len(c.affected_members),
                    detectors_fired=c.detectors_fired,
                    investigation_risk=c.investigation_risk,
                    investigation_band=c.investigation_band,
                    effort_hours=float(r.effort_hours),
                    cumulative_hours=float(r.cumulative_hours), queue=r.queue,
                    status=case_status(c.case_id, decisions), summary=c.summary,
                )
            )
    scheduled = [i for i in items if i.queue == "scheduled"]
    return QueueOut(
        capacity_hours=capacity, weights=asdict(weights),
        scheduled_hours=sum(i.effort_hours for i in scheduled),
        scheduled=scheduled, backlog=[i for i in items if i.queue == "backlog"],
        decided_excluded=excluded,
    )  # fmt: skip


# ---------------------------------------------------------------- overview and case detail


def build_overview(state: PipelineState, decisions: dict[str, dict]) -> OverviewOut:
    ids = {c.case_id for c in state.cases}
    decided = len(ids & set(decisions))
    ranked = state.ranked
    scheduled = ranked[ranked.queue == "scheduled"] if len(ranked) else ranked
    return OverviewOut(
        claims=state.claims_count, findings=len(state.findings), cases=len(state.cases),
        dollars_at_risk=state.dollars_at_risk,
        findings_per_rule=dict(sorted(Counter(f["detector"] for f in state.findings).items())),
        cases_awaiting_review=len(ids) - decided, cases_decided=decided,
        cases_scheduled=len(scheduled), cases_backlog=len(ranked) - len(scheduled),
        scheduled_hours=float(scheduled.effort_hours.sum()) if len(scheduled) else 0.0,
        team_hours=state.team_hours,
    )  # fmt: skip


def _windows(state: PipelineState, case: Case, thirty: dict) -> dict[str, PredictionOut]:
    """The 30, 60 and 90-day estimates. Each is the model's own number; when a longer window comes
    out lower than the one before it, a note says the models are trained separately on little data."""
    found = predictions_for(state.db_path, case.entity_ids)
    if thirty.get("available"):
        found[30] = thirty  # the pack the pipeline built, so the two always agree
    out: dict[str, PredictionOut] = {}
    previous: dict | None = None
    for horizon in sorted(found):
        item = dict(found[horizon])
        both = item.get("available") and previous and previous.get("available")
        if both and item["investigation_risk"] < previous["investigation_risk"]:
            item["note"] = (
                f"Lower than the {previous['horizon_days']}-day estimate, although a longer window "
                "should not be less likely. The windows are separate models trained on little data."
            )
        out[str(horizon)] = PredictionOut(**item)
        previous = item
    return out


def build_case_detail(
    state: PipelineState,
    case: Case,
    decisions: dict[str, dict],
    history: list[dict],
    overrides: dict[str, dict] | None = None,
    override_history: list[dict] | None = None,
) -> CaseDetail:
    pack = state.packs.get(case.case_id)
    overrides = overrides or {}
    standing = build_queue(  # where the case sits under default weights, with reviewer overrides
        state, decisions, state.team_hours, ranking.Weights(), True, overrides
    )
    everything = standing.scheduled + standing.backlog
    item = next((i for i in everything if i.case_id == case.case_id), None)
    prediction = pack.prediction if pack else {"available": False, "reason": "Insufficient data"}
    windows = _windows(state, case, prediction)
    keys = {e.finding_id: e.key for e in pack.evidence} if pack else {}

    def order(finding: dict) -> tuple[bool, int]:
        key = keys.get(finding["finding_id"])
        return (key is None, int(key[1:]) if key else 0)

    fields = [name for name in FindingOut.model_fields if name != "key"]
    return CaseDetail(
        case_id=case.case_id, title=case_title(case), case_type=case.case_type,
        primary_entity=case.primary_entity, entity_ids=case.entity_ids,
        rank=item.rank if item else 0, priority=item.priority if item else 0.0,
        ai_priority=item.ai_priority if item else 0.0,
        override=override_out(overrides.get(case.case_id)),
        recommended_action=RecommendedAction(**pack.recommended_action) if pack else None,
        overrides=[AuditEntry(**e) for e in (override_history or [])],
        queue=item.queue if item else "backlog",
        status=case_status(case.case_id, decisions), flagged_amount=case.flagged_amount,
        affected_members=case.affected_members, detectors_fired=case.detectors_fired,
        summary=case.summary,
        findings=[
            FindingOut(key=keys.get(f["finding_id"]), **{k: f[k] for k in fields})
            for f in sorted(case.findings, key=order)
        ],
        timeline=[TimelineOut(**asdict(t)) for t in pack.timeline] if pack else [],
        prediction=PredictionOut(**prediction), predictions=windows,
        confidence=pack.confidence if pack else {}, limitations=pack.limitations if pack else [],
        decisions=[AuditEntry(**d) for d in history],
    )  # fmt: skip


# ---------------------------------------------------------------- graph


def _label(node: dict) -> str:
    kind = node["type"]
    if kind == "provider":
        return f"{node['id']} ({node.get('specialty', 'provider')})"
    if kind == "facility":
        return f"{node['id']} ({node.get('facility_type', 'facility')})"
    if kind == "member_group":
        return f"{node.get('count', 0)} members"
    return node["id"]


def build_case_graph(state: PipelineState, case: Case) -> GraphOut:
    g = state.graph
    if g is None:
        raise HTTPException(503, "Insufficient data: the graph is not available")
    pairs = state.pairs
    shown = list(case.entity_ids)
    suspicious_entities = set(case.entity_ids)
    if case.case_type != "ring" and len(pairs):
        hot = pairs[(pairs.referrer == case.primary_entity) & (pairs.flagged | pairs.self_referral)]
        for r in hot.sort_values(["shared", "receiver"], ascending=[False, True]).head(
            GRAPH_PARTNER_LIMIT
        ).itertuples():
            shown.append(r.receiver)
            suspicious_entities.add(r.receiver)
    for entity in list(shown):  # owners of everything shown
        if entity in g and g.nodes[entity]["type"] in ("provider", "facility"):
            shown += [v for _, v, d in g.out_edges(entity, data=True) if d["type"] == "owned_by"]
    shown = list(dict.fromkeys(shown))
    hot_pairs = (
        {(r.referrer, r.receiver) for r in pairs[pairs.flagged | pairs.self_referral].itertuples()}
        if len(pairs) else set()
    )  # fmt: skip
    affected = set(case.affected_members)

    sub = graph_module.subgraph(g, shown)
    entity_members: dict[str, set[str]] = {
        u: {v for _, v, d in g.out_edges(u, data=True) if g.nodes[v]["type"] == "member"}
        for u in shown
        if u in g and g.nodes[u]["type"] in ("provider", "facility")
    }
    group_hot = any(m & affected for m in entity_members.values())
    suspicious: dict[str, bool] = {}
    nodes = []
    for n in sub["nodes"]:
        if n["type"] == "member":
            flag = n["id"] in affected
        elif n["type"] == "member_group":
            flag = group_hot
        else:
            flag = n["id"] in suspicious_entities
        suspicious[n["id"]] = flag
        nodes.append(
            GraphNode(id=n["id"], type=n["type"], suspicious=flag, label=_label(n),
                      count=n.get("count"))
        )
    links = []
    for link in sub["links"]:
        s, t, kind = link["source"], link["target"], link["type"]
        if kind == "referred_to":
            flag = (s, t) in hot_pairs
        elif kind in ("owned_by", "related_to"):
            flag = suspicious.get(s, False) and suspicious.get(t, False)
        elif t == "MEMBERS":
            flag = suspicious.get(s, False) and bool(entity_members.get(s, set()) & affected)
        else:  # billed_for to an individual member
            flag = suspicious.get(s, False) and t in affected
        links.append(GraphLink(source=s, target=t, type=kind, weight=float(link["weight"]),
                               suspicious=flag))  # fmt: skip
    return GraphOut(
        case_id=case.case_id, nodes=nodes, links=links, member_count=sub["member_count"],
        members_collapsed=sub["members_collapsed"],
    )


# ---------------------------------------------------------------- evidence rows


def build_evidence_rows(state: PipelineState, case: Case, key: str, limit: int) -> EvidenceRows:
    """The claim rows behind one evidence item (E1, E2, ...) plus linked stays and investigations."""
    pack = state.packs.get(case.case_id)
    item = next((e for e in pack.evidence if e.key.upper() == key.upper()), None) if pack else None
    if item is None:
        raise HTTPException(404, f"Unknown evidence {key} for {case.case_id}")
    rows: list[ClaimRow] = []
    linked: list[LinkedRecord] = []
    con = sqlite3.connect(state.db_path)
    con.row_factory = sqlite3.Row
    try:
        for start in range(0, len(item.claim_ids), 500):
            chunk = item.claim_ids[start : start + 500]
            marks = ",".join("?" * len(chunk))
            cursor = con.execute(f"SELECT * FROM claims WHERE claim_id IN ({marks})", chunk)
            rows += [ClaimRow(**{k: r[k] for k in ClaimRow.model_fields}) for r in cursor]
        for other in item.other_ids:
            if other.startswith("STAY-"):
                for s in con.execute("SELECT * FROM inpatient_stays WHERE stay_id = ?", (other,)):
                    text = f"{s['member_id']} at {s['facility_id']}, {s['admit_date']} to {s['discharge_date']}"
                    linked.append(LinkedRecord(id=other, kind="inpatient stay", description=text))
            elif other.startswith("CASE-"):
                for i in con.execute("SELECT * FROM investigations WHERE case_id = ?", (other,)):
                    text = (
                        f"{i['entity_id']} opened {i['opened_date']}, closed {i['closed_date']} "
                        f"as {i['outcome']}"
                    )
                    linked.append(LinkedRecord(id=other, kind="prior investigation", description=text))
    finally:
        con.close()
    rows.sort(key=lambda r: (r.service_date, r.claim_id))
    note = None
    if item.scope == "provider-level":
        note = (
            f"Provider-level signal: these are all {len(rows)} claims of {item.entity_id}, "
            "not specific suspect claims."
        )
    elif len(rows) > limit:
        note = f"Showing the first {limit} of {len(rows)} claims."
    return EvidenceRows(
        case_id=case.case_id, key=item.key, finding_id=item.finding_id, detector=item.detector,
        scope=item.scope, total_claims=len(rows), shown=min(len(rows), limit),
        claims=rows[:limit], linked_records=linked, note=note,
    )


__all__ = [
    "build_case_detail", "build_case_graph", "build_evidence_rows", "build_overview",
    "build_queue", "case_status", "case_title", "parse_weights",
]  # fmt: skip
