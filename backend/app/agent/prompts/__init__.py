"""Versioned agent prompts (architecture 6.6): rules only, never secrets or access rules.

Each prompt is a file <name>.<version>.md whose first line is "# <name> prompt <version>".
PROMPTS pins the version each agent uses; a change to a prompt is a new file and version.
"""

from dataclasses import dataclass
from functools import cache
from pathlib import Path

PROMPT_DIR = Path(__file__).resolve().parent
PROMPTS: dict[str, str] = {
    "guard": "v1",
    "planner": "v1",
    "research": "v1",
    "evaluator": "v1",
    "recommender": "v1",
    "writer": "v1",
}


@dataclass(frozen=True)
class Prompt:
    """A loaded prompt.

    Attributes:
        name: Agent name.
        version: Prompt version (recorded in the trace).
        text: The prompt text.
    """

    name: str
    version: str
    text: str


@cache
def load_prompt(name: str) -> Prompt:
    """Return the pinned prompt of an agent.

    Args:
        name: Agent name (a key of PROMPTS).

    Returns:
        The prompt.

    Raises:
        KeyError: If the agent has no prompt (names never become paths otherwise).
    """
    version = PROMPTS[name]
    text = (PROMPT_DIR / f"{name}.{version}.md").read_text(encoding="utf-8")
    return Prompt(name=name, version=version, text=text.strip())
