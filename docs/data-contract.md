# Data contract

One row per table the agent may use. Grain, keys, freshness, licence and the risk component each
feeds. The app reads only the views in Zetaris container `risk` (section "App-facing views");
the source tables below feed them. Setup and roles: `docs/zetaris-setup.md`.

## Synthetic ERP (Postgres 16, host port 5433)

Generated with seed 42 by `data_pipeline/synthetic_erp/generate.py`; labelled synthetic everywhere
(see `docs/data-card.md`). Zetaris reads it with the read-only role `zetaris_reader` (SELECT only).

| Table | Grain | Keys | Freshness | Licence | Feeds |
| --- | --- | --- | --- | --- | --- |
| suppliers | one row per supplier | PK supplier_id | regenerated on demand | synthetic | all components |
| parts | one row per part | PK part_id | regenerated on demand | synthetic | concentration |
| part_sourcing | one row per part and supplier | PK (part_id, supplier_id); FKs to parts, suppliers | regenerated on demand | synthetic | concentration |
| purchase_orders | one row per purchase order | PK po_id; FKs to suppliers, parts, part_sourcing | last 18 months up to the run date, plus open orders due within 45 days | synthetic | concentration, delivery |
| deliveries | one row per non-cancelled purchase order | PK/FK po_id; delivered_date is NULL while open | as purchase_orders | synthetic | delivery |
| contracts | one row per supplier | PK/FK supplier_id; buyer_notes is free text (untrusted) | regenerated on demand | synthetic | concentration (value, end date) |

## SEC CIK checks

Public suppliers in `data_pipeline/suppliers.yaml` were verified on 2026-10-27 with
`scripts/verify_ciks.py` against SEC EDGAR:

1. `GET https://data.sec.gov/submissions/CIK<10 digits>.json` must answer 200 (the entity name is recorded).
2. `GET https://data.sec.gov/api/xbrl/companyconcept/CIK<10 digits>/us-gaap/<tag>.json` must answer 200 for
   `Revenues` or, if that is missing, `RevenueFromContractWithCustomerExcludingAssessedTax`.

The declared User-Agent was passed through the environment only and is not stored in the repository.
Requests were limited to 2 per second. CIKs were looked up from the SEC ticker file
(`https://www.sec.gov/files/company_tickers.json`), not typed from memory.

| Supplier | Name in suppliers.yaml | CIK | SEC entity name | Revenue tag found | Result |
| --- | --- | --- | --- | --- | --- |
| S0001 | Texas Instruments Incorporated | 0000097476 | TEXAS INSTRUMENTS INC | RevenueFromContractWithCustomerExcludingAssessedTax | PASS |
| S0002 | Analog Devices, Inc. | 0000006281 | ANALOG DEVICES INC | Revenues (stale; now RevenueFromContractWithCustomerExcludingAssessedTax, see below) | PASS |
| S0003 | Microchip Technology Incorporated | 0000827054 | MICROCHIP TECHNOLOGY INC | Revenues (stale; now RevenueFromContractWithCustomerExcludingAssessedTax, see below) | PASS |
| S0004 | Dow Inc. | 0001751788 | DOW INC. | Revenues | PASS |
| S0005 | Celanese Corporation | 0001306830 | Celanese Corp | RevenueFromContractWithCustomerExcludingAssessedTax | PASS |
| S0006 | Alcoa Corporation | 0001675149 | Alcoa Corp | RevenueFromContractWithCustomerExcludingAssessedTax | PASS |
| S0007 | Nucor Corporation | 0000073309 | NUCOR CORP | Revenues (stale; now RevenueFromContractWithCustomerExcludingAssessedTax, see below) | PASS |
| S0008 | United Parcel Service, Inc. | 0001090727 | UNITED PARCEL SERVICE INC | Revenues (stale; now RevenueFromContractWithCustomerExcludingAssessedTax, see below) | PASS |

All 8 picks passed; none had to be replaced. Re-check on 2026-10-27 with the freshness test
(SEC-036 fix: freshest tag by latest period end, at most 275 days old): `Revenues` had stopped for
S0002 (last 2018-11), S0003 (2019-06), S0007 (2024-09) and S0008 (2021-09); suppliers.yaml now
uses `RevenueFromContractWithCustomerExcludingAssessedTax` for them. All 8 pass: latest period
ends S0001 2026-09-30, S0002 2026-08-01, S0003 2026-06-30, S0004 2026-09-30 (`Revenues`),
S0005 2026-06-30, S0006 2026-06-30, S0007 2026-07-04, S0008 2026-06-30. The 4 private suppliers (S0009 to S0012) are fictional
and have no CIK. Note: the check proves the revenue tag exists, not that it has current values;
the EDGAR feature checks freshness when it registers the REST tables.

## Zetaris registration (data_pipeline/zetaris_sql)

| File | Registers | Run by |
| --- | --- | --- |
| 10_erp_datasource | JDBC datasource `erp_pg` (host.docker.internal:5433/erp as `zetaris_reader`) and its 6 tables | user, as admin, in the SQL Editor (password placeholder) |
| 20_edgar_suppliers | Lightning database `SR_EDGAR`, container `sr_edgar`: REST table `sNNNN_revenue_facts` and view `sNNNN_revenue` per public supplier | getting-started runner |
| 30_eurostat_countries | Lightning database `SR_EUROSTAT`, container `sr_eurostat`: REST table and `_series` view per country and indicator (12) | getting-started runner |
| 40_risk_views | container `risk`: the 9 app-facing views | getting-started runner |
| 60_roles_grants | roles `buyer`, `risk_manager` and SELECT grants on risk views | user, as admin, after review |

Files 20 to 60 are generated by `data_pipeline/zetaris_sql/generate.py` from `suppliers.yaml`;
`--check` (and a unit test) fails when a committed file is stale.

## SEC EDGAR (companyconcept, live REST)

| Object | Grain | Keys | Freshness | Licence / terms | Feeds |
| --- | --- | --- | --- | --- | --- |
| sr_edgar.sNNNN_revenue (8 views) | one row per reported fact (a period in one filing) | (accession_number, period_start, period_end) | live on every query (no cache yet) | SEC terms: declared User-Agent, at most 10 requests/s, no bulk redistribution; filer-authored facts | financial |

Revenue tags per supplier are in `suppliers.yaml` (verified above). A filing repeats earlier
periods, and 10-K filings also carry quarterly facts, so `fiscal_year` is the fiscal year of the
filing, not of the period. Pick periods by `period_start`/`period_end` (a full year spans about
365 days). A fiscal Q4 often has no standalone fact (only the 10-K year): derive it as the
year minus the 9-month year-to-date fact. REST tables are named after the tag
(`sNNNN_<tag slug>_facts`).

**Tools always use the latest filing.** A period can hold several facts when a later filing
restates it (Celanese FY2024: 10,280.0 M USD in the FY2024 10-K, 10,268.0 M as restated in the
FY2025 10-K). For each (supplier_id, period_start, period_end) the tool takes the fact from the
latest filing: highest `fiscal_year`, then `fiscal_period` FY > Q3 > Q2 > Q1. The evidence record
names that filing (`fiscal_year`, `fiscal_period`, `form_type`).

Rows and period_end range in Zetaris on 2026-10-27: S0001 136 (2016-12-31 to 2026-09-30),
S0002 109 (2017-10-28 to 2026-08-01), S0003 99 (2018-03-31 to 2026-06-30), S0004 94 (2018-03-31
to 2026-09-30), S0005 104 (2016-12-31 to 2026-06-30), S0006 132 (2016-12-31 to 2026-06-30),
S0007 143 (2017-12-31 to 2026-07-04), S0008 104 (2016-12-31 to 2026-06-30).

## Eurostat (dissemination API, JSON-stat, live REST)

| Object | Grain | Keys | Range (curl, 2026-10-27) | Licence | Feeds |
| --- | --- | --- | --- | --- | --- |
| sr_eurostat.une_rt_m_XX_series (6) | one row per month | period (YYYY-MM) | 2015-01 to 2026-08, 140 months per country | Eurostat copyright notice: free re-use with attribution | country |
| sr_eurostat.prc_hicp_minr_XX_series (6) | one row per month | period (YYYY-MM) | 2015-01 to 2026-09, 141 months per country | as above | country |

XX is DE, PL, IT, CZ, FR, ES. Filters: une_rt_m `sex=T&age=TOTAL&unit=PC_ACT&s_adj=SA`
(unemployment, % of labour force, seasonally adjusted); prc_hicp_minr `coicop18=TOTAL&unit=RCH_A`
(HICP all items, annual rate of change). `prc_hicp_manr` stopped at 2025-12 (last update
2026-02-06), so the ECOICOP ver. 2 dataset `prc_hicp_minr` is used; the indicator keeps the name
`hicp_annual_rate`. Every URL was tested with curl first (an invalid code returns empty values,
not an error). History starts at 2015-01 (`sinceTimePeriod`).

## App-facing views (container risk)

Only these views are queried by the app. Buyer views never carry `unit_price_eur`,
`order_value_eur`, `annual_value_eur` or `buyer_notes`. Rows and ranges below were measured on
2026-10-27 with `40_risk_views_select.sql` (admin, setup); `scripts/zetaris_verify.py` repeats
them with the svc_risk key once it exists. ERP tables in `erp_pg` match erp-db: suppliers 12,
parts 24, part_sourcing 50, purchase_orders 1603, deliveries 1555, contracts 12.

| View | Rows | Date column | From | To |
| --- | --- | --- | --- | --- |
| v_suppliers_full | 12 | - | - | - |
| v_suppliers_buyer | 12 | - | - | - |
| v_open_orders_full | 141 | due_date | 2026-10-15 | 2026-12-11 |
| v_open_orders_buyer | 141 | due_date | 2026-10-15 | 2026-12-11 |
| v_part_sourcing | 50 | - | - | - |
| v_deliveries | 1555 | promised_date | 2025-04-27 | 2026-12-11 |
| v_contracts_full | 12 | end_date | 2026-09-28 | 2029-10-23 |
| v_supplier_revenue | 921 | period_end | 2016-12-31 | 2026-09-30 |
| v_country_indicators | 1686 (6 x (140 + 141)) | period | 2015-01 | 2026-09 |

Open orders due before the run date (from 2026-10-15) are overdue; one contract already ended
(2026-09-28).

| View | Columns | Grain | Keys | Roles | Feeds |
| --- | --- | --- | --- | --- | --- |
| v_suppliers_full | supplier_id, name, cik, country_iso, category, site_lat, site_lon, annual_value_eur, contract_end_date | supplier | supplier_id | risk_manager | all |
| v_suppliers_buyer | as v_suppliers_full without annual_value_eur | supplier | supplier_id | buyer, risk_manager | all |
| v_open_orders_full | po_id, supplier_id, part_id, part_description, criticality, category, qty, unit_price_eur, order_value_eur, due_date | open purchase order | po_id | risk_manager | concentration |
| v_open_orders_buyer | as v_open_orders_full without unit_price_eur and order_value_eur | open purchase order | po_id | buyer, risk_manager | concentration |
| v_part_sourcing | part_id, supplier_id, part_description, category, criticality, share_pct, is_single_source | part and supplier | (part_id, supplier_id) | buyer, risk_manager | concentration |
| v_deliveries | po_id, supplier_id, part_id, category, status, promised_date, delivered_date, days_late | non-cancelled purchase order | po_id | buyer, risk_manager | delivery |
| v_contracts_full | supplier_id, annual_value_eur, end_date, buyer_notes (untrusted text) | supplier | supplier_id | risk_manager | concentration |
| v_supplier_revenue | supplier_id, cik, fiscal_year, fiscal_period, period_start, period_end, form_type, value_usd | reported revenue fact | (supplier_id, period_start, period_end, fiscal_year) | risk_manager | financial |
| v_country_indicators | country_iso, indicator (unemployment_rate, hicp_annual_rate), period, value | country, indicator, month | (country_iso, indicator, period) | buyer, risk_manager | country |

`days_late` is `delivered_date - promised_date` in days (negative = early, NULL while open).
`v_supplier_revenue` maps each CIK to its supplier_id from `suppliers.yaml`; private suppliers
(S0009 to S0012) have no rows (financial = unknown, never 0).

## Worsening country (story 3), checked 2026-10-27

Latest-6-month average minus previous-6-month average, from the same Eurostat URLs
(to be confirmed on `risk.v_country_indicators` once Zetaris SQL runs):

| Country | Unemployment change (pp), 2026-03..08 vs 2025-09..2026-02 | HICP change (pp), 2026-04..09 vs 2025-10..2026-03 |
| --- | --- | --- |
| FR | +0.25 | +1.65 |
| PL | +0.20 | +0.68 |
| IT | +0.12 | +1.93 |
| DE | +0.08 | +0.55 |
| CZ | +0.02 | +0.08 |
| ES | -0.12 | +1.08 |

CZ (the generator default) is the least worsening country on both indicators.
