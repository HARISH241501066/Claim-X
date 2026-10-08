"""Shared reference tables and peer baselines, computed once per engine run."""

from __future__ import annotations

import math
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

MIN_CONSULT_CLAIMS = 30  # a provider needs this many levelled claims to be scored or be a peer
MIN_PEERS = 3
STD_FLOOR = 0.01  # avoids an infinite z-score when peers are almost identical
MIN_MEMBER_MONTHS = 20  # minimum sample for a specialty utilization baseline

# minutes of provider time per service (used for the >24 hours in a day check)
CONSULT_MINUTES = {1: 10, 2: 20, 3: 30, 4: 45, 5: 60}
CODE_MINUTES = {
    "PHY-SESSION": 45, "LAB-CBC": 5, "LAB-GLU": 5, "LAB-UREA": 5, "LAB-CREAT": 5, "LAB-NA": 5,
    "PNL-BMP": 5, "RAD-XRAY": 15, "RAD-USG": 30, "RAD-MRI": 60, "INP-DAY": 0, "PHM-RX": 5,
    "DME-WC": 10, "DME-BP": 10,
}  # fmt: skip

TABLES = [
    "claims", "providers", "facilities", "members", "inpatient_stays", "procedure_codes",
    "panel_components", "investigations",
]  # fmt: skip


def haversine_km(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    p1, l1, p2, l2 = map(math.radians, (lat1, lon1, lat2, lon2))
    h = math.sin((p2 - p1) / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin((l2 - l1) / 2) ** 2
    return 6371 * 2 * math.asin(math.sqrt(h))


def minutes_for(code: str, level: float | None) -> int:
    if level is not None and not pd.isna(level):
        return CONSULT_MINUTES[int(level)]
    return CODE_MINUTES.get(code, 15)


def load_tables(db_path: str | Path) -> dict[str, pd.DataFrame]:
    con = sqlite3.connect(db_path)
    try:
        return {t: pd.read_sql_query(f"SELECT * FROM {t}", con) for t in TABLES}
    finally:
        con.close()


@dataclass
class Context:
    providers: pd.DataFrame
    facilities: pd.DataFrame
    members: pd.DataFrame
    stays: pd.DataFrame
    panels: dict[str, list[str]]
    provider_specialty: dict[str, str]
    facility_coords: dict[str, tuple[float, float]]
    level5: pd.DataFrame  # index provider_id: specialty, n, share
    investigations: pd.DataFrame = field(default_factory=pd.DataFrame)
    peer_baselines: dict[str, tuple[float, float, int] | None] = field(default_factory=dict)
    util_p95: dict[str, float] = field(default_factory=dict)

    @classmethod
    def build(cls, tables: dict[str, pd.DataFrame]) -> Context:
        claims = tables["claims"]
        providers = tables["providers"]
        specialty = dict(zip(providers.provider_id, providers.specialty, strict=True))
        panels = (
            tables["panel_components"].groupby("panel_code").component_code.apply(list).to_dict()
        )
        fac = tables["facilities"]
        coords = {
            f: (lat, lon) for f, lat, lon in zip(fac.facility_id, fac.lat, fac.lon, strict=True)
        }

        levelled = claims[claims.code_level.notna()]
        level5 = (
            levelled.assign(is5=levelled.code_level == 5, specialty=levelled.provider_id.map(specialty))
            .groupby("provider_id")
            .agg(specialty=("specialty", "first"), n=("is5", "size"), share=("is5", "mean"))
        )
        ctx = cls(
            providers=providers,
            facilities=fac,
            members=tables["members"],
            stays=tables["inpatient_stays"],
            panels=panels,
            provider_specialty=specialty,
            facility_coords=coords,
            level5=level5,
            investigations=tables.get("investigations", pd.DataFrame()),
        )
        ctx.peer_baselines = {pid: ctx._baseline(pid) for pid in level5.index}
        ctx.util_p95 = ctx._utilization_p95(claims)
        return ctx

    def _baseline(self, provider_id: str) -> tuple[float, float, int] | None:
        """Leave-one-out level-5 baseline (mean, std, peers) or None if peers are too few."""
        row = self.level5.loc[provider_id]
        peers = self.level5[
            (self.level5.specialty == row.specialty)
            & (self.level5.index != provider_id)
            & (self.level5.n >= MIN_CONSULT_CLAIMS)
        ]
        if len(peers) < MIN_PEERS:
            return None
        return float(peers.share.mean()), max(float(peers.share.std(ddof=1)), STD_FLOOR), len(peers)

    def _utilization_p95(self, claims: pd.DataFrame) -> dict[str, float]:
        out = claims[claims.claim_type == "outpatient"]
        out = out.assign(
            specialty=out.provider_id.map(self.provider_specialty),
            month=out.service_date.str[:7],
        )
        counts = out.groupby(["specialty", "member_id", "month"]).size().rename("n").reset_index()
        return {
            spec: float(g.n.quantile(0.95))
            for spec, g in counts.groupby("specialty")
            if len(g) >= MIN_MEMBER_MONTHS
        }
