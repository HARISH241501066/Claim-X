"""Outpatient service dated inside the member's inpatient stay at a different facility."""

from __future__ import annotations

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context


class PhantomRule(Rule):
    name = "phantom"
    severity = "high"

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        outpatient = claims[claims.claim_type == "outpatient"]
        joined = outpatient.merge(
            ctx.stays, on="member_id", suffixes=("", "_stay"), how="inner"
        )
        hits = joined[
            (joined.service_date >= joined.admit_date)
            & (joined.service_date <= joined.discharge_date)
            & (joined.facility_id != joined.facility_id_stay)
        ]
        findings = []
        for row in hits.sort_values("claim_id").itertuples():
            findings.append(
                Finding(
                    entity_id=row.provider_id,
                    detector=self.name,
                    score=0.9,
                    severity="high",
                    reason=(
                        f"Outpatient claim {row.claim_id} for member {row.member_id} is dated "
                        f"{row.service_date}, inside their inpatient stay {row.stay_id} "
                        f"({row.admit_date} to {row.discharge_date}) at facility "
                        f"{row.facility_id_stay}, but was billed by provider {row.provider_id} "
                        f"at {row.facility_id}. Suspicious overlap that warrants review."
                    ),
                    evidence_ids=[row.claim_id, row.stay_id],
                )
            )
        return findings
