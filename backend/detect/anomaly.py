"""Provider anomaly model: StandardScaler -> IsolationForest, with plain-language drivers.

Pipeline order: data -> rules engine -> provider features -> anomaly (this module appends
detector="anomaly" findings to the findings table, replacing any earlier anomaly rows).
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.ensemble import IsolationForest
from sklearn.preprocessing import StandardScaler

from backend.detect.base import Finding
from backend.detect.engine import append_findings
from backend.features.provider_features import FEATURES, build_provider_features

log = logging.getLogger("claimshield.anomaly")
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"
SEED = 42
N_ESTIMATORS = 200
CONTAMINATION = 0.05
FLAG_PERCENTILE = 95
MIN_PROVIDERS = 10
MIN_PEERS = 3
TOP_DRIVERS = 3
MAD_TO_SIGMA = 1.4826

SHORT = {
    "claims_per_day": "Claims per day",
    "avg_amount_ratio": "Average amount",
    "level5_share": "Level-5 share",
    "services_per_member_month": "Services per member",
    "repeat_member_ratio": "Repeat-member ratio",
    "referred_claim_share": "Referred-claim share",
    "weekend_ratio": "Weekend ratio",
    "rule_hits_per_100": "Rule hits per 100 claims",
}
PERCENT = {"level5_share", "repeat_member_ratio", "referred_claim_share", "weekend_ratio"}

SCORES_DDL = """
CREATE TABLE provider_anomaly_scores (
    provider_id TEXT PRIMARY KEY, specialty TEXT NOT NULL, anomaly_score REAL NOT NULL,
    score_rank INTEGER NOT NULL, flagged INTEGER NOT NULL, drivers TEXT NOT NULL);
"""


@dataclass
class AnomalyResult:
    scores: pd.DataFrame = field(default_factory=pd.DataFrame)  # indexed by provider_id
    findings: list[Finding] = field(default_factory=list)
    threshold: float | None = None


def score_providers(features: pd.DataFrame) -> pd.Series | None:
    """Anomaly score in 0..1 (1 = most unusual), or None when there is too little data."""
    x = features[FEATURES]
    if len(x) < MIN_PROVIDERS or x.isna().any().any():
        log.warning("Insufficient data: %d providers or missing feature values", len(x))
        return None
    scaled = StandardScaler().fit_transform(x)
    model = IsolationForest(
        n_estimators=N_ESTIMATORS, contamination=CONTAMINATION, random_state=SEED
    ).fit(scaled)
    raw = -model.score_samples(scaled)
    span = raw.max() - raw.min()
    scaled_scores = (raw - raw.min()) / span if span > 0 else np.zeros(len(raw))
    return pd.Series(scaled_scores, index=x.index, name="anomaly_score")


def _fmt(feature: str, value: float) -> str:
    if feature in PERCENT:
        return f"{value:.0%}"
    return f"{value:.0f}" if feature == "rule_hits_per_100" else f"{value:.2f}"


def drivers(features: pd.DataFrame, provider_id: str) -> tuple[list[dict], str]:
    """Top positive deviations (value - peer median) / peer MAD, and the peer scope used."""
    row = features.loc[provider_id]
    same = features[(features.specialty == row.specialty) & (features.index != provider_id)]
    if len(same) >= MIN_PEERS:
        peers, scope = same, f"{row.specialty} peers"
    else:
        peers, scope = features.drop(index=provider_id), "all providers"
    out = []
    for feature in FEATURES:
        median = float(peers[feature].median())
        mad = float((peers[feature] - median).abs().median())
        scale = max(
            MAD_TO_SIGMA * mad, 0.05 * abs(median), 0.1 * float(features[feature].std()), 1e-9
        )
        deviation = (float(row[feature]) - median) / scale
        if deviation > 0:
            out.append(
                {
                    "feature": feature,
                    "value": float(row[feature]),
                    "peer_median": median,
                    "deviation": deviation,
                }
            )
    out.sort(key=lambda d: (-d["deviation"], d["feature"]))
    return out[:TOP_DRIVERS], scope


def driver_text(d: dict) -> str:
    value, median = _fmt(d["feature"], d["value"]), _fmt(d["feature"], d["peer_median"])
    if d["peer_median"] > 1e-9:
        return f"{SHORT[d['feature']]} {d['value'] / d['peer_median']:.1f}× peers ({value} vs {median})"
    return f"{SHORT[d['feature']]} {value} (peers typically {median})"


def _severity(score: float) -> str:
    return "high" if score >= 0.9 else "medium" if score >= 0.7 else "low"


def analyze(features: pd.DataFrame, claims: pd.DataFrame) -> AnomalyResult:
    """Score every provider and build findings for those above the 95th percentile."""
    scores = score_providers(features)
    if scores is None:
        return AnomalyResult()
    threshold = float(np.percentile(scores, FLAG_PERCENTILE))
    ranked = scores.sort_values(ascending=False, kind="stable")
    table = pd.DataFrame(
        {
            "specialty": features.loc[ranked.index, "specialty"],
            "anomaly_score": ranked.round(4),
            "score_rank": range(1, len(ranked) + 1),
            "flagged": (ranked > threshold).astype(int),
        }
    )
    claim_ids = claims.groupby("provider_id").claim_id.apply(sorted).to_dict()
    top, findings = {}, []
    for provider_id in table.index:
        top[provider_id], scope = drivers(features, provider_id)
        if not table.loc[provider_id, "flagged"] or provider_id not in claim_ids:
            continue
        score = float(table.loc[provider_id, "anomaly_score"])
        text = "; ".join(driver_text(d) for d in top[provider_id]) or "no single feature stands out"
        findings.append(
            Finding(
                entity_id=provider_id,
                detector="anomaly",
                score=score,
                severity=_severity(score),
                reason=(
                    f"Provider {provider_id} has an unusual overall profile versus {scope} "
                    f"(anomaly score {score:.2f}): {text}. Suspicious pattern that warrants review."
                ),
                evidence_ids=claim_ids[provider_id],
            )
        )
    table["drivers"] = [json.dumps(top[p]) for p in table.index]
    return AnomalyResult(scores=table, findings=findings, threshold=threshold)


def save_results(db_path: str | Path, result: AnomalyResult) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("DROP TABLE IF EXISTS provider_anomaly_scores")
        con.executescript(SCORES_DDL)
        con.executemany(
            "INSERT INTO provider_anomaly_scores VALUES (?, ?, ?, ?, ?, ?)",
            [
                (pid, r.specialty, r.anomaly_score, r.score_rank, r.flagged, r.drivers)
                for pid, r in result.scores.iterrows()
            ],
        )
        con.commit()
    finally:
        con.close()
    append_findings(db_path, "anomaly", result.findings)


def run(db_path: str | Path = DB_PATH, features: pd.DataFrame | None = None) -> AnomalyResult:
    if features is None:  # the pipeline passes features it has already built
        features = build_provider_features(db_path)  # reads current rule findings as one feature
    con = sqlite3.connect(db_path)
    try:
        claims = pd.read_sql_query("SELECT claim_id, provider_id FROM claims", con)
    finally:
        con.close()
    result = analyze(features, claims)
    if result.scores.empty:
        log.warning("Insufficient data: no anomaly scores produced")
        return result
    save_results(db_path, result)
    return result


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    res = run()
    print(res.scores[["specialty", "anomaly_score", "score_rank", "flagged"]].head(10).to_string())
    print(f"95th percentile threshold: {res.threshold:.3f}; findings emitted: {len(res.findings)}")
    for f in res.findings:
        print(f"- {f.entity_id} [{f.severity}] {f.reason}")
