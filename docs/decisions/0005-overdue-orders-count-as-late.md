# 0005: Overdue undelivered orders count as late deliveries (score rules v2)

Date: 27 Oct 2026. Status: accepted (user decision).

## Context
Security review finding SEC-067 (docs/security-review.md). Score rules v1 counted only *completed* deliveries in
the delivery component. A supplier that stops delivering therefore looks better, not worse: its overdue orders
are invisible, it drops below the minimum of 10 deliveries, and the component becomes "not assessed". Coverage
falls and no red or critical signal appears, which is exactly the failure the score exists to show.

This rule was decided on principle, from the finding, before the new scores were computed. It was not chosen to
move any supplier to a particular colour.

## Decision
- In the delivery component (on-time rate of the last 6 calendar months and its trend against the 6 before), an
  open order whose promised date has passed and that has no delivery counts as a **late delivery**. "Passed" is
  judged as of the snapshot date (`snapshot_at` of the v_deliveries snapshot), so the evidence explains it.
- Open orders that are not yet due are not counted.
- A supplier with overdue undelivered orders is always assessed, even with no completed deliveries and below
  the 10-delivery minimum. The minimum still applies when nothing is overdue.
- The reason line names the overdue orders, e.g. "On-time rate 32 % over the last 6 months (57 deliveries,
  6 overdue undelivered), 73 % in the 6 months before."
- `delivery_history` applies the same rule. It reports `overdue_undelivered` per month and in total, so the tool's
  numbers match the score's.
- Weights stay v1. A new `score_rules_version` "v2" sits next to `weights_version` on every RiskScore
  (v1 = decision 0004).

## Consequences
- A supplier that stops delivering now scores high on delivery and shows a critical signal, instead of
  disappearing from coverage.
- Scores change for suppliers with overdue orders. On the seed-42 data and the 2026-10-27 snapshot:
  - S0003: delivery stays 100 (on-time 32 % over 57 counted deliveries, 6 overdue undelivered, vs 73 %); total
    35.9, unchanged.
  - S0005: delivery 0 -> 4.8 (on-time 92 % over 39, 2 overdue undelivered, vs 90 %); total 23.6 -> 25.0.
  - Colours are unchanged. They were not a goal.
- "Overdue" depends on the snapshot date. Tests pin `snapshot_at` (2026-10-27), so results do not drift with
  the CI date.
- Any later change to how sub-scores are derived bumps `score_rules_version` and gets its own decision record.
