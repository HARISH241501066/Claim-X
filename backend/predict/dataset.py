"""Training dataset for investigation risk: one row per provider and monthly cutoff.

Features use only claims dated on or before the cutoff. The label is whether a confirmed
investigation was opened in the N days after it. Investigation risk is the probability of a
confirmed investigation within N days. It is not a probability that anyone did anything wrong.
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

log = logging.getLogger("claimshield.predict")
DB_PATH = Path(__file__).resolve().parents[1] / "claimshield.db"

WINDOW_START = date(2026, 1, 1)
DATA_END = date(2026, 6, 30)
CUTOFFS = [date(2026, 2, 28), date(2026, 3, 31), date(2026, 4, 30), date(2026, 5, 31)]  # months 2-5
HORIZONS = (30, 60, 90)
MIN_HISTORY_DAYS = 60  # below this a row is flagged "Low confidence"

FEATURES = [
    "claims_per_day",
    "avg_amount_ratio",
    "level5_share",
    "services_per_member_month",
    "repeat_member_ratio",
    "referred_claim_share",
    "weekend_ratio",
    "trend",
    "prior_confirmed_count",
]
LABELS = {
    "claims_per_day": "Claims per day",
    "avg_amount_ratio": "Average amount versus specialty median",
    "level5_share": "Level-5 share",
    "services_per_member_month": "Services per member per month",
    "repeat_member_ratio": "Repeat-member ratio",
    "referred_claim_share": "Referred-claim share",
    "weekend_ratio": "Weekend ratio",
    "trend": "Volume trend (last 30 days vs previous 30)",
    "prior_confirmed_count": "Prior confirmed investigations",
}


def load_inputs(db_path: str | Path) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    con = sqlite3.connect(db_path)
    try:
        claims = pd.read_sql_query(
            "SELECT claim_id, member_id, provider_id, service_date, billed_amount, code_level, "
            "referring_provider_id FROM claims",
            con,
        )
        providers = pd.read_sql_query("SELECT provider_id, specialty FROM providers", con)
        investigations = pd.read_sql_query("SELECT * FROM investigations", con)
    finally:
        con.close()
    return claims, providers, investigations


def features_at(
    claims: pd.DataFrame,
    providers: pd.DataFrame,
    investigations: pd.DataFrame,
    cutoff: date,
) -> pd.DataFrame:
    """Features for every provider with claims on or before the cutoff (index provider_id)."""
    iso = cutoff.isoformat()
    seen = claims[claims.service_date <= iso]
    if seen.empty:
        return pd.DataFrame(columns=[*FEATURES, "history_days", "low_confidence"])
    specialty = dict(zip(providers.provider_id, providers.specialty, strict=True))
    seen = seen.assign(
        day=pd.to_datetime(seen.service_date),
        specialty=seen.provider_id.map(specialty),
        month=seen.service_date.str[:7],
    )
    cut = pd.Timestamp(cutoff)
    recent_from, prior_from = cut - pd.Timedelta(days=29), cut - pd.Timedelta(days=59)
    median_amount = seen.groupby("specialty").billed_amount.median()
    window_days = (cutoff - WINDOW_START).days + 1
    confirmed = investigations[
        (investigations.outcome == "confirmed")
        & (investigations.entity_type == "provider")
        & (investigations.closed_date <= iso)
    ].groupby("entity_id").size()
    rows = {}
    for provider_id, g in seen.groupby("provider_id"):
        n = len(g)
        levelled = g[g.code_level.notna()]
        last30 = int((g.day >= recent_from).sum())
        prev30 = int(((g.day >= prior_from) & (g.day < recent_from)).sum())
        history_days = (cutoff - g.day.min().date()).days + 1
        rows[provider_id] = {
            "claims_per_day": n / window_days,
            "avg_amount_ratio": g.billed_amount.mean() / median_amount[specialty[provider_id]],
            "level5_share": float((levelled.code_level == 5).mean()) if len(levelled) else 0.0,
            "services_per_member_month": n / g.groupby(["member_id", "month"]).ngroups,
            "repeat_member_ratio": (n - g.member_id.nunique()) / n,
            "referred_claim_share": float(g.referring_provider_id.notna().mean()),
            "weekend_ratio": float((g.day.dt.dayofweek >= 5).mean()),
            "trend": (last30 + 1) / (prev30 + 1),
            "prior_confirmed_count": int(confirmed.get(provider_id, 0)),
            "history_days": history_days,
            "low_confidence": history_days < MIN_HISTORY_DAYS,
        }
    return pd.DataFrame.from_dict(rows, orient="index").rename_axis("provider_id")


def label_columns(
    provider_ids: pd.Index, investigations: pd.DataFrame, cutoff: date
) -> pd.DataFrame:
    """label_N: confirmed investigation opened in (cutoff, cutoff+N]; censored_N if past the data."""
    confirmed = investigations[
        (investigations.outcome == "confirmed") & (investigations.entity_type == "provider")
    ]
    out = pd.DataFrame(index=provider_ids)
    for n in HORIZONS:
        end = cutoff + timedelta(days=n)
        opened = confirmed[
            (confirmed.opened_date > cutoff.isoformat()) & (confirmed.opened_date <= end.isoformat())
        ].entity_id
        out[f"label_{n}"] = provider_ids.isin(set(opened)).astype(int)
        out[f"censored_{n}"] = end > DATA_END
    return out


def build_dataset(db_path: str | Path = DB_PATH) -> pd.DataFrame:
    """Rows for every provider at every monthly cutoff, with labels for 30/60/90 days."""
    claims, providers, investigations = load_inputs(db_path)
    frames = []
    for cutoff in CUTOFFS:
        feats = features_at(claims, providers, investigations, cutoff)
        if feats.empty:
            log.warning("Insufficient data: no claims before cutoff %s", cutoff)
            continue
        frame = feats.join(label_columns(feats.index, investigations, cutoff))
        frame.insert(0, "cutoff", cutoff.isoformat())
        frames.append(frame.reset_index())
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True).sort_values(["cutoff", "provider_id"], ignore_index=True)
