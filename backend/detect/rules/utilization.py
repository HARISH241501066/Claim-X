"""Services per member per month above twice the specialty peer 95th percentile."""

from __future__ import annotations

import logging

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context

log = logging.getLogger("claimx.detect.utilization")
P95_MULTIPLIER = 2.0


class UtilizationRule(Rule):
    name = "utilization"
    severity = "medium"

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        out = claims[claims.claim_type == "outpatient"].copy()
        out["specialty"] = out.provider_id.map(ctx.provider_specialty)
        out["month"] = out.service_date.str[:7]
        findings = []
        for (specialty, member, month), group in out.groupby(
            ["specialty", "member_id", "month"], sort=True
        ):
            p95 = ctx.util_p95.get(specialty)
            if p95 is None:
                log.info("Insufficient data: no utilization baseline for %s", specialty)
                continue
            threshold = P95_MULTIPLIER * p95
            count = len(group)
            if count <= threshold:
                continue
            ratio = count / threshold
            findings.append(
                Finding(
                    entity_id=member,
                    detector=self.name,
                    score=min(1.0, ratio / 3),
                    severity="high" if ratio >= 2 else "medium" if ratio >= 1.5 else "low",
                    reason=(
                        f"Member {member} had {count} {specialty} services in {month}, above "
                        f"twice the peer 95th percentile ({p95:.0f}, limit {threshold:.0f}). "
                        "Suspicious utilization that warrants review."
                    ),
                    evidence_ids=sorted(group.claim_id),
                )
            )
        return findings
