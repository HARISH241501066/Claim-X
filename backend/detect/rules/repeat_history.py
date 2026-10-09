"""Prior confirmed investigation followed by a clear rise in recent claim volume."""

from __future__ import annotations

import logging

import pandas as pd

from backend.detect.base import Finding, Rule
from backend.detect.context import Context

log = logging.getLogger("claimx.detect.repeat_history")
RECENT_MONTHS = 2
MIN_PRIOR_MONTHS = 3
MIN_RATIO = 1.5  # recent monthly volume versus the earlier monthly average
MIN_RECENT_CLAIMS = 20  # ignore tiny providers where a ratio means little


class RepeatHistoryRule(Rule):
    name = "repeat_history"
    severity = "medium"

    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        inv = ctx.investigations
        months = sorted(claims.service_date.str[:7].unique())
        if inv.empty or len(months) < RECENT_MONTHS + MIN_PRIOR_MONTHS:
            log.info("Insufficient data: no investigation history or too few months")
            return []
        recent, prior = months[-RECENT_MONTHS:], months[:-RECENT_MONTHS]
        recent_start = f"{recent[0]}-01"
        past = inv[
            (inv.outcome == "confirmed")
            & (inv.entity_type == "provider")
            & (inv.closed_date < recent_start)
        ]
        monthly = (
            claims.assign(month=claims.service_date.str[:7])
            .groupby(["provider_id", "month"])
            .size()
            .unstack(fill_value=0)
            .reindex(columns=months, fill_value=0)
        )
        findings = []
        for provider in sorted(past.entity_id.unique()):
            if provider not in monthly.index:
                continue
            prior_avg = float(monthly.loc[provider, prior].mean())
            recent_avg = float(monthly.loc[provider, recent].mean())
            if prior_avg <= 0 or recent_avg * RECENT_MONTHS < MIN_RECENT_CLAIMS:
                continue
            ratio = recent_avg / prior_avg
            if ratio < MIN_RATIO:
                continue
            case = past[past.entity_id == provider].sort_values("closed_date").iloc[-1]
            recent_claims = claims[
                (claims.provider_id == provider) & claims.service_date.str[:7].isin(recent)
            ]
            findings.append(
                Finding(
                    entity_id=provider,
                    detector=self.name,
                    score=min(1.0, ratio / 4),
                    severity="high" if ratio >= 3 else "medium",
                    reason=(
                        f"Provider {provider} had a confirmed investigation ({case.case_id}, "
                        f"closed {case.closed_date}) and its monthly claim volume rose to "
                        f"{recent_avg:.1f} over the last {RECENT_MONTHS} months from "
                        f"{prior_avg:.1f} earlier ({ratio:.1f}×). Suspicious pattern that "
                        "warrants review."
                    ),
                    evidence_ids=[case.case_id, *sorted(recent_claims.claim_id)],
                )
            )
        return findings
