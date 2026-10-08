"""ClaimShield Nexus API.

The pipeline runs once at startup and its results are served from memory. Reviewers record
decisions through the API; the system only recommends, and every decision is written to an
append-only audit log. No endpoint denies a claim or blocks a payment.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import Depends, FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware

from backend import pipeline
from backend.api import views
from backend.api.schemas import (
    STATUS_BY_ACTION,
    AuditEntry,
    BriefOut,
    CaseDetail,
    DecisionIn,
    DecisionOut,
    EvidenceRows,
    GraphOut,
    HealthOut,
    OverrideIn,
    OverviewOut,
    PrewarmItem,
    PrewarmOut,
    PriorityOverrideOut,
    QueueOut,
    StageOut,
)
from backend.audit import AUDIT_PATH, AuditLog
from backend.brief.generate import INTERACTIVE_WAIT_CAP, PREWARM_WAIT_CAP, PROVIDERS, generate_brief
from backend.pipeline import PipelineError, PipelineState

log = logging.getLogger("claimshield.api")
BACKEND_DIR = Path(__file__).resolve().parents[1]
VITE_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
DEFAULT_CAPACITY_HOURS = 40.0
PREWARM_PAUSE_SECONDS = 2.0  # between prewarm calls that reached the LLM, to respect rate limits
RETRY_AFTER_SECONDS = 60.0  # how long a failed LLM attempt's template is reused before trying again

Runner = Callable[[Path], PipelineState]


class Runtime:
    """Everything the endpoints share: the served state, the audit log and the caches."""

    def __init__(self, data_dir: Path, audit_path: Path, runner: Runner):
        self.paths = [data_dir / "claimshield.db", data_dir / "claimshield.alt.db"]
        self.active = 0  # index of the database file the current state was built from
        self.state: PipelineState | None = None
        self.error: str | None = None
        self.audit = AuditLog(audit_path)
        self.decisions: dict[str, dict] = self.audit.latest_decisions()
        self.overrides: dict[str, dict] = self.audit.latest_overrides()
        # (state, case, horizon) -> (brief, expiry); an expiry of None means keep until the next rerun
        self.briefs: dict[tuple[str, str, int], tuple[BriefOut, float | None]] = {}
        self.brief_locks: dict[tuple[str, str, int], threading.Lock] = {}
        self.brief_guard = threading.Lock()
        self.prewarm_lock = threading.Lock()
        self.runner = runner
        self.rerun_lock = threading.Lock()
        self.decision_lock = threading.Lock()

    def lock_for(self, key: tuple[str, str, int]) -> threading.Lock:
        """One lock per brief, so simultaneous requests for it make a single LLM call."""
        with self.brief_guard:
            return self.brief_locks.setdefault(key, threading.Lock())

    def load(self, index: int, trigger: str) -> PipelineState:
        """Run the pipeline into one of the two database files and swap it in atomically."""
        try:
            state = self.runner(self.paths[index])
        except PipelineError as exc:
            self.error = str(exc)
            self.audit.append("pipeline_run", details={"trigger": trigger, "status": "error",
                                                       "error": str(exc)})  # fmt: skip
            raise
        self.state, self.active, self.error = state, index, None
        self.briefs.clear()
        self.brief_locks.clear()
        self.audit.append(
            "pipeline_run",
            details={"trigger": trigger, "status": state.status,
                     "total_seconds": state.total_seconds,
                     "stages": {t.name: t.seconds for t in state.timings}},
        )  # fmt: skip
        return state


def get_runtime(request: Request) -> Runtime:
    return request.app.state.runtime


def get_state(runtime: Annotated[Runtime, Depends(get_runtime)]) -> PipelineState:
    if runtime.state is None:
        raise HTTPException(503, "The pipeline is not ready yet. Check /health.")
    return runtime.state


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
StateDep = Annotated[PipelineState, Depends(get_state)]


def _health(runtime: Runtime) -> HealthOut:
    state = runtime.state
    if state is None:
        return HealthOut(status="error" if runtime.error else "ok", ready=False)
    return HealthOut(
        status=state.status, ready=True, started_at=state.started_at,
        finished_at=state.finished_at, total_seconds=state.total_seconds,
        stages=[StageOut(name=t.name, seconds=t.seconds, status=t.status, error=t.error)
                for t in state.timings],
    )  # fmt: skip


def create_app(
    *,
    run_on_startup: bool = True,
    data_dir: Path = BACKEND_DIR,
    audit_path: Path | None = None,
    team_hours: float = DEFAULT_CAPACITY_HOURS,
    runner: Runner | None = None,
) -> FastAPI:
    # CLAIMSHIELD_AUDIT_PATH lets a demo or test use its own audit file
    audit_file = Path(audit_path or os.environ.get("CLAIMSHIELD_AUDIT_PATH") or AUDIT_PATH)
    runtime = Runtime(
        Path(data_dir), audit_file, runner or (lambda path: pipeline.run_all(path, team_hours))
    )

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        if run_on_startup:
            try:
                runtime.load(0, "startup")
            except PipelineError:
                log.exception("startup pipeline failed; the API will answer 503 until a rerun")
        yield

    app = FastAPI(
        title="ClaimShield Nexus API",
        description="Synthetic-data FWA review workbench. The system recommends; humans decide.",
        lifespan=lifespan,
    )
    app.state.runtime = runtime
    app.add_middleware(
        CORSMiddleware, allow_origins=VITE_ORIGINS, allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["*"],
    )  # fmt: skip

    @app.get("/health", response_model=HealthOut)
    def health(rt: RuntimeDep) -> HealthOut:
        """Server status and how long each pipeline stage took."""
        return _health(rt)

    @app.get("/overview", response_model=OverviewOut)
    def overview(rt: RuntimeDep, state: StateDep):
        """Headline numbers: claims, findings, cases, dollars at risk, findings per rule."""
        return views.build_overview(state, rt.decisions)

    @app.get("/queue", response_model=QueueOut)
    def queue(
        rt: RuntimeDep,
        state: StateDep,
        capacity: Annotated[float, Query(ge=0, le=1000, description="Team hours")] = (
            DEFAULT_CAPACITY_HOURS
        ),
        weights: Annotated[
            str | None,
            Query(description="risk:0.3,dollars:0.25,impact:0.15,severity:0.15,evidence:0.15"),
        ] = None,
        include_decided: Annotated[
            bool, Query(description="Also list cases a reviewer has decided")
        ] = False,
    ):
        """Cases re-ranked with the given weights and scheduled against team capacity."""
        return views.build_queue(
            state, rt.decisions, capacity, views.parse_weights(weights), include_decided,
            rt.overrides,
        )

    def make_brief(
        rt: Runtime,
        state: PipelineState,
        case_id: str,
        horizon: int,
        *,
        wait_cap: float = INTERACTIVE_WAIT_CAP,
        retry_failed: bool = False,
    ) -> BriefOut:
        """One brief per case and run: memory first, then the stored brief, then the LLM.
        Simultaneous requests share a lock, so they make one LLM call between them."""
        key = (state.finished_at, case_id, horizon)
        with rt.lock_for(key):
            cached = rt.briefs.get(key)
            if cached and (cached[1] is None or (not retry_failed and cached[1] > time.monotonic())):
                return cached[0].model_copy(update={"cached": True, "attempts": 0})
            brief = generate_brief(
                case_id, horizon, state.db_path, cache=rt.audit, wait_cap=wait_cap,
                record=lambda row: rt.audit.record_llm_request(**row),
            )
            label = PROVIDERS[brief.provider].label if brief.provider in PROVIDERS else "Template"
            out = BriefOut(
                case_id=case_id, horizon_days=horizon, source=brief.source,
                fallback_reason=brief.fallback_reason, model=brief.model, provider=brief.provider,
                provider_label=label, masked=brief.masked, cached=brief.cached,
                attempts=brief.attempts, brief=brief.text,
            )  # fmt: skip
            expires = time.monotonic() + RETRY_AFTER_SECONDS if brief.retryable else None
            rt.briefs[key] = (out, expires)
            rt.audit.append("brief", case_id=case_id,
                            details={"horizon": horizon, "source": brief.source,
                                     "provider": brief.provider, "masked": brief.masked,
                                     "cached": brief.cached, "attempts": brief.attempts,
                                     "fallback_reason": brief.fallback_reason})  # fmt: skip
            return out

    def find_case(state: PipelineState, case_id: str):
        case = state.case(case_id)
        if case is None:
            raise HTTPException(404, f"Unknown case {case_id}")
        return case

    @app.get("/cases/{case_id}", response_model=CaseDetail)
    def case_detail(case_id: str, rt: RuntimeDep, state: StateDep):
        """One case: findings with reasons and evidence IDs, timeline, prediction, decisions."""
        case = find_case(state, case_id)
        events = rt.audit.list(1000, case_id)
        history = [e for e in events if e["event_type"] == "decision"]
        changes = [e for e in events if e["event_type"] == "priority_override"]
        return views.build_case_detail(state, case, rt.decisions, history, rt.overrides, changes)

    @app.get("/cases/{case_id}/evidence/{key}", response_model=EvidenceRows)
    def case_evidence(
        case_id: str,
        key: str,
        state: StateDep,
        limit: Annotated[int, Query(ge=1, le=500, description="Most claim rows to return")] = 200,
    ):
        """The claim rows (and linked records) behind one evidence item such as E1."""
        return views.build_evidence_rows(state, find_case(state, case_id), key, limit)

    @app.get("/cases/{case_id}/graph", response_model=GraphOut)
    def case_graph(case_id: str, state: StateDep):
        """Network around the case, with a suspicious flag on nodes and links."""
        return views.build_case_graph(state, find_case(state, case_id))

    @app.get("/cases/{case_id}/brief", response_model=BriefOut)
    def case_brief(
        case_id: str,
        rt: RuntimeDep,
        state: StateDep,
        horizon: Annotated[int, Query(ge=1, le=365, description="Prediction horizon in days")] = 30,
    ):
        """Investigation brief: written by the LLM only if a key is set and the text validates."""
        find_case(state, case_id)
        return make_brief(rt, state, case_id, horizon)

    @app.post("/cases/{case_id}/decision", response_model=DecisionOut, status_code=201)
    def decide(case_id: str, body: DecisionIn, rt: RuntimeDep, state: StateDep):
        """Record a human reviewer's decision. A reason is required. Nothing is denied or blocked."""
        find_case(state, case_id)
        with rt.decision_lock:
            entry = rt.audit.append(
                "decision", case_id=case_id, action=body.action, reason=body.reason,
                reviewer=body.reviewer,
            )  # fmt: skip
            rt.decisions[case_id] = entry
        return DecisionOut(
            audit_id=entry["audit_id"], case_id=case_id, action=body.action, reason=body.reason,
            reviewer=body.reviewer, decided_at=entry["ts"], case_status=STATUS_BY_ACTION[body.action],
        )  # fmt: skip

    @app.post(
        "/cases/{case_id}/priority-override", response_model=PriorityOverrideOut, status_code=201
    )
    def override_priority(case_id: str, body: OverrideIn, rt: RuntimeDep, state: StateDep):
        """A reviewer changes a case's queue priority (or clears it). A reason is required."""
        find_case(state, case_id)
        standing = views.build_queue(
            state, rt.decisions, state.team_hours, views.ranking.Weights(), True, {}
        )
        ai_priority = next(
            i.ai_priority for i in standing.scheduled + standing.backlog if i.case_id == case_id
        )
        with rt.decision_lock:
            entry = rt.audit.append(
                "priority_override", case_id=case_id,
                action="set_priority" if body.priority is not None else "clear_priority",
                reason=body.reason, reviewer=body.reviewer,
                details={"priority": body.priority, "ai_priority": ai_priority},
            )
            if body.priority is not None:
                rt.overrides[case_id] = entry
            else:
                rt.overrides.pop(case_id, None)
        return PriorityOverrideOut(
            audit_id=entry["audit_id"], case_id=case_id, ai_priority=ai_priority,
            priority=body.priority if body.priority is not None else ai_priority,
            override_active=body.priority is not None, reason=body.reason,
            reviewer=body.reviewer, ts=entry["ts"],
        )

    @app.get("/audit", response_model=list[AuditEntry])
    def audit(
        rt: RuntimeDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        case_id: Annotated[str | None, Query()] = None,
    ):
        """Decisions and pipeline runs, latest first."""
        return rt.audit.list(limit, case_id)

    @app.post("/admin/prewarm-briefs", response_model=PrewarmOut)
    def prewarm_briefs(
        rt: RuntimeDep,
        state: StateDep,
        top: Annotated[int, Query(ge=1, le=10, description="How many top-ranked cases")] = 5,
    ):
        """Write and store the briefs of the top cases of the default queue (open cases, default
        weights, reviewer overrides applied), pausing between calls that reached the LLM."""
        if not rt.prewarm_lock.acquire(blocking=False):
            raise HTTPException(409, "A prewarm is already in progress")
        started = time.monotonic()
        try:
            queue = views.build_queue(
                state, rt.decisions, DEFAULT_CAPACITY_HOURS, views.parse_weights(None), False,
                rt.overrides,
            )
            ranked = sorted(queue.scheduled + queue.backlog, key=lambda i: i.rank)[:top]
            items: list[PrewarmItem] = []
            for n, item in enumerate(ranked):
                out = make_brief(rt, state, item.case_id, 30, wait_cap=PREWARM_WAIT_CAP,
                                 retry_failed=True)  # fmt: skip
                items.append(PrewarmItem(
                    rank=item.rank, case_id=item.case_id, source=out.source,
                    provider_label=out.provider_label, cached=out.cached, attempts=out.attempts,
                    fallback_reason=out.fallback_reason,
                ))  # fmt: skip
                if out.attempts and n < len(ranked) - 1:
                    time.sleep(PREWARM_PAUSE_SECONDS)  # only after a call that used the provider
            return PrewarmOut(
                requested=top,
                generated=sum(1 for i in items if i.source == "llm" and not i.cached),
                already_cached=sum(1 for i in items if i.source == "llm" and i.cached),
                fell_back=sum(1 for i in items if i.source == "template"),
                llm_calls=sum(i.attempts for i in items),
                seconds=round(time.monotonic() - started, 1), items=items,
            )
        finally:
            rt.prewarm_lock.release()

    @app.post("/admin/rerun", response_model=HealthOut)
    def rerun(rt: RuntimeDep):
        """Rebuild everything. The current data keeps being served until the new run swaps in."""
        if not rt.rerun_lock.acquire(blocking=False):
            raise HTTPException(409, "A rerun is already in progress")
        try:
            try:
                rt.load(1 - rt.active, "rerun")
            except PipelineError as exc:
                raise HTTPException(500, f"Rerun failed; previous state kept ({exc})") from exc
        finally:
            rt.rerun_lock.release()
        return _health(rt)

    return app


app = create_app()
