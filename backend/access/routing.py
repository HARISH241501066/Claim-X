"""Send each case to the unit that covers its main provider's city.

For a ring, the main provider is the one with the most flagged claims in the case. A case whose
city no unit covers stays unrouted and is visible to the admin only. Routing never overwrites an
assignment: an existing assignee stays whatever the units or the data look like later.
"""

from __future__ import annotations

import logging
import sqlite3
from pathlib import Path

from backend.access.store import AccessStore

log = logging.getLogger("claimshield.access")
MARKS = 500


def main_provider(case, db_path: Path) -> str | None:
    providers = [e for e in case.entity_ids if e.startswith("PRV-")]
    if case.primary_entity.startswith("PRV-"):
        return case.primary_entity
    if not providers:
        return None
    if len(providers) == 1 or not case.flagged_claim_ids:
        return providers[0]
    con = sqlite3.connect(db_path)
    try:
        counts: dict[str, int] = {}
        ids = case.flagged_claim_ids
        for i in range(0, len(ids), MARKS):
            chunk = ids[i : i + MARKS]
            rows = con.execute(
                f"SELECT provider_id, COUNT(*) FROM claims WHERE claim_id IN ({','.join('?' * len(chunk))}) "
                "GROUP BY provider_id", chunk,
            ).fetchall()  # fmt: skip
            for provider, n in rows:
                counts[provider] = counts.get(provider, 0) + n
    finally:
        con.close()
    ranked = [p for p in providers if p in counts]
    return max(ranked, key=lambda p: (counts[p], p), default=providers[0]) if ranked else providers[0]


def provider_city(db_path: Path, provider_id: str | None) -> str | None:
    if not provider_id:
        return None
    con = sqlite3.connect(db_path)
    try:
        row = con.execute("SELECT city FROM providers WHERE provider_id = ?", (provider_id,)).fetchone()
    finally:
        con.close()
    return row[0] if row else None


def unit_for_city(city: str | None, units: list[dict]) -> int | None:
    if not city:
        return None
    for unit in units:
        if city.strip().lower() in {c.strip().lower() for c in unit["region"]}:
            return unit["id"]
    return None


def route_cases(cases, db_path: Path, store: AccessStore) -> dict[str, int | None]:
    """Route every case that has no assignee yet. Returns case -> unit id (None: unrouted)."""
    units = store.list_units()
    existing = store.assignments()
    routed: dict[str, int | None] = {}
    for case in cases:
        row = existing.get(case.case_id)
        if row and (row["assignee_user_id"] is not None or row["unit_id"] is not None):
            routed[case.case_id] = row["unit_id"]  # already placed: keep it
            continue
        unit = unit_for_city(provider_city(db_path, main_provider(case, db_path)), units)
        store.route(case.case_id, unit)
        routed[case.case_id] = unit
        if unit is None:
            log.info("case %s is unrouted: no unit covers its provider's city", case.case_id)
    return routed
