"""Pure functions that turn the in-memory pipeline state into API response models."""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import asdict

from fastapi import HTTPException

from backend.api.schemas import (
    AWAITING,
    STATUS_BY_ACTION,
    AuditEntry,
    CaseDetail,
    FactorsOut,
    FindingOut,
    GraphLink,
    GraphNode,
    GraphOut,
    OverviewOut,
    PredictionOut,
    QueueItem,
    QueueOut,
    TimelineOut,
)
from backend.cases import ranking
from backend.cases.builder import Case
from backend.detect import graph as graph_module
from backend.pipeline import PipelineState

WEIGHT_KEYS = ("risk", "dollars", "impact", "severity", "evidence")
GRAPH_PARTNER_LIMIT = 5  # referral partners shown around a single-provider case


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


def build_queue(
    state: PipelineState,
    decisions: dict[str, dict],
    capacity: float,
    weights: ranking.Weights,
    include_decided: bool,
) -> QueueOut:
    open_cases = [c for c in state.cases if include_decided or c.case_id not in decisions]
    excluded = len(state.cases) - len(open_cases)
    items: list[QueueItem] = []
    if open_cases:
        ranked = ranking.schedule(ranking.rank_cases(open_cases, weights), open_cases, capacity)
        by_id = {c.case_id: c for c in open_cases}
        for r in ranked.itertuples():
            c = by_id[r.case_id]
            items.append(
                QueueItem(
                    rank=int(r.rank), case_id=c.case_id, case_type=c.case_type,
                    primary_entity=c.primary_entity, priority=float(r.priority),
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


def build_case_detail(
    state: PipelineState, case: Case, decisions: dict[str, dict], history: list[dict]
) -> CaseDetail:
    pack = state.packs.get(case.case_id)
    row = state.ranked[state.ranked.case_id == case.case_id]
    default = row.iloc[0] if len(row) else None
    prediction = pack.prediction if pack else {"available": False, "reason": "Insufficient data"}
    return CaseDetail(
        case_id=case.case_id, case_type=case.case_type, primary_entity=case.primary_entity,
        entity_ids=case.entity_ids, rank=int(default["rank"]) if default is not None else 0,
        priority=float(default["priority"]) if default is not None else 0.0,
        queue=str(default["queue"]) if default is not None else "backlog",
        status=case_status(case.case_id, decisions), flagged_amount=case.flagged_amount,
        affected_members=case.affected_members, detectors_fired=case.detectors_fired,
        summary=case.summary,
        findings=[FindingOut(**{k: f[k] for k in FindingOut.model_fields}) for f in case.findings],
        timeline=[TimelineOut(**asdict(t)) for t in pack.timeline] if pack else [],
        prediction=PredictionOut(**prediction),
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


__all__ = [
    "build_case_detail", "build_case_graph", "build_overview", "build_queue", "case_status",
    "parse_weights",
]  # fmt: skip
