"""Investigation risk model: probability of a confirmed investigation within N days.

Built with GradientBoostingClassifier (seed 42), trained on earlier monthly cutoffs and tested
on the last. The output is called investigation_risk. It estimates how likely an investigation
is to be confirmed soon, not how likely anyone is to have done something wrong, and it only
recommends: a human reviewer decides every case.
"""

from __future__ import annotations

import json
import logging
import sqlite3
import sys
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path

import pandas as pd
from sklearn.ensemble import GradientBoostingClassifier
from sklearn.metrics import average_precision_score

from backend.predict.dataset import (
    DATA_END,
    FEATURES,
    LABELS,
    build_dataset,
    features_at,
    load_inputs,
)

log = logging.getLogger("claimshield.predict")
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"
METRICS_PATH = Path(__file__).resolve().parents[1] / "metrics.json"

DEFAULT_HORIZON = 30
MEDIUM_MIN, HIGH_MIN = 0.3, 0.6  # Low < 0.3 <= Medium < 0.6 <= High
ESCALATION_MIN_PRIOR = 1  # a prior confirmed investigation ...
ESCALATION_MIN_TREND = 1.5  # ... and recent volume at least 1.5x the previous 30 days
TOP_DRIVERS = 3
RISK_TABLE_DDL = """
CREATE TABLE investigation_risk (
    provider_id TEXT PRIMARY KEY, as_of TEXT NOT NULL, horizon_days INTEGER NOT NULL,
    investigation_risk REAL NOT NULL, risk_band TEXT NOT NULL, band_source TEXT NOT NULL,
    band_reason TEXT NOT NULL, low_confidence INTEGER NOT NULL, history_days INTEGER NOT NULL,
    prior_confirmed_count INTEGER NOT NULL, trend REAL NOT NULL, drivers TEXT NOT NULL);
"""


def make_model() -> GradientBoostingClassifier:
    return GradientBoostingClassifier(
        n_estimators=100, learning_rate=0.1, max_depth=2, subsample=0.8, random_state=42
    )


def band_for(investigation_risk: float) -> str:
    if investigation_risk < MEDIUM_MIN:
        return "Low"
    return "Medium" if investigation_risk < HIGH_MIN else "High"


def apply_band_policy(
    investigation_risk: float, prior_confirmed_count: int, trend: float
) -> tuple[str, str, str]:
    """Band, source and reason. The model's number is never changed; only the band may rise.

    Policy: a provider with a prior confirmed investigation and a clear rise in recent volume
    is placed in at least the High band, because the model has too few such examples to learn it.
    """
    band = band_for(investigation_risk)
    if (
        prior_confirmed_count >= ESCALATION_MIN_PRIOR
        and trend >= ESCALATION_MIN_TREND
        and band != "High"
    ):
        reason = (
            f"Raised to High by history rule: {prior_confirmed_count} prior confirmed "
            f"investigation(s) and volume trend {trend:.1f}× (model alone: {band})"
        )
        return "High", "escalated", reason
    return band, "model", f"Model estimate {investigation_risk:.2f}"


# ---------------------------------------------------------------- training and evaluation


def usable_rows(dataset: pd.DataFrame, horizon: int) -> pd.DataFrame:
    return dataset[~dataset[f"censored_{horizon}"]]


def split_by_time(dataset: pd.DataFrame, horizon: int) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Train on earlier cutoffs, test on the last one whose label window is fully observed."""
    rows = usable_rows(dataset, horizon)
    cutoffs = sorted(rows.cutoff.unique())
    if len(cutoffs) < 2:
        return rows.iloc[0:0], rows.iloc[0:0]
    return rows[rows.cutoff < cutoffs[-1]], rows[rows.cutoff == cutoffs[-1]]


def precision_at(test: pd.DataFrame, risk: pd.Series, k: int) -> float:
    ranked = test.assign(risk=risk.values).sort_values(
        ["risk", "provider_id"], ascending=[False, True]
    )
    return float(ranked.head(k).label.mean())


def evaluate(train: pd.DataFrame, test: pd.DataFrame, horizon: int) -> dict:
    """Metrics on the held-out last cutoff, written to metrics.json."""
    label = f"label_{horizon}"
    metrics: dict = {
        "target": "investigation_risk",
        "horizon_days": horizon,
        "train_cutoffs": sorted(train.cutoff.unique()),
        "test_cutoff": min(test.cutoff.unique()) if len(test) else None,
        "n_train_rows": len(train),
        "n_train_positives": int(train[label].sum()) if len(train) else 0,
        "n_test_rows": len(test),
        "n_test_positives": int(test[label].sum()) if len(test) else 0,
        "note": (
            "Labels come from simulated investigations and the test window has few positives, "
            "so these metrics check the pipeline, not real-world accuracy."
        ),
    }
    empty = {"precision_at_5": None, "precision_at_10": None, "recall": None, "pr_auc": None}
    if not len(train) or metrics["n_train_positives"] == 0 or metrics["n_test_positives"] == 0:
        log.warning("Insufficient data: no positives to train or evaluate on")
        return {**metrics, **empty, "status": "Insufficient data"}
    model = make_model().fit(train[FEATURES], train[label])
    risk = pd.Series(model.predict_proba(test[FEATURES])[:, 1], index=test.index)
    flagged = risk >= MEDIUM_MIN
    positives = test[label] == 1
    test = test.assign(label=test[label])
    return {
        **metrics,
        "status": "ok",
        "precision_at_5": round(precision_at(test, risk, 5), 4),
        "precision_at_10": round(precision_at(test, risk, 10), 4),
        "recall": round(float((flagged & positives).sum() / positives.sum()), 4),
        "recall_threshold": MEDIUM_MIN,
        "pr_auc": round(float(average_precision_score(positives, risk)), 4),
        "base_rate": round(float(positives.mean()), 4),
    }


# ---------------------------------------------------------------- scoring


def occlusion_drivers(
    model: GradientBoostingClassifier, row: pd.Series, medians: pd.Series, base: float
) -> list[dict]:
    """Top features that push the risk up: the drop when a feature is set to its typical value."""
    contributions = []
    for feature in FEATURES:
        typical = row[FEATURES].copy()
        typical[feature] = medians[feature]
        lowered = float(model.predict_proba(typical.to_frame().T.astype(float))[:, 1][0])
        if base - lowered > 1e-6:
            contributions.append(
                {
                    "feature": feature,
                    "label": LABELS[feature],
                    "value": round(float(row[feature]), 4),
                    "contribution": round(base - lowered, 4),
                }
            )
    contributions.sort(key=lambda d: (-d["contribution"], d["feature"]))
    return contributions[:TOP_DRIVERS]


def driver_text(d: dict) -> str:
    value = f"{d['value']:.0f}" if d["feature"] == "prior_confirmed_count" else f"{d['value']:.2f}"
    return f"{d['label']} {value} (+{d['contribution']:.2f} risk)"


def score_providers(
    model: GradientBoostingClassifier,
    features: pd.DataFrame,
    medians: pd.Series,
    as_of: date,
    horizon: int,
) -> pd.DataFrame:
    risk = model.predict_proba(features[FEATURES].astype(float))[:, 1]
    rows = []
    for provider_id, p in zip(features.index, risk, strict=True):
        row = features.loc[provider_id]
        band, source, reason = apply_band_policy(
            float(p), int(row.prior_confirmed_count), float(row.trend)
        )
        rows.append(
            {
                "provider_id": provider_id,
                "as_of": as_of.isoformat(),
                "horizon_days": horizon,
                "investigation_risk": round(float(p), 4),
                "risk_band": band,
                "band_source": source,
                "band_reason": reason,
                "low_confidence": bool(row.low_confidence),
                "history_days": int(row.history_days),
                "prior_confirmed_count": int(row.prior_confirmed_count),
                "trend": round(float(row.trend), 4),
                "drivers": occlusion_drivers(model, row, medians, float(p)),
            }
        )
    out = pd.DataFrame(rows).sort_values(
        ["investigation_risk", "provider_id"], ascending=[False, True], ignore_index=True
    )
    out["risk_rank"] = out.index + 1
    return out


@dataclass
class RiskResult:
    metrics: dict = field(default_factory=dict)
    scores: pd.DataFrame = field(default_factory=pd.DataFrame)


def save_scores(db_path: str | Path, scores: pd.DataFrame) -> None:
    con = sqlite3.connect(db_path)
    try:
        con.execute("DROP TABLE IF EXISTS investigation_risk")
        con.executescript(RISK_TABLE_DDL)
        con.executemany(
            "INSERT INTO investigation_risk VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    r.provider_id, r.as_of, int(r.horizon_days), float(r.investigation_risk),
                    r.risk_band, r.band_source, r.band_reason, int(r.low_confidence),
                    int(r.history_days), int(r.prior_confirmed_count), float(r.trend),
                    json.dumps(r.drivers),
                )
                for r in scores.itertuples()
            ],
        )  # fmt: skip
        con.commit()
    finally:
        con.close()


def run(
    db_path: str | Path = DB_PATH,
    metrics_path: str | Path | None = METRICS_PATH,
    horizon: int = DEFAULT_HORIZON,
    as_of: date = DATA_END,
) -> RiskResult:
    dataset = build_dataset(db_path)
    if dataset.empty:
        log.warning("Insufficient data: no dataset rows")
        return RiskResult()
    train, test = split_by_time(dataset, horizon)
    metrics = evaluate(train, test, horizon)
    if metrics_path is not None:
        Path(metrics_path).write_text(json.dumps(metrics, indent=2), encoding="utf-8")
    everything = usable_rows(dataset, horizon)  # final model uses every observed cutoff
    label = f"label_{horizon}"
    if everything[label].sum() == 0:
        log.warning("Insufficient data: no positive labels; no risk scores produced")
        return RiskResult(metrics=metrics)
    model = make_model().fit(everything[FEATURES], everything[label])
    claims, providers, investigations = load_inputs(db_path)
    features = features_at(claims, providers, investigations, as_of)
    scores = score_providers(model, features, everything[FEATURES].median(), as_of, horizon)
    save_scores(db_path, scores)
    return RiskResult(metrics=metrics, scores=scores)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    res = run()
    print(json.dumps(res.metrics, indent=2))
    if not res.scores.empty:
        cols = ["provider_id", "investigation_risk", "risk_band", "band_source", "low_confidence"]
        print(res.scores[cols].head(10).to_string(index=False))
