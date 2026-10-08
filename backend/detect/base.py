"""Finding and Rule base types shared by every detector."""

from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import asdict, dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pandas as pd

    from backend.detect.context import Context

SEVERITIES = ("low", "medium", "high")


@dataclass
class Finding:
    """One detector output: {entity_id, detector, score, severity, reason, evidence_ids}."""

    entity_id: str
    detector: str
    score: float
    severity: str
    reason: str
    evidence_ids: list[str] = field(default_factory=list)

    def __post_init__(self) -> None:
        if not self.evidence_ids:
            raise ValueError("a finding must link to at least one evidence ID")
        if not 0.0 <= self.score <= 1.0:
            raise ValueError(f"score must be within 0..1, got {self.score}")
        if self.severity not in SEVERITIES:
            raise ValueError(f"severity must be one of {SEVERITIES}, got {self.severity}")
        if not self.entity_id or not self.detector or not self.reason:
            raise ValueError("entity_id, detector and reason are required")
        self.score = round(float(self.score), 4)

    def to_dict(self) -> dict:
        return asdict(self)


class Rule(ABC):
    """A claim-level rule. Drop a subclass into rules/ and the engine loads it."""

    name: str
    severity: str = "medium"  # default severity; a finding may use a different level

    @abstractmethod
    def evaluate(self, claims: pd.DataFrame, ctx: Context) -> list[Finding]:
        """Return findings for the claims. Never decide or deny anything: only recommend."""
