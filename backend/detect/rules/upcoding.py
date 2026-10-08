"""Provider's level-5 share is far above leave-one-out specialty peers (z > 3)."""

from __future__ import annotations

import logging

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import MIN_CONSULT_CLAIMS, Context

log = logging.getLogger("claimshield.detect.upcoding")
Z_THRESHOLD = 3.0
MIN_EXCESS = 0.25  # share must also exceed the peer mean by this much (practical significance)


class UpcodingRule(Rule):
    name = "upcoding"
    severity = "medium"

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        findings = []
        for provider_id, row in ctx.level5.iterrows():
            if row.n < MIN_CONSULT_CLAIMS:
                continue
            baseline = ctx.peer_baselines.get(provider_id)
            if baseline is None:
                log.info("Insufficient data: too few %s peers for %s", row.specialty, provider_id)
                continue
            mean, std, peers = baseline
            z = (row.share - mean) / std
            if z <= Z_THRESHOLD or row.share - mean < MIN_EXCESS:
                continue
            level5 = claims[(claims.provider_id == provider_id) & (claims.code_level == 5)]
            findings.append(
                Finding(
                    entity_id=provider_id,
                    detector=self.name,
                    score=min(1.0, z / 10),
                    severity="high" if z >= 6 else "medium",
                    reason=(
                        f"Provider {provider_id} billed level 5 on {row.share:.0%} of "
                        f"{int(row.n)} {row.specialty} visits versus a peer mean of {mean:.0%} "
                        f"({peers} peers), z-score {z:.1f}. Suspicious coding pattern that "
                        "warrants review."
                    ),
                    evidence_ids=sorted(level5.claim_id),
                )
            )
        return findings
