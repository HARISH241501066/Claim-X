"""Pydantic response and request models for the ClaimShield API."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field, StringConstraints

ReviewAction = Literal[
    "escalate_for_investigation", "request_more_information", "monitor", "dismiss"
]
# What the case status reads after each human decision (nothing here denies or blocks anything)
STATUS_BY_ACTION: dict[str, str] = {
    "escalate_for_investigation": "Escalated for investigation",
    "request_more_information": "More information requested",
    "monitor": "Monitoring",
    "dismiss": "Dismissed by reviewer",
}
AWAITING = "Awaiting human review"


class StageOut(BaseModel):
    name: str
    seconds: float
    status: str
    error: str | None = None


class HealthOut(BaseModel):
    status: str  # ok | degraded | error
    ready: bool
    started_at: str | None = None
    finished_at: str | None = None
    total_seconds: float | None = None
    stages: list[StageOut] = []


class OverviewOut(BaseModel):
    claims: int
    findings: int
    cases: int
    dollars_at_risk: int
    findings_per_rule: dict[str, int]
    cases_awaiting_review: int
    cases_decided: int
    cases_scheduled: int
    cases_backlog: int
    scheduled_hours: float
    team_hours: float


class FactorsOut(BaseModel):
    risk: float
    dollars: float
    impact: float
    severity: float
    evidence: float


class OverrideOut(BaseModel):
    priority: float
    reason: str
    reviewer: str
    ts: str


class QueueItem(BaseModel):
    rank: int
    case_id: str
    title: str
    case_type: str
    primary_entity: str
    priority: float  # the priority used for ranking: the reviewer's override if one is set
    ai_priority: float
    override: OverrideOut | None = None
    factors: FactorsOut
    flagged_amount: int
    n_members: int
    detectors_fired: list[str]
    investigation_risk: float | None = None
    investigation_band: str | None = None
    effort_hours: float
    cumulative_hours: float
    queue: str
    status: str
    summary: str


class QueueOut(BaseModel):
    capacity_hours: float
    weights: dict[str, float]
    scheduled_hours: float
    scheduled: list[QueueItem]
    backlog: list[QueueItem]
    decided_excluded: int


class FindingOut(BaseModel):
    key: str | None = None  # E1, E2, ... as cited in the brief
    finding_id: str
    detector: str
    entity_id: str
    severity: str
    score: float
    reason: str
    evidence_ids: list[str]


class TimelineOut(BaseModel):
    date: str
    end_date: str
    kind: str
    description: str
    evidence_keys: list[str]
    count: int
    amount: int


class PredictionOut(BaseModel):
    available: bool
    reason: str | None = None
    horizon_days: int | None = None
    provider_id: str | None = None
    investigation_risk: float | None = None
    risk_band: str | None = None
    band_source: str | None = None
    band_reason: str | None = None
    top_drivers: list[str] = []
    history_days: int | None = None
    low_confidence: bool | None = None


class AuditEntry(BaseModel):
    audit_id: int
    ts: str
    event_type: str
    case_id: str | None = None
    action: str | None = None
    reason: str | None = None
    reviewer: str | None = None
    details: dict = {}


class RecommendedAction(BaseModel):
    tier: str
    text: str


class CaseDetail(BaseModel):
    case_id: str
    title: str
    case_type: str
    primary_entity: str
    entity_ids: list[str]
    rank: int
    priority: float
    ai_priority: float
    override: OverrideOut | None = None
    recommended_action: RecommendedAction | None = None
    overrides: list[AuditEntry] = []
    queue: str
    status: str
    flagged_amount: int
    affected_members: list[str]
    detectors_fired: list[str]
    summary: str
    findings: list[FindingOut]
    timeline: list[TimelineOut]
    prediction: PredictionOut
    confidence: dict
    limitations: list[str]
    decisions: list[AuditEntry]


class GraphNode(BaseModel):
    id: str
    type: str
    suspicious: bool
    label: str
    count: int | None = None  # members in a collapsed group


class GraphLink(BaseModel):
    source: str
    target: str
    type: str
    weight: float
    suspicious: bool


class GraphOut(BaseModel):
    case_id: str
    nodes: list[GraphNode]
    links: list[GraphLink]
    member_count: int
    members_collapsed: bool


class BriefOut(BaseModel):
    case_id: str
    horizon_days: int
    source: Literal["llm", "template"]
    fallback_reason: str | None = None
    model: str | None = None
    provider: str | None = None  # who wrote it: anthropic, xai or groq; null for the template
    provider_label: str = "Template"  # what the screen shows: Claude, Grok, Groq or Template
    masked: bool = False  # True when an LLM wrote it from masked data
    cached: bool = False
    brief: str


Reason = Annotated[str, StringConstraints(strip_whitespace=True, min_length=5, max_length=2000)]
Reviewer = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=100)]


class DecisionIn(BaseModel):
    action: ReviewAction
    reason: Reason = Field(description="Why the reviewer decided this; required, 5+ characters")
    reviewer: Reviewer = Field(description="Name or ID of the human reviewer")


class ClaimRow(BaseModel):
    claim_id: str
    service_date: str
    member_id: str
    provider_id: str
    facility_id: str
    referring_provider_id: str | None = None
    procedure_code: str
    code_level: int | None = None
    billed_amount: int
    claim_type: str


class LinkedRecord(BaseModel):
    id: str
    kind: str  # inpatient stay | prior investigation
    description: str


class EvidenceRows(BaseModel):
    case_id: str
    key: str
    finding_id: str
    detector: str
    scope: str  # claim-specific | provider-level
    total_claims: int
    shown: int
    claims: list[ClaimRow]
    linked_records: list[LinkedRecord]
    note: str | None = None


class OverrideIn(BaseModel):
    priority: Annotated[float, Field(ge=0, le=1)] | None = Field(
        description="New priority between 0 and 1, or null to clear the override"
    )
    reason: Reason = Field(description="Why the reviewer changed the priority; 5+ characters")
    reviewer: Reviewer = Field(description="Name or ID of the human reviewer")


class PriorityOverrideOut(BaseModel):
    audit_id: int
    case_id: str
    ai_priority: float
    priority: float
    override_active: bool
    reason: str
    reviewer: str
    ts: str


class DecisionOut(BaseModel):
    audit_id: int
    case_id: str
    action: ReviewAction
    reason: str
    reviewer: str
    decided_at: str
    case_status: str
