"""Tests for app.llm.budget: per-investigation model call budget."""

import logging

import pytest

from app.llm.budget import DEFAULT_CALL_BUDGET, CallBudget
from app.llm.errors import BudgetExhausted


def _events(caplog: pytest.LogCaptureFixture, event: str) -> list[logging.LogRecord]:
    return [r for r in caplog.records if getattr(r, "event", None) == event]


def test_default_limit_is_15() -> None:
    assert DEFAULT_CALL_BUDGET == 15
    assert CallBudget().limit == 15


def test_counts_used_and_remaining() -> None:
    budget = CallBudget(limit=3)
    budget.consume()
    assert budget.used == 1
    assert budget.remaining == 2


def test_raises_when_exhausted_without_counting_more() -> None:
    budget = CallBudget(limit=2)
    budget.consume()
    budget.consume()
    with pytest.raises(BudgetExhausted):
        budget.consume()
    assert budget.used == 2
    assert budget.remaining == 0


def test_warns_once_at_80_percent(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO)
    budget = CallBudget(limit=5)
    for _ in range(3):
        budget.consume()
    assert _events(caplog, "call_budget_warning") == []
    budget.consume()
    warnings = _events(caplog, "call_budget_warning")
    assert len(warnings) == 1
    assert warnings[0].levelno == logging.WARNING
    assert warnings[0].used == 4
    assert warnings[0].limit == 5
    budget.consume()
    assert len(_events(caplog, "call_budget_warning")) == 1


def test_exhaustion_is_logged_as_warning(caplog: pytest.LogCaptureFixture) -> None:
    budget = CallBudget(limit=1)
    budget.consume()
    with pytest.raises(BudgetExhausted):
        budget.consume()
    records = _events(caplog, "budget_exhausted")
    assert len(records) == 1
    assert records[0].levelno == logging.WARNING


@pytest.mark.parametrize("limit", [0, -1])
def test_rejects_non_positive_limit(limit: int) -> None:
    with pytest.raises(ValueError, match="limit"):
        CallBudget(limit=limit)
