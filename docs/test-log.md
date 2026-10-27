# Test log

One line per feature branch. Full per-test results are in docs/test-runs/. CI results for every push are on the
GitHub Actions run page (job summary) with the JUnit and coverage reports as artifacts.

| Date | Branch | Unit (pass/fail/skip/xfail) | Integration | Coverage | Details |
|---|---|---|---|---|---|
| 2026-10-26 | chore/ci | 17/0/0/0 | not run (no services touched) | 98 % | [details](test-runs/2026-10-26-chore-ci.md) |
| 2026-10-27 | feat/core | 130/0/0/10 | not run (no services touched) | 97 % | [details](test-runs/2026-10-27-feat-core.md) |
| 2026-10-27 | feat/llm-client | 392/0/0/12 | 5/5 live Nightshift (final run; cache-hit failure fixed) | 97 % | [details](test-runs/2026-10-27-feat-llm-client.md) |
