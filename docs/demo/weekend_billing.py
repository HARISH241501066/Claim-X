"""DEMO RULE: a busy weekend day.

This file is NOT part of the product. It is a ready-made plug-in to show that the rule engine is
adaptable: copy it into backend/detect/rules/ and the next pipeline run picks it up with no change
to the engine, the API or the screens. Delete it from there to remove the rule again.

It flags a provider that bills at least 6 claims on a single Saturday or Sunday, when clinics are
normally quiet. It only recommends a review; it never decides anything.
"""

from __future__ import annotations

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context

MIN_CLAIMS_ON_A_WEEKEND_DAY = 6  # a provider's claims on one Saturday or Sunday


class WeekendBillingRule(Rule):
    name = "weekend_billing"
    severity = "low"  # informational: it adds evidence to a case without raising its severity

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        weekend = pd.to_datetime(claims.service_date).dt.dayofweek >= 5
        findings = []
        for (provider, day), group in claims[weekend].groupby(["provider_id", "service_date"], sort=True):
            if len(group) < MIN_CLAIMS_ON_A_WEEKEND_DAY:
                continue
            findings.append(
                Finding(
                    entity_id=provider,
                    detector=self.name,
                    score=min(1.0, len(group) / 10),
                    severity="low",
                    reason=(
                        f"Provider {provider} billed {len(group)} claims on {day}, a weekend day, "
                        f"at or above the {MIN_CLAIMS_ON_A_WEEKEND_DAY} this rule looks for. "
                        "Unusual weekend volume that warrants review."
                    ),
                    evidence_ids=sorted(group.claim_id),
                )
            )
        return findings
