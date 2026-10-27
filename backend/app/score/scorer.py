"""Combine component scores into the risk score (weighted average over assessed components)."""

from app.score.components import round_half_up
from app.score.model import ComponentScore, CriticalSignal, Rating, RiskScore
from app.score.weights import (
    AMBER_FROM,
    COMPONENTS,
    CRITICAL_FROM,
    MIN_COVERAGE_PCT,
    RED_FROM,
    SCORE_RULES_VERSION,
    WEIGHTS_VERSION,
)


def _rating(total: float | None, coverage_pct: int) -> Rating:
    """Return the colour of a total, or insufficient when coverage is too low."""
    if total is None or coverage_pct < MIN_COVERAGE_PCT:
        return "insufficient"
    if total >= RED_FROM:
        return "red"
    return "amber" if total >= AMBER_FROM else "green"


def combine(supplier_id: str, components: list[ComponentScore]) -> RiskScore:
    """Return the risk score from one score per component.

    Components that are not assessed are excluded from the total, and the coverage shows how
    much of the weight was assessed. Critical signals are listed but never change the rating.

    Args:
        supplier_id: Supplier id.
        components: Exactly one ComponentScore per component name.

    Returns:
        The score with total, rating, coverage, components and critical signals.

    Raises:
        ValueError: If a component is missing or repeated.
    """
    if sorted(comp.name for comp in components) != sorted(COMPONENTS):
        raise ValueError("combine needs each component exactly once")
    ordered = sorted(components, key=lambda comp: COMPONENTS.index(comp.name))
    assessed = [comp for comp in ordered if comp.assessed and comp.value is not None]
    weight = sum(comp.weight for comp in assessed)
    total = (
        round_half_up(sum(comp.weight * (comp.value or 0.0) for comp in assessed) / weight)
        if weight
        else None
    )
    critical = [
        CriticalSignal(component=comp.name, value=comp.value or 0.0, reason=comp.reason)
        for comp in assessed
        if (comp.value or 0.0) >= CRITICAL_FROM
    ]
    return RiskScore(
        supplier_id=supplier_id,
        total=total,
        rating=_rating(total, weight),
        # Weights v1 sum to 100, so the assessed weight is the coverage in percent.
        coverage_pct=weight,
        components=ordered,
        critical_signals=critical,
        weights_version=WEIGHTS_VERSION,
        score_rules_version=SCORE_RULES_VERSION,
    )
