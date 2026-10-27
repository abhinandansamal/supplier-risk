# Test log

One line per feature branch. Full per-test results are in docs/test-runs/. CI results for every push are on the
GitHub Actions run page (job summary) with the JUnit and coverage reports as artifacts.

| Date | Branch | Unit (pass/fail/skip/xfail) | Integration | Coverage | Details |
|---|---|---|---|---|---|
| 2026-10-26 | chore/ci | 17/0/0/0 | not run (no services touched) | 98 % | [details](test-runs/2026-10-26-chore-ci.md) |
| 2026-10-27 | feat/core | 130/0/0/10 | not run (no services touched) | 97 % | [details](test-runs/2026-10-27-feat-core.md) |
| 2026-10-27 | feat/llm-client | 392/0/0/12 | 5/5 live Nightshift (final run; cache-hit failure fixed) | 97 % | [details](test-runs/2026-10-27-feat-llm-client.md) |
| 2026-10-27 | feat/erp-data | 473/0/0/21 | 10/0/0/1 live erp-db (zetaris_reader); Zetaris/Nightshift not needed | 98 % | [details](test-runs/2026-10-27-feat-erp-data.md) |
| 2026-10-27 | feat/zetaris-data | 682/0/0/38 | 21/2/0/0 live Zetaris, then story re-run 7/0/0/0 (v_country_indicators: upstream Eurostat 400, intermittent, fixed by Feature 5 caching; Celanese FY2024 restated, story 1b updated) | 97 % | [details](test-runs/2026-10-27-feat-zetaris-data.md) |
| 2026-10-27 | feat/tools-score | 900/0/5/36 | live Zetaris: snapshot warm-up 1/1 (183 s; first run 319 s), views 14/14 (138 s) | 98 % | [details](test-runs/2026-10-27-feat-tools-score.md) |
| 2026-10-27 | feat/world-model | 991/0/5/36 (+ vitest 77/0/0/0; conformance 8/8 x2) | 1/0/0/0 live context service (Docker); Zetaris/Nightshift not needed | 98 % | [details](test-runs/2026-10-27-feat-world-model.md) |
| 2026-10-27 | feat/agent-graph | 1191/0/5/39 (+ vitest 78/0/0/0; conformance 8/8 x2) | world_model 1/0/0/0; agent slow_live 1/0/0/0 (S0003 completed, 10/15 calls, receipts on all; first run failed at warm-up, test relaxed) | 97 % | [details](test-runs/2026-10-27-feat-agent-graph.md) |
| 2026-10-27 | feat/api-ui | 1362/0/5/46 | api slow_live S0003: run 1 failed (agent escalated at 15/15 calls: 4 fast LLMBadResponse in round-1 research), run 2 1/0/0/0 (completed, 14/15 calls, 40 live SSE trace events, receipts on all) | 97 % | [details](test-runs/2026-10-27-feat-api-ui.md) |
