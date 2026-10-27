# Test run 2026-10-27, branch feat/zetaris-data

Zetaris registration (ERP over JDBC, EDGAR for 8 public suppliers, Eurostat for 6 countries), role-scoped risk
views in container `risk`, roles and grants (60_), the role-scoped Zetaris REST client (backend/app/zetaris), the
admin-key rename, scripts/zetaris_verify.py and the SEC-036 fix in scripts/verify_ciks.py. Tests written first and
run red, then green: generator, client (including the new query_timeout), verify script (including per-view failure
lines), story 1a/1b and signal facts, role descriptions. Security pass: 18 strict xfail tests for SEC-045 to 057
(each checked with --runxfail to fail for the stated reason). Coverage 97 %. ruff, ruff format and mypy strict clean.

Live runs as svc_risk / svc_buyer (role keys, never the admin key):
1. Before the timeout change (client default 20 s): 6 passed, 5 failed, 12 errors in 10 min. All failures were
   ZetarisUnavailable after two 20 s timeouts, on ERP views as well as live REST views.
2. With query_timeout 300 s (below): 21 passed, 2 failed in 22 min.
   - test_every_risk_view_has_rows[v_country_indicators]: upstream Eurostat 400, intermittent, fixed by Feature 5
     caching (Zetaris answered 400 after 348 s; the next zetaris_verify run read the view, 1686 rows).
   - test_story_1_revenue_facts_match_sec[S0005 FY2024]: EDGAR holds two FY2024 facts for Celanese, 10,280.0 M
     (FY2024 10-K) and 10,268.0 M (restated in the FY2025 10-K). Fixed: story 1b uses the restated figure (user
     decision) and the test checks the value from the latest filing.
   - Re-run after that fix (-k "story_1 or latest_filing"): 7 passed in 95 s (6 live story facts + the rule test).
3. zetaris_verify.py: first run FAIL (400 on one view, not named at the time; the script now names the view and
   checks the rest). Second run: exit 0 in 2 min 31 s, all 9 views:

| View | Rows | From | To |
| --- | --- | --- | --- |
| risk.v_suppliers_full | 12 | - | - |
| risk.v_suppliers_buyer | 12 | - | - |
| risk.v_open_orders_full | 141 | 2026-10-15 | 2026-12-11 |
| risk.v_open_orders_buyer | 141 | 2026-10-15 | 2026-12-11 |
| risk.v_part_sourcing | 50 | - | - |
| risk.v_deliveries | 1555 | 2025-04-27 | 2026-12-11 |
| risk.v_contracts_full | 12 | 2026-09-28 | 2029-10-23 |
| risk.v_supplier_revenue | 921 | 2016-12-31 | 2026-09-30 |
| risk.v_country_indicators | 1686 | 2015-01 | 2026-09 |

Slowest live tests: v_country_indicators 348 s, v_supplier_revenue 194 s, all-twelve-suppliers 124 s (ERP).
logs/errors.jsonl: not written (tests log to temporary directories); no unexpected ERROR lines.

## Unit and security tests

| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |
|---|---|---|---|---|---|---|
| 725 | 682 | 0 | 0 | 5 | 38 | 11.05s |


## Integration tests (Zetaris)

| Tests | Passed | Failed | Errors | Skipped | XFailed | Duration |
|---|---|---|---|---|---|---|
| 23 | 21 | 2 | 0 | 0 | 0 | 1324.21s |

| Test | Outcome | Seconds | Failure |
|---|---|---|---|
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_suppliers_full\] | passed | 74.18 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_suppliers_buyer\] | passed | 29.12 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_open_orders_full\] | passed | 33.68 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_open_orders_buyer\] | passed | 13.28 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_part_sourcing\] | passed | 16.98 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_deliveries\] | passed | 21.47 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_contracts_full\] | passed | 5.25 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_supplier_revenue\] | passed | 196.75 |  |
| backend.tests.integration.test_zetaris_views::test_every_risk_view_has_rows\[v_country_indicators\] | failed | 356.32 | risk = ZetarisClient(base_url='http://localhost:8888/api/v1.0', role='risk_manager') |
| backend.tests.integration.test_zetaris_views::test_all_twelve_suppliers_are_visible | passed | 184.61 |  |
| backend.tests.integration.test_zetaris_views::test_revenue_covers_the_eight_public_suppliers | passed | 74.78 |  |
| backend.tests.integration.test_zetaris_views::test_country_indicators_cover_six_countries_and_two_indicators | passed | 115.93 |  |
| backend.tests.integration.test_zetaris_views::test_buyer_reads_the_buyer_views | passed | 16.38 |  |
| backend.tests.integration.test_zetaris_views::test_buyer_cannot_see_prices\[SELECT unit_price_eur FROM risk.v_open_orders_full\] | passed | 7.57 |  |
| backend.tests.integration.test_zetaris_views::test_buyer_cannot_see_prices\[SELECT annual_value_eur FROM risk.v_suppliers_full\] | passed | 17.90 |  |
| backend.tests.integration.test_zetaris_views::test_buyer_cannot_see_prices\[SELECT buyer_notes FROM risk.v_contracts_full\] | passed | 15.51 |  |
| backend.tests.integration.test_zetaris_views::test_buyer_cannot_see_prices\[SELECT value_usd FROM risk.v_supplier_revenue\] | passed | 11.95 |  |
| backend.tests.integration.test_zetaris_views::test_story_1_revenue_facts_match_sec\[S0003-2025-04-01-2026-03-31-4713100000\] | passed | 33.38 |  |
| backend.tests.integration.test_zetaris_views::test_story_1_revenue_facts_match_sec\[S0003-2024-04-01-2025-03-31-4401600000\] | passed | 27.83 |  |
| backend.tests.integration.test_zetaris_views::test_story_1_revenue_facts_match_sec\[S0003-2026-04-01-2026-06-30-1484700000\] | passed | 22.06 |  |
| backend.tests.integration.test_zetaris_views::test_story_1_revenue_facts_match_sec\[S0003-2025-04-01-2025-06-30-1075500000\] | passed | 16.58 |  |
| backend.tests.integration.test_zetaris_views::test_story_1_revenue_facts_match_sec\[S0005-2025-01-01-2025-12-31-9544000000\] | passed | 17.46 |  |
| backend.tests.integration.test_zetaris_views::test_story_1_revenue_facts_match_sec\[S0005-2024-01-01-2024-12-31-10280000000\] | failed | 15.24 | risk = ZetarisClient(base_url='http://localhost:8888/api/v1.0', role='risk_manager') |
