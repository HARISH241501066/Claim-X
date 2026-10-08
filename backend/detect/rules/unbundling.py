"""Three or more component codes of one panel billed for a member on one date."""

from __future__ import annotations

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context

MIN_COMPONENTS = 3


class UnbundlingRule(Rule):
    name = "unbundling"
    severity = "medium"

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        findings = []
        for panel, components in sorted(ctx.panels.items()):
            parts = claims[claims.procedure_code.isin(components)]
            for (provider, member, day), group in parts.groupby(
                ["provider_id", "member_id", "service_date"], sort=True
            ):
                distinct = group.procedure_code.nunique()
                if distinct < MIN_COMPONENTS:
                    continue
                findings.append(
                    Finding(
                        entity_id=provider,
                        detector=self.name,
                        score=distinct / len(components),
                        severity="high" if distinct == len(components) else "medium",
                        reason=(
                            f"{distinct} of {len(components)} components of panel {panel} were "
                            f"billed separately for member {member} on {day} by provider "
                            f"{provider} instead of the panel code. Suspicious unbundling that "
                            "warrants review."
                        ),
                        evidence_ids=sorted(group.claim_id),
                    )
                )
        return findings
