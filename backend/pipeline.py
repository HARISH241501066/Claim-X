"""Run every stage end to end and keep the results in memory for the API.

Stages: data -> rules -> features -> anomaly -> graph -> prediction -> cases -> ranking -> packs.
(Prediction runs before cases are built because case risk uses the investigation risk.)
Only the data stage is fatal; any other stage that fails is logged and skipped, and later stages
degrade to "Insufficient data" instead of guessing.
"""

from __future__ import annotations

import logging
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import networkx as nx
import pandas as pd

from backend.audit import now_iso
from backend.brief.evidence import DEFAULT_HORIZON, Pack, build_pack
from backend.cases import ranking
from backend.cases.builder import Case, build_cases
from backend.data import generator
from backend.detect import anomaly, engine, graph
from backend.features.provider_features import build_provider_features
from backend.predict import model as risk_model

log = logging.getLogger("claimx.pipeline")
DB_PATH = Path(__file__).resolve().parent / "claimx.db"
STAGE_NAMES = [
    "data", "rules", "features", "anomaly", "graph", "prediction", "cases", "ranking", "packs",
]  # fmt: skip


class PipelineError(RuntimeError):
    """The pipeline cannot produce a usable state (the data stage failed)."""


@dataclass
class StageTiming:
    name: str
    seconds: float
    status: str = "ok"  # "ok" or "failed"
    error: str | None = None


@dataclass
class PipelineState:
    db_path: Path
    started_at: str
    finished_at: str
    timings: list[StageTiming]
    total_seconds: float
    team_hours: float
    cases: list[Case] = field(default_factory=list)
    ranked: pd.DataFrame = field(default_factory=pd.DataFrame)  # default weights and capacity
    packs: dict[str, Pack] = field(default_factory=dict)
    findings: list[dict] = field(default_factory=list)
    claims_count: int = 0
    dollars_at_risk: int = 0
    graph: nx.MultiDiGraph | None = None
    pairs: pd.DataFrame = field(default_factory=pd.DataFrame)
    metrics: dict = field(default_factory=dict)

    @property
    def status(self) -> str:
        return "ok" if all(t.status == "ok" for t in self.timings) else "degraded"

    def case(self, case_id: str) -> Case | None:
        return next((c for c in self.cases if c.case_id == case_id), None)


def _stage(
    name: str, fn: Callable[[], Any], timings: list[StageTiming], *, critical: bool = False
) -> Any:
    """Run one stage, time it, and log it. Non-critical failures are recorded and skipped."""
    started = time.perf_counter()
    try:
        result = fn()
    except Exception as exc:
        seconds = time.perf_counter() - started
        log.exception("stage %s failed after %.2fs", name, seconds)
        timings.append(StageTiming(name, round(seconds, 3), "failed", f"{type(exc).__name__}"))
        if critical:
            raise PipelineError(f"stage '{name}' failed: {type(exc).__name__}") from exc
        return None
    seconds = time.perf_counter() - started
    log.info("stage %-10s %.2fs", name, seconds)
    timings.append(StageTiming(name, round(seconds, 3)))
    return result


def _scalar(db_path: Path, sql: str, params: tuple = ()) -> int:
    con = sqlite3.connect(db_path)
    try:
        return int(con.execute(sql, params).fetchone()[0] or 0)
    finally:
        con.close()


def _dollars_at_risk(db_path: Path, cases: list[Case]) -> int:
    """Rupee value of the distinct claims that claim-level findings cite, across all cases."""
    flagged = sorted({c for case in cases for c in case.flagged_claim_ids})
    total = 0
    con = sqlite3.connect(db_path)
    try:
        for i in range(0, len(flagged), 500):
            chunk = flagged[i : i + 500]
            marks = ",".join("?" * len(chunk))
            total += con.execute(
                f"SELECT COALESCE(SUM(billed_amount), 0) FROM claims WHERE claim_id IN ({marks})",
                chunk,
            ).fetchone()[0]
    finally:
        con.close()
    return int(total)


def run_all(
    db_path: str | Path = DB_PATH,
    team_hours: float = ranking.DEFAULT_TEAM_HOURS,
    horizon: int = DEFAULT_HORIZON,
) -> PipelineState:
    """Run all stages into db_path and return the in-memory state the API serves from."""
    db = Path(db_path)
    metrics_path = db.with_name(f"{db.stem}.metrics.json")
    timings: list[StageTiming] = []
    started_at, clock = now_iso(), time.perf_counter()

    _stage("data", lambda: generator.generate(db, None), timings, critical=True)
    _stage("rules", lambda: engine.run(db), timings)
    features = _stage("features", lambda: build_provider_features(db), timings)
    _stage("anomaly", lambda: anomaly.run(db, features=features), timings)

    def graph_stage() -> tuple[nx.MultiDiGraph, pd.DataFrame]:
        result = graph.run(db)
        return graph.build_graph(graph.load_graph_tables(db)), result.pairs

    graph_out = _stage("graph", graph_stage, timings)
    metrics = _stage("prediction", lambda: risk_model.run_all(db, metrics_path).metrics, timings)
    cases = _stage("cases", lambda: build_cases(db), timings) or []

    def ranking_stage() -> pd.DataFrame:
        ranked = ranking.schedule(ranking.rank_cases(cases), cases, team_hours)
        ranking.save_cases(db, ranking.RankResult(cases, ranked, team_hours))
        return ranked

    ranked = _stage("ranking", ranking_stage, timings) if cases else None
    packs = _stage(
        "packs", lambda: {c.case_id: build_pack(c.case_id, horizon, db) for c in cases}, timings
    ) if ranked is not None else None
    if not cases:
        log.warning("Insufficient data: no cases were built")

    findings = _stage("findings", lambda: engine.load_findings(db), []) or []
    state = PipelineState(
        db_path=db, started_at=started_at, finished_at=now_iso(), timings=timings,
        total_seconds=round(time.perf_counter() - clock, 3), team_hours=team_hours, cases=cases,
        ranked=ranked if ranked is not None else pd.DataFrame(), packs=packs or {},
        findings=findings, claims_count=_scalar(db, "SELECT COUNT(*) FROM claims"),
        dollars_at_risk=_dollars_at_risk(db, cases),
        graph=graph_out[0] if graph_out else None,
        pairs=graph_out[1] if graph_out else pd.DataFrame(), metrics=metrics or {},
    )  # fmt: skip
    log.info("pipeline %s in %.2fs", state.status, state.total_seconds)
    return state
