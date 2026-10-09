"""ClaimShield Nexus API.

The pipeline runs once at startup and its results are served from memory. Everyone signs in; what
they can see and do depends on their role (admin, team lead, investigator) and unit, and the rules
are enforced here, not in the screens. Reviewers record decisions through the API; the system only
recommends, and every decision is written to an append-only audit log. No endpoint denies a claim
or blocks a payment.
"""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated, Literal

from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from backend import pipeline
from backend.access.routing import route_cases
from backend.access.seed import Seeder, ensure_seeded, seed_demo
from backend.access.store import AccessStore
from backend.api import access_routes, notifications, scope, views
from backend.api.deps import (
    AdminDep,
    UserDep,
    case_permission,
    get_runtime,
    get_state,
)  # fmt: skip
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
from backend.notify import alerts
from backend.notify.notifier import email_channel, load_config
from backend.notify.store import NotifyStore
from backend.pipeline import PipelineError, PipelineState

log = logging.getLogger("claimshield.api")
BACKEND_DIR = Path(__file__).resolve().parents[1]
VITE_ORIGINS = ["http://localhost:5173", "http://127.0.0.1:5173"]
# Vite moves to the next port (5174, 5175 ...) when 5173 is busy; any local dev port 5173-5199 may call the API
DEV_ORIGIN_PATTERN = r"^http://(localhost|127\.0\.0\.1):51[7-9]\d$"
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
        self.notify = NotifyStore(audit_path)
        self.access = AccessStore(audit_path)
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
        self.route(state)
        self.raise_alerts(state)
        return state

    def route(self, state: PipelineState) -> None:
        """Place new cases in a unit by their provider's city. Existing assignments stay as they are."""
        try:
            route_cases(state.cases, state.db_path, self.access)
        except Exception:
            log.exception("could not route the cases to units")

    def raise_alerts(self, state: PipelineState) -> None:
        """Tell reviewers what this run found. A problem here is logged and never stops the run."""
        try:
            queue = views.build_queue(
                state, self.decisions, DEFAULT_CAPACITY_HOURS, views.parse_weights(None), False,
                self.overrides,
            )
            config = load_config()
            alerts.run_alerts(
                cases=state.cases,
                ranks={i.case_id: i.rank for i in queue.scheduled + queue.backlog},
                backlog_count=len(queue.backlog), decided=set(self.decisions),
                store=self.notify, audit=self.audit, email=email_channel(config), config=config,
            )
        except Exception:
            log.exception("could not raise notifications for this run")


RuntimeDep = Annotated[Runtime, Depends(get_runtime)]
StateDep = Annotated[PipelineState, Depends(get_state)]
ViewDep = Annotated[object, Depends(case_permission("view"))]  # the user may open this case
DecideDep = Annotated[object, Depends(case_permission("decide"))]  # ... and decide on it


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
    seed: Seeder | None = None,
) -> FastAPI:
    # CLAIMSHIELD_AUDIT_PATH lets a demo or test use its own audit file
    audit_file = Path(audit_path or os.environ.get("CLAIMSHIELD_AUDIT_PATH") or AUDIT_PATH)
    runtime = Runtime(
        Path(data_dir), audit_file, runner or (lambda path: pipeline.run_all(path, team_hours))
    )
    ensure_seeded(runtime.access, seed or seed_demo)  # demo users, from DEMO_PASSWORD, only into an empty database

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
        CORSMiddleware, allow_origins=VITE_ORIGINS, allow_origin_regex=DEV_ORIGIN_PATTERN, allow_methods=["GET", "POST", "PUT", "OPTIONS"],
        allow_headers=["*"], expose_headers=["Content-Disposition"],
    )  # fmt: skip

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

    app.include_router(notifications.build_router())
    app.include_router(access_routes.build_router(make_brief))

    @app.get("/health", response_model=HealthOut)
    def health(rt: RuntimeDep) -> HealthOut:
        """Server status and how long each pipeline stage took. The only open endpoint."""
        return _health(rt)

    @app.get("/overview", response_model=OverviewOut)
    def overview(rt: RuntimeDep, state: StateDep, user: UserDep):
        """Headline numbers for what you may see: everything for an admin, your unit or your cases."""
        if user.role == "admin":
            return views.build_overview(state, rt.decisions)
        label = "unit" if user.role == "team_lead" else "mine"
        return scope.overview_for(state, rt.decisions, rt.overrides, scope.visible(rt, user, state), label)

    @app.get("/queue", response_model=QueueOut)
    def queue(
        rt: RuntimeDep,
        state: StateDep,
        user: UserDep,
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
        view: Annotated[
            Literal["mine", "unit"],
            Query(description="Investigators: their own cases, or the read-only unit queue"),
        ] = "mine",
    ):
        """Cases re-ranked with the given weights and scheduled against team capacity, over the
        cases you may see (an investigator can also list the unit's queue, read-only)."""
        cases = scope.visible(rt, user, state, "unit" if view == "unit" else "all")
        built = views.build_queue(
            state if user.role == "admin" else scope.scoped_state(state, cases), rt.decisions,
            capacity, views.parse_weights(weights), include_decided, rt.overrides,
        )  # fmt: skip
        return scope.annotate_queue(rt, built, user)

    def find_case(state: PipelineState, case_id: str):
        case = state.case(case_id)
        if case is None:
            raise HTTPException(404, f"Unknown case {case_id}")
        return case

    @app.get("/cases/{case_id}", response_model=CaseDetail)
    def case_detail(case_id: str, rt: RuntimeDep, state: StateDep, user: UserDep, _: ViewDep):
        """One case: findings with reasons and evidence IDs, timeline, prediction, decisions."""
        return scope.case_detail(rt, state, find_case(state, case_id), user)

    @app.get("/cases/{case_id}/evidence/{key}", response_model=EvidenceRows)
    def case_evidence(
        case_id: str,
        key: str,
        state: StateDep,
        _: ViewDep,
        limit: Annotated[int, Query(ge=1, le=500, description="Most claim rows to return")] = 200,
    ):
        """The claim rows (and linked records) behind one evidence item such as E1."""
        return views.build_evidence_rows(state, find_case(state, case_id), key, limit)

    @app.get("/cases/{case_id}/graph", response_model=GraphOut)
    def case_graph(case_id: str, state: StateDep, _: ViewDep):
        """Network around the case, with a suspicious flag on nodes and links."""
        return views.build_case_graph(state, find_case(state, case_id))

    @app.get("/cases/{case_id}/brief", response_model=BriefOut)
    def case_brief(
        case_id: str,
        rt: RuntimeDep,
        state: StateDep,
        _: ViewDep,
        horizon: Annotated[int, Query(ge=1, le=365, description="Prediction horizon in days")] = 30,
    ):
        """Investigation brief: written by the LLM only if a key is set and the text validates."""
        find_case(state, case_id)
        return make_brief(rt, state, case_id, horizon)

    @app.post("/cases/{case_id}/decision", response_model=DecisionOut, status_code=201)
    def decide(case_id: str, body: DecisionIn, rt: RuntimeDep, state: StateDep, user: UserDep, _: DecideDep):
        """Record a decision as the signed-in user. A reason is required. Nothing is denied or blocked."""
        find_case(state, case_id)
        with rt.decision_lock:
            entry = rt.audit.append(
                "decision", case_id=case_id, action=body.action, reason=body.reason,
                reviewer=user.display_name, details={"user_id": user.id, "role": user.role},
            )  # fmt: skip
            rt.decisions[case_id] = entry
            if rt.access.get_assignment(case_id):
                rt.access.set_status(case_id, "closed" if body.action == "dismiss" else "in_review")
        return DecisionOut(
            audit_id=entry["audit_id"], case_id=case_id, action=body.action, reason=body.reason,
            reviewer=user.display_name, decided_at=entry["ts"],
            case_status=STATUS_BY_ACTION[body.action],
        )  # fmt: skip

    @app.post(
        "/cases/{case_id}/priority-override", response_model=PriorityOverrideOut, status_code=201
    )
    def override_priority(case_id: str, body: OverrideIn, rt: RuntimeDep, state: StateDep, user: UserDep, _: DecideDep):
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
                reason=body.reason, reviewer=user.display_name,
                details={"priority": body.priority, "ai_priority": ai_priority, "user_id": user.id},
            )
            if body.priority is not None:
                rt.overrides[case_id] = entry
            else:
                rt.overrides.pop(case_id, None)
        return PriorityOverrideOut(
            audit_id=entry["audit_id"], case_id=case_id, ai_priority=ai_priority,
            priority=body.priority if body.priority is not None else ai_priority,
            override_active=body.priority is not None, reason=body.reason,
            reviewer=user.display_name, ts=entry["ts"],
        )

    @app.get("/audit", response_model=list[AuditEntry])
    def audit(
        rt: RuntimeDep,
        state: StateDep,
        user: UserDep,
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        case_id: Annotated[str | None, Query()] = None,
        event_type: Annotated[str | None, Query(description="For example decision")] = None,
    ):
        """The audit log, latest first: everything for an admin, otherwise only entries about the
        cases you may see."""
        if user.role == "admin":
            return rt.audit.list(limit, case_id, event_type)
        mine = {c.case_id for c in scope.visible(rt, user, state)}
        if case_id is not None and case_id not in mine:
            return []
        entries = rt.audit.list(1000, case_id, event_type)
        return [e for e in entries if e["case_id"] in mine][:limit]

    @app.post("/admin/prewarm-briefs", response_model=PrewarmOut)
    def prewarm_briefs(
        rt: RuntimeDep,
        state: StateDep,
        _: AdminDep,
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
    def rerun(rt: RuntimeDep, user: AdminDep):
        """Rebuild everything. The current data keeps being served until the new run swaps in."""
        if not rt.rerun_lock.acquire(blocking=False):
            raise HTTPException(409, "A rerun is already in progress")
        try:
            try:
                rt.load(1 - rt.active, "rerun")
            except PipelineError as exc:
                raise HTTPException(500, f"Rerun failed; previous state kept ({exc})") from exc
            rt.audit.append("rerun_requested", reviewer=user.display_name,
                            details={"user_id": user.id})  # fmt: skip
        finally:
            rt.rerun_lock.release()
        return _health(rt)

    return app


app = create_app()
