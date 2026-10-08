"""Per-provider behavioural features for the anomaly model (written to provider_features)."""

from __future__ import annotations

import json
import logging
import sqlite3
from pathlib import Path

import pandas as pd

log = logging.getLogger("claimshield.features")
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"
WINDOW_DAYS = 181  # 2026-01-01 .. 2026-06-30

FEATURES = [
    "claims_per_day",
    "avg_amount_ratio",
    "level5_share",
    "services_per_member_month",
    "repeat_member_ratio",
    "referred_claim_share",
    "weekend_ratio",
    "rule_hits_per_100",
]
LABELS = {
    "claims_per_day": "Claims per day",
    "avg_amount_ratio": "Average amount versus specialty median",
    "level5_share": "Level-5 share",
    "services_per_member_month": "Services per member per month",
    "repeat_member_ratio": "Repeat-member ratio",
    "referred_claim_share": "Referred-claim share",
    "weekend_ratio": "Weekend ratio",
    "rule_hits_per_100": "Rule hits per 100 claims",
}


def rule_hit_claims(con: sqlite3.Connection) -> set[str]:
    """Claim IDs that any rule finding cites as evidence (model and graph findings are excluded)."""
    try:
        rows = con.execute(
            "SELECT evidence_ids FROM findings WHERE detector NOT IN ('anomaly', 'ring')"
        ).fetchall()
    except sqlite3.OperationalError:
        log.warning("Insufficient data: no findings table yet; rule_hits_per_100 set to 0")
        return set()
    return {e for (raw,) in rows for e in json.loads(raw)}


def compute_features(
    claims: pd.DataFrame,
    providers: pd.DataFrame,
    rule_hits: set[str] | None = None,
    window_days: int = WINDOW_DAYS,
) -> pd.DataFrame:
    """One row per provider with at least one claim; index is provider_id."""
    specialty = dict(zip(providers.provider_id, providers.specialty, strict=True))
    df = claims.assign(
        specialty=claims.provider_id.map(specialty),
        month=claims.service_date.str[:7],
        weekend=pd.to_datetime(claims.service_date).dt.dayofweek >= 5,
        referred=claims.referring_provider_id.notna(),
        hit=claims.claim_id.isin(rule_hits or set()),
    )
    specialty_median = df.groupby("specialty").billed_amount.median()
    rows = []
    for provider_id, g in df.groupby("provider_id"):
        n = len(g)
        levelled = g[g.code_level.notna()]
        rows.append(
            {
                "provider_id": provider_id,
                "specialty": specialty[provider_id],
                "n_claims": n,
                "claims_per_day": n / window_days,
                "avg_amount_ratio": g.billed_amount.mean()
                / specialty_median[specialty[provider_id]],
                "level5_share": float((levelled.code_level == 5).mean()) if len(levelled) else 0.0,
                "services_per_member_month": n / g.groupby(["member_id", "month"]).ngroups,
                "repeat_member_ratio": (n - g.member_id.nunique()) / n,
                "referred_claim_share": float(g.referred.mean()),
                "weekend_ratio": float(g.weekend.mean()),
                "rule_hits_per_100": 100 * float(g.hit.sum()) / n,
            }
        )
    return pd.DataFrame(rows).set_index("provider_id")


def build_provider_features(db_path: str | Path = DB_PATH) -> pd.DataFrame:
    con = sqlite3.connect(db_path)
    try:
        claims = pd.read_sql_query("SELECT * FROM claims", con)
        providers = pd.read_sql_query("SELECT * FROM providers", con)
        features = compute_features(claims, providers, rule_hit_claims(con))
        con.execute("DROP TABLE IF EXISTS provider_features")
        features.reset_index().to_sql("provider_features", con, index=False)
        con.commit()
    finally:
        con.close()
    return features


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    out = build_provider_features()
    print(f"provider_features: {len(out)} providers")
    print(out[FEATURES].describe().loc[["min", "50%", "max"]].round(3).to_string())
