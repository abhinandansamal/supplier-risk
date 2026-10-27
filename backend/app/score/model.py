"""Pydantic models of the risk score."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.score.weights import ComponentName

Rating = Literal["green", "amber", "red", "insufficient"]


class ComponentScore(BaseModel):
    """One score component.

    Attributes:
        name: Component name.
        weight: Weight v1 of the component.
        assessed: False when the component could not be assessed (it is then excluded).
        value: Sub-score 0-100 (higher is riskier), None when not assessed.
        evidence_ids: Evidence records the value (or the reason it is missing) rests on.
        reason: One line that explains the value.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: ComponentName
    weight: int
    assessed: bool
    value: float | None = Field(default=None, ge=0, le=100)
    evidence_ids: list[str]
    reason: str


class CriticalSignal(BaseModel):
    """An assessed component whose sub-score is at or above CRITICAL_FROM.

    Attributes:
        component: Component name.
        value: Its sub-score.
        reason: The component's one-line reason.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    component: ComponentName
    value: float
    reason: str


class RiskScore(BaseModel):
    """Deterministic risk score of one supplier.

    Attributes:
        supplier_id: Supplier id.
        total: Weighted average over the assessed components (0-100), None if none assessed.
        rating: green, amber, red, or insufficient (coverage below MIN_COVERAGE_PCT).
        coverage_pct: Share of the total weight that was assessed.
        components: All five components, assessed or not.
        critical_signals: Assessed components at or above CRITICAL_FROM.
        weights_version: Version of the weights used.
        score_rules_version: Version of the rules that turn data into sub-scores.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    supplier_id: str
    total: float | None
    rating: Rating
    coverage_pct: int
    components: list[ComponentScore]
    critical_signals: list[CriticalSignal]
    weights_version: str
    score_rules_version: str

    @property
    def label(self) -> str:
        """Return the short UI label, e.g. "Amber · coverage 65 % · critical: delivery"."""
        parts = [self.rating.capitalize(), f"coverage {self.coverage_pct} %"]
        if self.critical_signals:
            parts.append("critical: " + ", ".join(s.component for s in self.critical_signals))
        return " · ".join(parts)
