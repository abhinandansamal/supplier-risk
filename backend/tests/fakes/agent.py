"""Fixture that runs whole investigations against the fake model, Zetaris and case board."""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from tests.fakes.board import FakeBoardClient
from tests.fakes.llm import FakeLLM
from tests.fakes.zetaris import TODAY

from app.agent.graph import Investigator
from app.agent.models import InvestigationResult
from app.core.settings import Settings
from app.security.identity import UserContext
from app.tools.cache import SnapshotCache

RISK = UserContext(user_id="u-risk", role="risk_manager")
QUESTION = "Why is Microchip (S0003) risky?"


@dataclass
class Case:
    """One finished investigation and the fakes it ran against.

    Attributes:
        result: The investigation result.
        llm: The fake model with every call it received.
        board: The fake case board with every stored event.
    """

    result: InvestigationResult
    llm: FakeLLM
    board: FakeBoardClient

    @property
    def events(self) -> list[Any]:
        """Return the stored case board events of this investigation."""
        return self.board.cases.get(self.result.investigation_id, [])


Investigate = Callable[..., Awaitable[Case]]


@pytest.fixture
def investigate(
    make_settings: Callable[..., Settings], snapshot_cache: SnapshotCache, tmp_path: Path
) -> Investigate:
    """Return a function that runs one investigation and returns it with its fakes."""

    async def run(
        question: str = QUESTION,
        user: UserContext = RISK,
        script: dict[str, list[Any]] | None = None,
        board: FakeBoardClient | None = None,
        **settings: Any,  # noqa: ANN401
    ) -> Case:
        llm = FakeLLM(script)
        board = board or FakeBoardClient()
        investigator = Investigator(
            make_settings(**settings),
            llm=llm,
            cache=snapshot_cache,
            board_client=board,
            today=TODAY,
            trace_dir=tmp_path / "runs",
        )
        return Case(await investigator.run_investigation(user, question), llm, board)

    return run
