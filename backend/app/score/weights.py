"""Weights v1 and rating thresholds of the risk score (architecture 6.5).

These are documented design choices, decided before any supplier was scored. They are never
tuned to get a particular colour for a demo supplier; a change needs a new WEIGHTS_VERSION
and an entry in docs/decisions.
"""

from collections.abc import Mapping
from types import MappingProxyType
from typing import Literal

ComponentName = Literal["delivery", "concentration", "financial", "country", "weather"]
COMPONENTS: tuple[ComponentName, ...] = (
    "delivery",
    "concentration",
    "financial",
    "country",
    "weather",
)

WEIGHTS_VERSION = "v1"
# Version of the rules that turn data into sub-scores (decision records 0004, 0005).
# v2: an open order past its due date without a delivery counts as a late delivery (SEC-067).
SCORE_RULES_VERSION = "v2"
WEIGHTS: Mapping[str, int] = MappingProxyType(
    {"delivery": 25, "concentration": 25, "financial": 20, "country": 15, "weather": 15}
)

# Rating of the weighted average (0-100, higher is riskier): green < 35 <= amber < 60 <= red.
AMBER_FROM = 35
RED_FROM = 60
# Below this share of assessed weight the score is "insufficient", never green.
MIN_COVERAGE_PCT = 50
# Any assessed component at or above this sub-score is listed as a critical signal. It does
# not change the rating; the UI shows both (e.g. "Amber · critical: delivery").
CRITICAL_FROM = 80
