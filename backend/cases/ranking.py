"""Priority ranking and capacity scheduling for cases.

priority = w_risk*risk + w_dollars*dollars + w_impact*impact + w_severity*severity + w_evidence*evidence
Risk, dollars and impact are divided by their maximum across cases (0 stays 0); severity and
evidence are already 0-1. The ranking is a recommendation for human reviewers, never a decision.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, fields
from pathlib import Path

import pandas as pd

from backend.cases.builder import DETECTOR_GROUPS, STATUS_AWAITING, Case, build_cases

log = logging.getLogger("claimshield.cases")
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"

SEVERITY_SCORE = {"critical": 1.0, "high": 0.75, "medium": 0.5, "low": 0.25}
PROVIDER_CASE_HOURS = 4.0
RING_BASE_HOURS = 4.0
RING_HOURS_PER_ENTITY = 1.0
DEFAULT_TEAM_HOURS = 40.0

CASES_DDL = """
CREATE TABLE cases (
    case_id TEXT PRIMARY KEY, case_type TEXT NOT NULL, primary_entity TEXT NOT NULL,
    entity_ids TEXT NOT NULL, rank INTEGER NOT NULL, priority REAL NOT NULL,
    risk REAL NOT NULL, dollars REAL NOT NULL, impact REAL NOT NULL, severity REAL NOT NULL,
    evidence REAL NOT NULL, flagged_amount INTEGER NOT NULL, affected_members TEXT NOT NULL,
    n_members INTEGER NOT NULL, detectors_fired TEXT NOT NULL, finding_ids TEXT NOT NULL,
    summary TEXT NOT NULL, effort_hours REAL NOT NULL, cumulative_hours REAL NOT NULL,
    queue TEXT NOT NULL CHECK (queue IN ('scheduled', 'backlog')), status TEXT NOT NULL);
"""


@dataclass(frozen=True)
class Weights:
    risk: float = 0.30
    dollars: float = 0.25
    impact: float = 0.15
    severity: float = 0.15
    evidence: float = 0.15

    def __post_init__(self) -> None:
        values = [getattr(self, f.name) for f in fields(self)]
        if any(v < 0 for v in values) or abs(sum(values) - 1.0) > 1e-9:
            raise ValueError(f"weights must be non-negative and sum to 1, got {values}")


def effort_hours(case: Case) -> float:
    if case.case_type == "ring":
        return RING_BASE_HOURS + RING_HOURS_PER_ENTITY * len(case.entity_ids)
    return PROVIDER_CASE_HOURS


def _scaled(series: pd.Series) -> pd.Series:
    peak = series.max()
    return series / peak if peak > 0 else series * 0.0


def rank_cases(cases: list[Case], weights: Weights | None = None) -> pd.DataFrame:
    """Factor scores, priority and rank per case (best first; ties broken by case_id)."""
    weights = weights or Weights()
    if not cases:
        return pd.DataFrame()
    df = pd.DataFrame(
        {
            "case_id": [c.case_id for c in cases],
            "risk_raw": [(c.rule_score + c.anomaly_score + c.graph_score) / 3 for c in cases],
            "dollars_raw": [c.flagged_amount for c in cases],
            "impact_raw": [len(c.affected_members) for c in cases],
            "severity": [SEVERITY_SCORE[c.worst_severity] for c in cases],
            "evidence": [len(c.detectors_fired) / len(DETECTOR_GROUPS) for c in cases],
        }
    ).set_index("case_id")
    df["risk"], df["dollars"], df["impact"] = (
        _scaled(df.risk_raw), _scaled(df.dollars_raw), _scaled(df.impact_raw),
    )  # fmt: skip
    df["priority"] = (
        weights.risk * df.risk
        + weights.dollars * df.dollars
        + weights.impact * df.impact
        + weights.severity * df.severity
        + weights.evidence * df.evidence
    ).round(6)
    df = df.reset_index().sort_values(["priority", "case_id"], ascending=[False, True])
    df["rank"] = range(1, len(df) + 1)
    return df.reset_index(drop=True)


def schedule(ranked: pd.DataFrame, cases: list[Case], team_hours: float) -> pd.DataFrame:
    """Schedule in strict priority order until team hours run out; the rest is backlog."""
    by_id = {c.case_id: c for c in cases}
    effort, cumulative, queue = [], [], []
    used, full = 0.0, False
    for case_id in ranked.case_id:
        hours = effort_hours(by_id[case_id])
        if not full and used + hours <= team_hours:
            used += hours
            queue.append("scheduled")
        else:
            full = True  # strict priority: nothing lower-ranked jumps the queue
            queue.append("backlog")
        effort.append(hours)
        cumulative.append(used)
    return ranked.assign(effort_hours=effort, cumulative_hours=cumulative, queue=queue)


@dataclass
class RankResult:
    cases: list[Case]
    ranked: pd.DataFrame
    team_hours: float

    @property
    def scheduled_hours(self) -> float:
        done = self.ranked[self.ranked.queue == "scheduled"]
        return float(done.effort_hours.sum()) if len(done) else 0.0


def save_cases(db_path: str | Path, result: RankResult) -> None:
    by_id = {c.case_id: c for c in result.cases}
    rows = []
    for r in result.ranked.itertuples():
        c = by_id[r.case_id]
        rows.append(
            (
                c.case_id, c.case_type, c.primary_entity, json.dumps(c.entity_ids), int(r.rank),
                float(r.priority), float(r.risk), float(r.dollars), float(r.impact),
                float(r.severity), float(r.evidence), int(c.flagged_amount),
                json.dumps(c.affected_members), len(c.affected_members),
                json.dumps(c.detectors_fired), json.dumps(c.finding_ids), c.summary,
                float(r.effort_hours), float(r.cumulative_hours), r.queue, STATUS_AWAITING,
            )
        )  # fmt: skip
    con = sqlite3.connect(db_path)
    try:
        con.execute("DROP TABLE IF EXISTS cases")
        con.executescript(CASES_DDL)
        con.executemany(f"INSERT INTO cases VALUES ({', '.join('?' * 21)})", rows)
        con.commit()
    finally:
        con.close()


def run(
    db_path: str | Path = DB_PATH,
    weights: Weights | None = None,
    team_hours: float = DEFAULT_TEAM_HOURS,
) -> RankResult:
    cases = build_cases(db_path)
    if not cases:
        log.warning("Insufficient data: no cases to rank")
        return RankResult([], pd.DataFrame(), team_hours)
    ranked = schedule(rank_cases(cases, weights or Weights()), cases, team_hours)
    result = RankResult(cases, ranked, team_hours)
    save_cases(db_path, result)
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    res = run()
    by_id = {c.case_id: c for c in res.cases}
    for r in res.ranked.itertuples():
        c = by_id[r.case_id]
        print(
            f"#{r.rank:<2d} {c.case_id} {c.case_type:8s} {c.primary_entity:8s} "
            f"priority {r.priority:.3f}  ₹{c.flagged_amount:>9,}  members {len(c.affected_members):3d}  "
            f"{','.join(c.detectors_fired):13s} {r.effort_hours:.0f}h  {r.queue}"
        )
    print(f"scheduled {res.scheduled_hours:.0f}h of {res.team_hours:.0f}h team hours; "
          f"{int((res.ranked.queue == 'backlog').sum())} cases in backlog")
