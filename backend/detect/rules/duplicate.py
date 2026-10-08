"""Same member, provider, procedure code and date billed more than once."""

from __future__ import annotations

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context

KEYS = ["member_id", "provider_id", "procedure_code", "service_date"]


class DuplicateRule(Rule):
    name = "duplicate"
    severity = "medium"

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        repeated = claims[claims.duplicated(KEYS, keep=False)]
        findings = []
        for (member, provider, code, day), group in repeated.groupby(KEYS, sort=True):
            ids = sorted(group.claim_id)
            identical = group.billed_amount.nunique() == 1
            findings.append(
                Finding(
                    entity_id=provider,
                    detector=self.name,
                    score=0.9 if identical else 0.6,
                    severity="high" if identical else "medium",
                    reason=(
                        f"{len(ids)} claims for member {member}, code {code}, by provider "
                        f"{provider} on {day} ({', '.join(ids)}); "
                        f"{'identical billed amounts' if identical else 'differing amounts'}. "
                        "Suspicious repeat billing that warrants review."
                    ),
                    evidence_ids=ids,
                )
            )
        return findings
