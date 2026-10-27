# Zetaris setup: data, roles and keys

How the data is registered in Zetaris and how the app gets role-scoped access
(docs/architecture.md sections 3 and 10.1). The app never uses the admin key: the Zetaris
client refuses to start for a role whose key is missing or equals the admin key.

## Keys

| Variable in .env | Zetaris user | Used by | Notes |
| --- | --- | --- | --- |
| `ZETARIS_ADMIN_API_KEY` | admin | setup scripts only | Admin, setup only, never used at runtime. Rename the old `ZETARIS_APP_API_KEY` to this. |
| `ZETARIS_KEY_BUYER` | svc_buyer | app, role buyer | Zetaris role `buyer` |
| `ZETARIS_KEY_RISK` | svc_risk | app, roles risk_manager and director; `scripts/zetaris_verify.py`; integration tests | Zetaris role `risk_manager` |

| App role | Zetaris access |
| --- | --- |
| buyer | svc_buyer: the five buyer views (no prices, order values, contract values or notes) |
| risk_manager | svc_risk: every view in container `risk` |
| director | svc_risk (same data as the risk manager; directors approve high-impact actions in the app) |
| auditor | none: reads only the app database (audit log, evidence records), no Zetaris key |

Row scope (a buyer sees only their own category) is a bound parameter in the tool layer.
Zetaris roles and grants limit the objects and columns.

## Order of steps

1. **ERP datasource (you, as admin, in the SQL Editor).** Run the two statements below one at a
   time, without the semicolon, after you replace the placeholder with the real
   `ERP_READER_PASSWORD`. The erp-db must be running (`scripts/erp_up.sh up`).

   ```sql
   CREATE DATASOURCE erp_pg DESCRIBE BY "Synthetic ERP, read-only role zetaris_reader" OPTIONS (
     jdbcdriver "org.postgresql.Driver",
     jdbcurl "jdbc:postgresql://host.docker.internal:5433/erp",
     username "zetaris_reader",
     password "<ERP_READER_PASSWORD>",
     fetchsize "1000"
   )
   ```

   ```sql
   REGISTER DATASOURCE TABLES FROM erp_pg
   ```

   Check with `data_pipeline/zetaris_sql/10_erp_datasource_select.sql` (read-only).
2. **EDGAR, Eurostat and the risk views (runner, admin key from the getting-started `.env.local`).**
   From `../getting-started`, for each of `20_edgar_suppliers`, `30_eurostat_countries`,
   `40_risk_views`:

   ```bash
   python3 scripts/run_sql.py --dry-run ../supplier-risk/data_pipeline/zetaris_sql/<NN>_create.sql
   python3 scripts/run_sql.py --skip-exists ../supplier-risk/data_pipeline/zetaris_sql/<NN>_create.sql
   ```

   EDGAR: on HTTP 429, wait 60 s and run the file once more with `--skip-exists`.
3. **Roles and grants (you, as admin, in the SQL Editor, after review).** Run each statement of
   `data_pipeline/zetaris_sql/60_roles_grants_create.sql` one at a time (listed below).
4. **Users, role assignment, masking and keys**: the checklist below.
5. **Verify**: `uv run python scripts/zetaris_verify.py` and
   `uv run pytest -q -m integration -k zetaris`.

### Statements of 60_roles_grants_create.sql

```sql
CREATE ROLE 'buyer' DESCRIBE BY 'Category_buyers,buyer_views_without_prices_or_contract_values'
CREATE ROLE 'risk_manager' DESCRIBE BY 'Risk_managers_and_directors,all_risk_views'
GRANT SELECT ON VIEW TABLE risk.v_suppliers_buyer TO ROLE 'buyer'
GRANT SELECT ON VIEW TABLE risk.v_open_orders_buyer TO ROLE 'buyer'
GRANT SELECT ON VIEW TABLE risk.v_part_sourcing TO ROLE 'buyer'
GRANT SELECT ON VIEW TABLE risk.v_deliveries TO ROLE 'buyer'
GRANT SELECT ON VIEW TABLE risk.v_country_indicators TO ROLE 'buyer'
GRANT SELECT ON VIEW TABLE risk.v_suppliers_full TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_suppliers_buyer TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_open_orders_full TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_open_orders_buyer TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_part_sourcing TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_deliveries TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_contracts_full TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_supplier_revenue TO ROLE 'risk_manager'
GRANT SELECT ON VIEW TABLE risk.v_country_indicators TO ROLE 'risk_manager'
```

Check with `60_roles_grants_select.sql` (`SHOW ROLES`, `SHOW GRANT ROLE ... ON ALL`).

## Checklist: users, roles, masking, keys (Zetaris UI, as admin unless noted)

Done on 2026-10-27; the boxes record what was verified.

- [x] **Roles**: the statements of `60_roles_grants_create.sql` run one at a time in the SQL
      Editor. `CREATE ROLE ... DESCRIBE BY` accepts only descriptions without spaces or colons;
      all 14 GRANTs return Success.
- [x] **Create user `svc_buyer@supplier-risk.local`** in the UI (Management → Users), with role
      `buyer`.
- [x] **Create user `svc_risk@supplier-risk.local`** the same way, with role `risk_manager`.
      Check in SQL: `SHOW USER ASSIGNED TO ROLE 'buyer'`,
      `SHOW USER ASSIGNED TO ROLE 'risk_manager'`.
- [x] **Least privilege**: on Management → Permissions nothing is ticked for either user; the SQL
      Workspace works without it. No direct grants on `erp_pg`, `sr_edgar` or `sr_eurostat`:
      the views work without any grant on the raw tables, and the Data Explorer shows no
      datasources for the service users.
- [ ] **Mask price columns (defence in depth).** The buyer has no grant on the full views. In
      addition, open Configure Columns (Schema Store View, NDP Fabric Builder) on the source
      tables and add policies for role `buyer`:
      - `erp_pg.purchase_orders.unit_price_eur`: **Hide** (Masked works only on string columns)
      - `erp_pg.contracts.annual_value_eur`: **Hide**
      - `erp_pg.contracts.buyer_notes`: **Hide** (or **Masked**)
- [x] **One API key per user, created while logged in as that user**: log out, log in as
      `svc_buyer@supplier-risk.local`, Management → API Tokens → create (the key is shown once),
      paste it into `.env` as `ZETARIS_KEY_BUYER`. Repeat as `svc_risk@supplier-risk.local` for
      `ZETARIS_KEY_RISK`. A key has the rights of the user who created it, so never create these
      keys as admin.
- [x] **Rename** `ZETARIS_APP_API_KEY` to `ZETARIS_ADMIN_API_KEY` in `.env` (the app no longer
      reads the old name).
- [ ] **Verify**: `uv run python scripts/zetaris_verify.py` prints the row table (exit 0), and
      `uv run pytest -q -m integration -k zetaris` passes, including `test_buyer_cannot_see_prices`.
- [x] **SQL Editor history (SEC-044), verified 2026-10-27**: the admin history still holds the
      `CREATE DATASOURCE` statement with the reader password in plain text, but it is not visible
      to the service users: svc_buyer's History shows only its own 5 queries.
- [x] **Negative and positive checks** while logged in as each service user (section below):
      all passed on 2026-10-27.

If svc_risk can only read the views after it also gets SELECT on the source tables (Zetaris
checks rights on the sources), tell the developer before granting anything: the buyer must
then rely on the column policies above, and the grants file changes.

## Checks per service user (after 60_ and the checklist)

Log in to the Zetaris UI as the service user and run each query on its own in the SQL Editor
(one statement, no semicolon). FAIL means Zetaris refuses it (no access); WORK means it returns
a row.

### svc_buyer

| Query | Expected |
| --- | --- |
| `SELECT supplier_id, annual_value_eur, buyer_notes FROM risk.v_contracts_full LIMIT 1` | FAIL |
| `SELECT po_id, unit_price_eur, order_value_eur FROM risk.v_open_orders_full LIMIT 1` | FAIL |
| `SELECT supplier_id, annual_value_eur, buyer_notes FROM erp_pg.contracts LIMIT 1` | FAIL |
| `SELECT po_id, unit_price_eur FROM erp_pg.purchase_orders LIMIT 1` | FAIL |
| `SELECT po_id, supplier_id, part_id, qty, due_date FROM risk.v_open_orders_buyer LIMIT 1` | WORK |

### svc_risk

| Query | Expected |
| --- | --- |
| `SELECT supplier_id, annual_value_eur, buyer_notes FROM erp_pg.contracts LIMIT 1` | FAIL |
| `SELECT COUNT(1) AS row_count FROM risk.v_suppliers_full` | WORK (12) |
| `SELECT COUNT(1) AS row_count FROM risk.v_suppliers_buyer` | WORK (12) |
| `SELECT COUNT(1) AS row_count FROM risk.v_open_orders_full` | WORK (141) |
| `SELECT COUNT(1) AS row_count FROM risk.v_open_orders_buyer` | WORK (141) |
| `SELECT COUNT(1) AS row_count FROM risk.v_part_sourcing` | WORK (50) |
| `SELECT COUNT(1) AS row_count FROM risk.v_deliveries` | WORK (1555) |
| `SELECT COUNT(1) AS row_count FROM risk.v_contracts_full` | WORK (12) |
| `SELECT COUNT(1) AS row_count FROM risk.v_supplier_revenue` | WORK (921) |
| `SELECT COUNT(1) AS row_count FROM risk.v_country_indicators` | WORK (1686) |

Counts are the values measured on 2026-10-27 (docs/data-contract.md); EDGAR and Eurostat are
live, so v_supplier_revenue and v_country_indicators grow when new periods are published.

### Both users

- [x] SQL Editor → History shows none of the admin's queries (in particular no
      `CREATE DATASOURCE` statement; SEC-044). Verified 2026-10-27.

Result on 2026-10-27: every FAIL query was refused with "does not have privileges", every WORK
query returned rows with the counts above.

Timings (as svc_risk, 2026-10-27): `risk.v_country_indicators` took 3.5 minutes (12 live
Eurostat REST calls) and `risk.v_open_orders_buyer` 13 s. The app must not query the live REST
views per request; caching is mandatory before the demo and the load test (docs/devlog.md).

If a FAIL query works, stop and report it: the role has a grant or column policy it should not
have. If a WORK query fails for svc_risk only because the raw tables are not granted, see the
note above before granting anything.

## Terms of the public sources

- **SEC EDGAR**: declared User-Agent (name and email) on every request, at most 10 requests/s,
  query live, no bulk redistribution of the extracted data
  (https://www.sec.gov/os/webmaster-faq#developers).
- **Eurostat**: free re-use with attribution ("Source: Eurostat, une_rt_m / prc_hicp_minr"),
  https://ec.europa.eu/eurostat/about/policies/copyright. No key, no rate limit.
