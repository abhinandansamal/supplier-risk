# 0001: Call Nemotron models by exact id through Nightshift

Date: 25 Oct 2026. Status: accepted.

## Context
meterless/auto routed a test request to Nemotron 3.5 Bolt E4B and can also route to local models.
The NVIDIA Nemotron award needs clear evidence that Nemotron powers the result.

## Decision
The app sends exact model ids to Nightshift:
- METERLESS_MODEL = nvidia/nemotron-3.5-lightning-30b-a3b (research agents, Evaluator; tool calls verified)
- METERLESS_DEEP_MODEL = nvidia/nemotron-3-ultra-550b-a55b (Planner, Recommender, final report)
- METERLESS_FAST_MODEL = nvidia/nemotron-3.5-bolt-e4b (quick checks)

## Consequences
Each receipt shows the routed Nemotron model. We do not use meterless/fusion (it multiplies calls under the 40 RPM limit).
