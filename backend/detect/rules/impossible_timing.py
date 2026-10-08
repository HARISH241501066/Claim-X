"""Physically implausible timing: a member in two distant cities, or a provider over 24 hours."""

from __future__ import annotations

import itertools

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context, haversine_km, minutes_for

DISTANT_KM = 250.0  # claims carry dates only, so any same-day pair this far apart is flagged
MAX_MINUTES_PER_DAY = 24 * 60


class ImpossibleTimingRule(Rule):
    name = "impossible_timing"
    severity = "medium"

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        return self._member_travel(claims, ctx) + self._provider_hours(claims, ctx)

    def _member_travel(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        findings = []
        for (member, day), group in claims.groupby(["member_id", "service_date"], sort=True):
            facilities = sorted(group.facility_id.unique())
            if len(facilities) < 2:
                continue
            far = max(
                (
                    (haversine_km(*ctx.facility_coords[a], *ctx.facility_coords[b]), a, b)
                    for a, b in itertools.combinations(facilities, 2)
                ),
                default=(0.0, "", ""),
            )
            if far[0] <= DISTANT_KM:
                continue
            findings.append(
                Finding(
                    entity_id=member,
                    detector=self.name,
                    score=min(1.0, far[0] / 1500),
                    severity="medium",
                    reason=(
                        f"Member {member} has services on {day} at facilities {far[1]} and "
                        f"{far[2]}, about {far[0]:.0f} km apart; with dates only, this "
                        "cannot be reconciled and warrants review."
                    ),
                    evidence_ids=sorted(group.claim_id),
                )
            )
        return findings

    def _provider_hours(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        timed = claims[claims.claim_type == "outpatient"].copy()
        timed["minutes"] = [
            minutes_for(c, lv) for c, lv in zip(timed.procedure_code, timed.code_level, strict=True)
        ]
        findings = []
        for (provider, day), group in timed.groupby(["provider_id", "service_date"], sort=True):
            total = int(group.minutes.sum())
            if total <= MAX_MINUTES_PER_DAY:
                continue
            findings.append(
                Finding(
                    entity_id=provider,
                    detector=self.name,
                    score=min(1.0, total / (2 * MAX_MINUTES_PER_DAY)),
                    severity="high",
                    reason=(
                        f"Provider {provider} billed {total / 60:.1f} hours of services on "
                        f"{day}, more than 24 hours in a day. Suspicious volume that "
                        "warrants review."
                    ),
                    evidence_ids=sorted(group.claim_id),
                )
            )
        return findings
