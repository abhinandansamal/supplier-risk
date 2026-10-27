# Development log

Times are Europe/Berlin. Secrets are never written here.

## Fri 23 Oct 2026: Data layer
- Installed Zetaris locally (zetaris-platform v2.4.3, UI on port 3000, REST API on 8888).
- Created the admin account and API key; ran the PokeAPI recipe (Lightning database POKEAPI_REST).
- getting-started preflight: PASS (REST datasources HTTP 200, SELECT 1). Lightning DBs: NY_TAXI, POKEAPI_REST, TPCDS_DB.

## Sat 24 Oct 2026: Build tool
- Organisers made Cursor optional (Discord, 22 Oct). Chose Claude Code (existing subscription).
- Claude Code: user settings deny reading .env files; Manual permission mode by default.
- Claude Code in getting-started: read-only REST check passed (SELECT 1, SHOW LIGHTNING DATABASES).

## Sun 25 Oct 2026: Power layer and repo
- Installed Nightshift (Mac M1-M4 edition). NVIDIA key stored in Nightshift Providers only.
- Lanes: Race = Nemotron 3.5 Bolt E4B, Nemotron 3.5 Lightning 30B A3B, Nemotron 3 Ultra 550B A55B; Verify = Lightning.
- Runtime tests (http://127.0.0.1:8088/v1):
  - GET /v1/models: routing modes and Nemotron models listed.
  - Chat on meterless/auto: OK, routed to Bolt E4B, provider nvidia, receipt status ok.
  - Lightning by exact model id with a run_sql tool: returned a proper tool call.
  - Ultra by exact model id: OK, but output contains reasoning text before "</think>"; the client must strip it.
- Decision: pin models by exact id (see docs/decisions/0001-pin-nemotron-models.md).
- Risk noted: NVIDIA free tier is limited to 40 requests/minute; app will use a shared rate limiter, 429 retries and a call budget.
- Created repo supplier-risk with uv (Python 3.12), .gitignore and .env.example.
- Build check (setup guide 3.1): Claude Code created hello.py, ran `uv run python hello.py` (output: hello), then deleted it.

## Mon 26 Oct 2026: Quality gates, test reporting and CI (branch chore/ci)
- Added ruff (Google docstrings, complexity 10), strict mypy and pytest config with JUnit and coverage output in pyproject.toml.
- Added scripts/test_report.py (JUnit XML to Markdown, always exits 0), pre-commit config and the GitHub Actions job "ci"
  (actions pinned to full commit SHAs, verified through the GitHub API).
- Tests added: 16 for the report script (8 of them for SEC-001/002, written first and red), 1 smoke test. Results: 17 passed, coverage 98 %. Integration: not run (no services touched).
- Security review (security-breaker): no Critical or High. Medium: SEC-001 (Markdown injection through newlines in test ids,
  FIXED), SEC-002 (bad time attribute breaks "always exits 0", FIXED), SEC-009 (gitleaks only local; no CI secret scan,
  FIXED).
  Low: SEC-003 to SEC-008, SEC-010. See docs/security-review.md.
- Decisions: stdlib ElementTree with noqa (trusted pytest output; defusedxml deferred, SEC-003); CI runs commands through
  `uv run`; added read-only workflow permissions and a timeout.
- SEC-009 fixed: the pre-commit gitleaks hook scans only staged changes (0 commits scanned in CI), so CI instead downloads
  gitleaks v8.30.1 (pinned sha256, checked against the release checksums.txt) and runs `gitleaks git --redact` on the full
  history (fetch-depth: 0). Tested on throwaway repos: clean repo passes (exit 0), committed-then-removed fake token fails
  (exit 1); real repo history clean (5 commits). Branch protection on main is still a manual repo setting.

## Tue 27 Oct 2026: Application core (branch feat/core)
- Added settings (pydantic-settings, SecretStr, startup error names variables only), JSON logging with contextvars and a
  redaction filter on all handlers, the one error model, the FastAPI app factory (request-id + access-log middleware,
  16 KB body limit, /health, /ready with a readiness registry, /docs only in dev) and app.main:create for uvicorn.
- Tests added (130 passing + 10 strict xfail, written first, red then green): settings, logging, redaction, errors, API app, architecture
  rule (parses imports; self-tests with fake trees), main. Results: 130 passed, 10 xfailed, coverage 97 %. Integration: not run.
- Security review (security-breaker): no Critical or High. Medium: SEC-011 (redaction misses key=value, URL userinfo,
  cookies, api-key headers), SEC-013 (percent-decoded URL passwords), SEC-016 (/ready unauthenticated, uncached).
  Low: SEC-012, 014, 015, 017, 018. All recorded in docs/security-review.md.
- Fixed on request before the commit, test first: SEC-011 (redaction now covers password/secret/token/api_key
  key=value pairs and query params, URL user:password@ userinfo, Cookie/Set-Cookie and whole Authorization
  headers, wider Bearer charset; patterns are keyword-anchored and bounded, checked linear on 1 MB input),
  SEC-013 (secret_values adds the percent-decoded URL password and strips whitespace), SEC-017 (.env.example
  ships APP_ENV=prod). Still open and recorded: SEC-012, 014, 015, 016, 018 (10 strict-xfail tests).
- Deliberate choice (SEC-011): the Authorization and Cookie/Set-Cookie patterns redact the rest of the line (up to a
  quote or newline), so text after them on the same line is over-redacted. Safer than leaking; a key=value secret
  containing spaces is redacted only up to the first space.
- Decisions: default APP_ENV is prod (docs off); readiness returns 503 with failed check names only; request ids must
  match ^[A-Za-z0-9._-]{1,64}$; body limit is enforced while streaming (HTTPException 413 so FastAPI re-raises it);
  ruff treats `app` as first-party; app code imports `app.*` (backend is on pythonpath).
- Follow-up: SEC-016 (cache/protect /ready) before real routes exist. Local .env keeps APP_ENV=dev (user's own file).

## Tue 27 Oct 2026: Meterless Nightshift client (branch feat/llm-client)
- Added backend/app/llm: MeterlessClient (status, chat, embed) on httpx.AsyncClient, following the official "Add
  Meterless as a provider" contract (Runtime 0.90.0): Bearer token and meterless-app on every call, meterless-scope:
  user on chat, GET /v1/models decides online/unauthorized/offline (10 s), 180 s per attempt, no redirects followed.
  Shared AsyncRateLimiter (30/min, 2 concurrent), retries on 429/5xx/timeouts (max 4, exponential backoff + jitter,
  Retry-After capped at 60 s), deep -> default fallback once on persistent 429 (model_fallback), CallBudget (15, WARNING
  at 80 %, BudgetExhausted), reasoning stripped up to the last </think>, tool-call arguments parsed with json.loads only.
- ChatResult carries requested_model, routed_model, provider (winning_route.provider), receipt_id, cache_status and
  from_cache (meterless.cache.status), usage, latency_ms, retries, fallback_used; all of them (no content) are on the
  one INFO model_call line.
- Settings: METERLESS_BASE_URL is normalised to <host>/v1 (scheme optional, endpoint suffixes cut, never /v1/v1);
  meterless/auto and meterless/fusion rejected as model ids; rt-local rejected; app id must be lowercase.
- scripts/check_llm.py runs the five live checks and prints PASS/FAIL lines (ids only). Architecture test: tools/ and
  zetaris/ may not import llm/, and llm/ imports only core/.
- Tests (written first, red then green): settings, text, budget, limiter, client status/chat/embed/logging, check_llm,
  security. Unit + security: 392 passed, 12 xfailed, coverage 97 %. Integration (live Nightshift): 5/5 on the final run.
- Security review (security-breaker): no Critical or High. Fixed test first on request: SEC-019 (non-ASCII Retry-After
  crash), SEC-020 (RecursionError on nested JSON), SEC-021 (unclosed/upper-case think tags, finish_reason=length),
  SEC-022 (response byte cap, content/tool-call/embedding bounds, finite vectors), SEC-023 (per-attempt asyncio timeout
  and a 600 s total deadline), SEC-025, 026, 027, 028 (https unless local runtime), 030 (header-safe token). Open:
  SEC-024 (limiter bound to one event loop; per-process, so N workers exceed 40 RPM) and SEC-029 (embed not budgeted).
- Live finding: a repeated identical prompt was answered from Nightshift's exact cache (cache.status exact, 4 ms), and the
  cache keeps no tool_calls, so the tool-call test got an empty reply. The client now treats an empty reply (tools sent
  but no tool calls and no content, or 0 completion tokens) as a cache artefact: one retry with a "request-id: <uuid4>"
  system message, then LLMBadResponse (event model_empty_reply). Live prompts include a fresh uuid4.
- Decisions: receipts show requested_mode meterless/auto even for pinned ids, so routed_model comes from
  meterless.routed_model (then winning_route.model, then the response model) and provider from winning_route; retries,
  the fallback and the empty-reply retry do not use extra budget units (the limiter paces every HTTP attempt); status()
  bypasses the limiter (local runtime); plain http is allowed for loopback, localhost and host.docker.internal only;
  exception names follow the feature spec (noqa N818).
- Follow-up: SEC-024 (own the limiter in the app lifespan, single API worker or a shared store), SEC-029 (budget for
  embeddings and a cap on HTTP attempts per investigation), confirm whether GET /v1/models counts against the NVIDIA quota.
- cache-retry path not yet security-reviewed; covered in the red-team pass.

## 2026-10-27: feat/erp-data (synthetic ERP)
- What: suppliers.yaml (12 suppliers; 8 real US-listed with SEC-verified CIK and revenue tag, 4 fictional) with a
  pydantic loader; deterministic generator (Faker + numpy, seed 42, `today` argument) for suppliers, parts,
  part_sourcing, purchase_orders, deliveries, contracts, writing schema.sql, seed.sql and CSVs; docker-compose
  erp-db (postgres:16, 127.0.0.1:5433, named volume, healthcheck, init scripts) with the SELECT-only role
  zetaris_reader; scripts/erp_up.sh (generate/up/reset); scripts/verify_ciks.py; docs/data-contract.md and
  docs/data-card.md.
- Tests (red first, then green): suppliers.yaml schema, determinism (rows and files), foreign keys and primary keys,
  value ranges, the four planted stories, compose and role scripts (static), SEC verification with respx, and
  integration tests as zetaris_reader (counts; INSERT/UPDATE/DELETE/TRUNCATE/DROP/CREATE denied).
  Unit + security: 468 passed, 22 xfailed, coverage 98 %. Integration (live erp-db): 9 passed, 2 xfailed.
- Security review: no Critical or High. Open (Medium): SEC-031 reader can switch off read-only default, SEC-032 reader
  can alter its own role, SEC-033 no statement timeout or connection limit, SEC-034 erp_up.sh --out copies stale
  files, SEC-036 verify_ciks does not compare the tag with suppliers.yaml. Low: SEC-035, 037 to 043.
- Decisions: public picks TI, ADI, Microchip, Dow, Celanese, Alcoa, Nucor, UPS all passed (none replaced); site
  countries are synthetic; worsening_country (default CZ) overrides the yaml country of S0009; the DB admin user is
  `erp` to match the existing ERP_DATABASE_URL example; data_pipeline added to mypy and coverage; the SEC User-Agent
  is passed only through the environment.
- Fixed before merge (test first): SEC-031 (lo_* functions and TEMP revoked from PUBLIC) and SEC-033 (role connection
  limit and timeouts, container mem/cpu limits). The security-breaker agent now has the rule "never modify live
  services, databases or roles" (it altered zetaris_reader during its probe).
- Follow-up: SEC-034 and SEC-036 (small) before Zetaris registration; use a pooler or SQL guard for SEC-032;
  confirm worsening country against Eurostat in the next feature.

## 2026-10-27: feat/zetaris-data (Zetaris registration, risk views, role keys) - in progress
- TODO: Feature 5 must add CACHE TABLE (or the app's tool cache) for SR_EDGAR and SR_EUROSTAT, because the load
  test and the demo must never call SEC/Eurostat live per request. MANDATORY before the demo and the load test:
  measured 2026-10-27 as svc_risk in the SQL Editor, `SELECT COUNT(1) FROM risk.v_country_indicators` took
  3.5 minutes (live Eurostat, 12 REST calls) and `risk.v_open_orders_buyer` took 13 s (ERP over JDBC). The app's
  Zetaris query timeout is 20 s, so uncached Eurostat views cannot serve an investigation at all.
- Zetaris SQL engine (container zetaris-driver-1) found exited (code 56) since 25 Oct: the platform folder moved
  from ~/Projects/zetaris-platform to ~/Projects/open-agent-hackathon-2026/zetaris-platform, and the container
  still bind-mounts conf/spark-defaults.conf from the old path. The user fixes the install; registration waits.
- Worsening country (story 3) decided 2026-10-27 from Eurostat (fetched 2026-10-27, same URLs as the Zetaris REST
  tables): latest-6-month average minus previous-6-month average, in percentage points.
  Unemployment (une_rt_m, 2026-03..08 vs 2025-09..2026-02): FR +0.25, PL +0.20, IT +0.12, DE +0.08, CZ +0.02,
  ES -0.12. HICP annual rate (prc_hicp_minr, 2026-04..09 vs 2025-10..2026-03): IT +1.93, FR +1.65, ES +1.08,
  PL +0.68, DE +0.55, CZ +0.08. Decision: FR (worsens on both; CZ worsened least). S0009 moved to FR in
  suppliers.yaml with real coordinates of the Crolles industrial area, Isere (45.2833, 5.8833); new optional
  site_lat/site_lon in suppliers.yaml (used only while the site country matches); generator default FR; ERP
  regenerated (seed 42, today 2026-10-27; table sizes unchanged); the user resets erp-db.
- Registered in Zetaris (runner, --skip-exists): 30_eurostat_countries 26/26 ok; all 12 series match curl
  (une_rt_m 140 months 2015-01..2026-08, prc_hicp_minr 141 months 2015-01..2026-09). 20_edgar_suppliers 18/18 ok,
  no SEC 429. EDGAR freshness (rows, period_end range): S0001 136 2016-12..2026-09, S0002 15 2016-10..2018-11,
  S0003 17 2017-03..2019-06, S0004 94 2018-03..2026-09, S0005 104 2016-12..2026-06, S0006 132 2016-12..2026-06,
  S0007 66 2018-03..2024-09, S0008 219 2007-12..2021-09. The `Revenues` tag is stale for S0002, S0003 (story 1),
  S0007 and S0008 (filers moved to another tag; SEC-036 in practice). Fix pending the user's decision.
- SEC-036 fixed (test first, xfail removed): verify_ciks.py reads ZETARIS_USER_AGENT through settings (.env), reads
  the latest period_end of each candidate tag (Revenues, RevenueFromContractWithCustomer Excluding/Including
  AssessedTax), picks the freshest, and fails if suppliers.yaml has another tag or the data is older than 275 days.
- EDGAR REST tables are now named after the tag (sNNNN_<tag slug>_facts) and all EDGAR and risk views are
  CREATE OR REPLACE, so a tag change adds a table and repoints the view; nothing is dropped in Zetaris.
- S0009 renamed to "Grésivolt Électronique SAS" (fictional; 0 matches in recherche-entreprises.api.gouv.fr and a web
  search on 2026-10-27). ERP regenerated (seed 42, today 2026-10-27, sizes unchanged), ready for the user's reset.
- erp_pg registered by the user; Zetaris names tables erp_pg.<table>; counts match erp-db (12, 24, 50, 1603, 1555, 12); S0009 reads FR / Grésivolt. Tags of S0002,
  S0003, S0007, S0008 changed to RevenueFromContractWithCustomerExcludingAssessedTax; 20_ re-run (8 new tag-named
  tables, views replaced, 2 skipped, no 429); all 8 EDGAR views fresh (latest period ends match verify_ciks). 40_ run:
  10/10 ok; all 9 risk views return rows (v_supplier_revenue 921, v_country_indicators 1686).
- Microchip (S0003, story 1) from risk.v_supplier_revenue: FY2026 (2025-04-01..2026-03-31) 4,713.1 M USD vs FY2025
  4,401.6 M (+7.1 %); FY2025 vs FY2024 (7,634.4 M) -42.3 %; latest quarter 2026-04-01..06-30 1,484.7 M vs 1,075.5 M
  a year earlier (+38.0 %). Revenue is recovering, so "falling revenue" does not hold for the latest data; story
  wording unchanged until the user decides.
- Story decision (user, 2026-10-27), real data only:
  - Story 1a, S0003 Microchip: late deliveries rise while revenue recovers, so the risk is operational, not
    financial. EDGAR FY2026 (2025-04-01..2026-03-31) 4,713.1 M USD vs FY2025 (2024-04-01..2025-03-31) 4,401.6 M:
    +7.1 %; latest quarter (2026-04-01..06-30) 1,484.7 M vs (2025-04-01..06-30) 1,075.5 M: +38.0 % YoY. ERP
    late-delivery pattern kept.
  - Story 1b, S0005 Celanese: falling revenue FY2025 (2025-01-01..12-31) 9,544.0 M vs FY2024 10,268.0 M (as
    restated in the FY2025 10-K; first set to 10,280.0 M / -7.2 %, corrected below): -7.1 %,
    site country FR (the worsening country). No ERP change.
  - Signals the agent should find (seed 42, today 2026-10-27, pinned by unit tests): 34 of 141 open orders overdue
    (earliest due 2026-10-15, S0003 most with 6); the S0003 contract ended 2026-09-28 (the only one).
  - Rules: causes only as labelled hypotheses, never findings; risk-score weights/thresholds are never tuned to
    get a colour. Integration test test_story_1_revenue_facts_match_sec pins the six SEC facts.
  - No gold-case files exist yet (evals/gold is empty); the gold set should reuse these exact numbers.
- Roles and users (the user, as admin, 2026-10-27): 60_ run in the SQL Editor; CREATE ROLE accepted only
  descriptions without spaces or colons, and all 14 GRANTs succeeded. Users svc_buyer@supplier-risk.local (role
  buyer) and svc_risk@supplier-risk.local (role risk_manager) created in the UI; nothing ticked on the Permissions
  page (the SQL Workspace works without it). All per-user checks in docs/zetaris-setup.md passed: svc_buyer is
  refused ("does not have privileges") on v_contracts_full, v_open_orders_full, erp_pg.contracts and
  erp_pg.purchase_orders and reads v_open_orders_buyer; svc_risk is refused on erp_pg.contracts and reads all 9
  views (12, 12, 141, 141, 50, 1555, 12, 921, 1686). The views work without any grant on the raw erp_pg tables,
  the service users see no datasources in the Data Explorer, and svc_buyer's history shows only its own queries
  (SEC-044 history check verified). ZETARIS_KEY_BUYER and ZETARIS_KEY_RISK set in .env by the user.
- Timings as svc_risk: v_country_indicators 3.5 min (live Eurostat), v_open_orders_buyer 13 s (see the TODO
  above).
- Live runs with the role keys: with the client's 20 s timeout even ERP views timed out (ZetarisUnavailable).
  ZetarisClient got an optional query_timeout (default 20 s, max 600 s); zetaris_verify.py and the integration
  fixtures use 300 s, and tests on live SEC/Eurostat views are marked slow_live (run the rest with
  -m "integration and not slow_live"). Then: integration 21 passed, 2 failed in 22 min (v_country_indicators got
  a 400 after 348 s, not reproduced by the next verify run; Celanese FY2024 restated, below). zetaris_verify.py
  now names a failing view and checks the rest; second run exit 0 in 2.5 min with all 9 counts as in the setup
  doc. The app's 20 s default is borderline for every Zetaris view on this machine, not only the REST ones;
  Feature 5 must cache or pre-materialise before the tools are built.
- Celanese (S0005) FY2024 has two EDGAR facts: 10,280.0 M USD (FY2024 10-K) and 10,268.0 M (restated in the
  FY2025 10-K). Decision (user, 2026-10-27): story 1b uses the restated figure, FY2025 9,544.0 M vs FY2024
  10,268.0 M: -7.1 % (-7.05 %). v_supplier_revenue can hold several facts per period; tools always use the latest
  filing (highest fiscal_year, then FY > Q3 > Q2 > Q1; docs/data-contract.md), and the story test checks exactly
  that value.
- Tests: generator, client, verify script, settings, story/signal facts, role descriptions (unit); 23 Zetaris
  integration tests; 18 strict xfail security tests. Unit + security: 682 passed, 38 xfailed, coverage 97 %.
- Security (security-breaker, unit level only): no Critical/High; SEC-045 to SEC-051 Medium, SEC-052 to SEC-060
  Low, all open (docs/security-review.md). SEC-045 (no bound parameters / row scope) and SEC-047 (error JSON
  reads as zero rows) must be fixed before backend/app/tools. SEC-036 fixed; SEC-044 history isolation verified.
- Decisions: role keys only at runtime (admin key setup-only), director uses svc_risk, auditor no Zetaris key;
  FR is the worsening country; stories 1a/1b on real data only; tag-named EDGAR tables and CREATE OR REPLACE
  views, never DROP; role descriptions only [A-Za-z0-9_.,-].

## 2026-10-27: feat/tools-score (tools, snapshot cache, evidence, risk score v1)
- What: the tool catalogue of architecture 6.3 (all but propose_action; weather_extremes answers "not assessed"),
  the in-memory snapshot cache (app/tools/cache.py), EvidenceRecord/EvidenceLog, UserContext, and the
  deterministic risk score v1 (app/score/). It warms at startup without blocking it (FastAPI lifespan), and
  scripts/warm_cache.py does the same by hand. This resolves the Feature 5 caching TODO above: no request
  reads SEC/Eurostat live any more.
- SEC-045 fixed by design (decision 0004). Each of the 9 views is read whole with one constant
  `SELECT <columns> FROM risk.<view>`, and rows are filtered in Python. Zetaris enforces column scope through the
  role key; the tool layer enforces row scope (the buyer's category) from the session. SEC-047 fixed: an error
  JSON with HTTP 200 is ZetarisBadResponse, never "0 rows".
- Cache:
  - Keyed by (role key, view); single flight.
  - ERP: TTL 900 s, max stale 3600 s. SEC/Eurostat: TTL 6 h, max stale 24 h (user decision). Stale data is served
    while one background refresh runs; after max staleness, `source_unavailable`.
  - At most 2 loads at once (SNAPSHOT_MAX_CONCURRENCY), ERP first. Loads use ZETARIS_SNAPSHOT_TIMEOUT_SECONDS
    (300 s). The client row cap is now 5,000; a full snapshot is refused as possibly truncated.
- Live timings (svc_buyer/svc_risk, 2026-10-27): ERP views 2-17 s per load (ERP phase about 34 s for 11 loads,
  2 at a time), v_supplier_revenue 33 s, v_country_indicators 147 s. Full warm-up of 14 snapshots: 183-319 s.
- Score v1 decisions (user, 2026-10-27):
  - Weights 25/25/20/15/15. Thresholds green < 35, amber 35-59, red >= 60, insufficient below 50 % coverage.
  - Critical signals: any assessed component >= 80 is listed and never changes the rating
    (e.g. "Amber · critical: delivery").
  - Not assessed (lowers coverage): weather; private-supplier financials; financials for the buyer role;
    SEC/Eurostat when unavailable.
  - Sub-score scales are fixed in architecture 6.5. Weights and thresholds were not changed after seeing scores.
- Scores on the fake (seed-42 ERP; real story facts for S0001/3/5 only), for the record, not tuned:
  - S0003: 35.9, "Amber · coverage 85 % · critical: delivery". Delivery 100 (on-time 35 % vs 73 %), financial 0
    (+7.1 %), country PL 37, concentration 0.
  - S0005: 23.6, Green, coverage 85 %. Financial 35.3 (-7.1 %), country FR 66.3, delivery 0, concentration 12.1.
  - S0009 (private): coverage 65 %. A buyer sees S0003 at coverage 65 %, with delivery still critical.
- Tests (red first, then green): see docs/test-runs/2026-10-27-feat-tools-score.md. Unit + security (before the follow-up): 886
  passed, 37 xfailed, coverage 98 %. Live Zetaris: warm-up 1/1 (183 s after the fixes), views 14/14.
- Security (security-breaker): SEC-061..073, none Critical/High, no row-scope leak.
  - Fixed test first: 062 (NULL revenue scored as a collapse), 063/064 (malformed or NaN cells; now checked at load
    and dropped with a count), 065 (list ratings uncited), 068/069 (supplier names and part descriptions quoted as
    untrusted, text capped at 2,000), 070, 071, 072, 073 (ERP keeps a slot), and the backoff part of 061.
  - Open: 061 (request-time timeout and per-call deadline, agent feature), 066 (evidence aggregates for the claim
    verifier), 067 (undelivered orders as late: a score rule change, needs a user decision and weights v2).
- Follow-up decisions (user, 2026-10-27), tests first:
  - SEC-067 fixed with score rules v2 (decision 0005, decided on principle before the new scores were computed).
    An open order past its due date without a delivery counts as a late delivery (rate and trend), as of the
    snapshot date. A supplier with overdue orders is always assessed. delivery_history uses the same rule.
    Weights stay v1; RiskScore carries score_rules_version "v2".
  - New results, exactly as computed (seed-42 fake, snapshot 2026-10-27), not adjusted:
    - S0003: 35.9, "Amber · coverage 85 % · critical: delivery". Delivery 100 (on-time 32 % over 57, of which
      6 overdue undelivered, vs 73 %), concentration 0, financial 0 (+7.1 %), country PL 37.0.
    - S0005: 25.0 (was 23.6), "Green · coverage 85 %". Delivery 4.8 (on-time 92 % over 39, 2 overdue
      undelivered, vs 90 %), concentration 12.1, financial 35.3 (-7.1 %), country FR 66.3.
  - Every script in scripts/ now parses its command line with argparse first (erp_up.sh: -h/--help), so --help
    prints usage and does no work; test_scripts_help.py covers all scripts automatically.
  - SEC-066 stays open for Feature 9 (claim verifier), the rest of SEC-061 for Feature 7.
  - Unit + security: 900 passed, 36 xfailed, coverage 98 %. The live warm-up was not re-run (not needed).
- Mistake noted: a `scripts/warm_cache.py --help` call ran a real warm-up (the script has no argument parsing). It
  was read-only (SELECT with the role keys) and stopped by hand; its log gave the per-load timings above.

## 2026-10-27: feat/world-model (Meterless World Model case board)
- What:
  - The Meterless World Model reference and its conformance suite, vendored unchanged in
    context-service/vendor/world-model/ (Meterless@1742dec5, Apache-2.0, recorded in PRE_EXISTING.md).
  - A small, strict TypeScript HTTP wrapper (Fastify + zod) in context-service/src:
    - POST /cases/{id}/events: append-only, idempotent by event id.
    - GET /cases/{id}/timeline, GET /entities/{id}, GET /health.
    - X-Internal-Token auth, body and event caps, JSON logs (pino, same levels and redaction as the backend).
  - A docker-compose service: internal network only, named volume, 256 MB, healthcheck.
  - docker-compose.dev.yml publishes 127.0.0.1:7070 (user decision).
  - The Python client app/memory/world_model.py. If the service is down, `record_event()` returns
    `memory_unavailable` and the agent goes on.
  - H-MEM is cut, and the agent wiring is Feature 7.
- Decisions (decision 0006):
  - One engine namespace (file) per case.
  - At most one subject per kind per event (the engine closes an edge with the same from/type).
  - The same event id with a different body is 409.
  - Event kinds follow architecture §6.1 (not only the 5 in AGENTS.md), so Feature 7 needs no schema change.
  - CONTEXT_SERVICE_TOKEN is optional in the backend.
  - Corrupt-store recovery (user request): a case file that cannot be parsed or replayed is moved to
    /data/corrupt-<timestamp>-<case>.json, with CRITICAL `memory_store_corrupt`, and the case starts empty.
    The name carries the case id because there is one file per case.
- Conformance: CI runs the vendored suite against the reference and against src/engine.ts (the module the
  wrapper loads); both pass 8/8 with exit code 0. CI also checks the vendored files against a sha256 manifest
  and against upstream at the recorded commit.
- Tests:
  - vitest: 77 (config, engine, board, HTTP, verify-vendor).
  - pytest: client, settings, compose, and one integration test (two agents writing one case, the
    Evaluator reading all 20 events).
  - Unit 991 passed, coverage 98 %.
- Security-breaker: SEC-074..086, one High.
  - SEC-074: the engine rewrites its whole file synchronously about 20 times per append. With one shared
    file, the default caps ran out of memory under the 160 MB heap.
  - Fixed with per-case files plus byte caps (1 MB per case, 16 MB in total). Measured heap ≤ 57 MB.
  - Residual: append p99 0.6–1.6 s and rare multi-second stalls from host disk I/O. The client's 5 s deadline
    turns them into `memory_unavailable`. Re-measure in the load test.
  - All Medium findings were fixed (ghost events after a failed write, an unreadable store wiped as
    corrupt, `record_event` raising on a full board, client response cap). Lows fixed: 079–083, 085, 086.
  - SEC-084 (reads are not scoped by role) is open for Feature 7.
- Tooling notes:
  - npm 10.9 (Homebrew Node 23.7) crashed resolving vitest's optional peers ("reading 'edgesOut'"). Installs
    use npm 11 (`npx npm@11`), which Node 24 ships.
  - vitest 4.1 is used because vitest 5 needs Node 22.12+/24. CI and the image run Node 24.

## 2026-10-27: feat/agent-graph (Feature 7a: agent graph, claim verifier, evidence facts)
- What:
  - LangGraph graph in backend/app/agent/: guard (rules + Bolt) -> planner (Ultra, Lightning fallback)
    -> research (Lightning, one step per dimension) -> evaluator (at most 2 extra rounds) -> score ->
    recommender (pending proposals only) -> writer (Ultra) -> verifier (Python, one rewrite) ->
    escalation.
  - Entry point `Investigator.run_investigation(user, question)`, plus scripts/investigate.py.
  - Prompts are versioned in agent/prompts/*.v1.md.
- Additions approved with the plan:
  - injection_detected events in the trace and the World Model (field and evidence id only), with a
    report note such as "1 buyer note contained instruction-like text and was ignored";
  - an evaluator escalation returns the Python fallback report plus the gap list;
  - each step's model route is a setting (`AGENT_MODEL_<STEP>`, defaults from §6.4).
- SEC-066: `EvidenceRecord.facts` and `rows_digest`. Every tool and the score fill them. The verifier
  checks numbers against facts, not sample rows.
- SEC-061: tool calls are bounded by TOOL_CALL_TIMEOUT_SECONDS (20 s, also on a cold load). Model calls
  are bounded by MODEL_CALL_TIMEOUT_SECONDS (240 s), capped by INVESTIGATION_DEADLINE_SECONDS (900 s).
  A stop ends as "incomplete" with what was checked.
- SEC-084 (agent side): the agent reads only its own case and drops events about suppliers outside the
  resolved scope. `scope_entity_view()` is ready for Feature 7b.
- Tests:
  - Scripted fake LLM covering every graph path.
  - Security tests: S0011 injection with an "obedient" model, invented numbers, buyer scope, tool-call and
    proposal scope, plus the security-breaker's red-team file.
  - Unit 1191 passed, coverage 97 %. context-service: vitest 78, conformance 8/8 x2.
- Live (slow_live, docs/test-runs/2026-10-27-feat-agent-graph-live-S0003.json): S0003 as risk_manager.
  - Result: completed, Amber with critical delivery, 10/15 model calls, a receipt on every call, 19
    World Model events, investigation 121 s (cache warm-up 634 s).
  - The first run failed at warm-up on a Zetaris 400 for one external view. The test now requires the ERP
    views only.
- Security-breaker: SEC-087..099.
  - SEC-087 (High): verifier rounding let a fact below 0.5 back a written "0". Fixed: exact integers, no
    false zero, at most 5 % error.
  - Mediums fixed: unit-aware matching, supplier match, sign and direction, NFKC/number words/grouping,
    rating synonyms, Python-only "missing", rationale checks, argument validation plus a last-resort catch.
  - Lows: SEC-096 and SEC-097 fixed, SEC-098 in part. SEC-099 (detector variants) stays open.
- Decisions: 0007 (agent graph, verifier rules, evidence facts). Choices approved with the plan:
  - extra rounds go straight to research, without another planner call;
  - an evaluator escalation skips the writer;
  - the director gets no propose_action;
  - H-MEM recall waits for its client.
- Ops note: the context-service container must be rebuilt with the backend. An older service refuses
  injection_detected, and the client treats that as a bug and raises.

## Sponsor feedback
- Meterless (2026-10-27): the World Model reference persists with a pretty-printed, synchronous, non-atomic
  `writeFileSync` of the whole namespace after every mutation (about 20 per ingest of one record). A crash
  mid-write truncates the store, and the cost grows with the store size. Suggestion: write a temp file and
  rename it (atomic), write once per public call, and offer an append-only log file option.
- Zetaris (2026-10-27, SEC-044): the SQL Editor History keeps `CREATE DATASOURCE` statements in plain text,
  including the JDBC password. Suggestion: redact `password "..."` options in the history (and in audit logs), or
  offer a secret reference in `CREATE DATASOURCE` so the password never appears in SQL text.
- Zetaris (2026-10-27): after the platform folder was moved, the driver container failed at start because its
  bind mount (conf/spark-defaults.conf) pointed at the old path; Docker created empty folders there and the only
  symptom in the SQL Editor was "HikariPool-1 - Connection is not available" after 300 s. A clearer driver-down
  error from the API would save time.
- Zetaris (2026-10-27): `CREATE ROLE ... DESCRIBE BY '...'` accepts only letters, digits and `_ . - ,` (no
  spaces, no colons), unlike `CREATE DATASOURCE ... DESCRIBE BY`; the documented syntax does not mention this
  limit. 60_ now uses 'Category_buyers,buyer_views_without_prices_or_contract_values' and
  'Risk_managers_and_directors,all_risk_views', and a unit test checks every description against
  ^[A-Za-z0-9_.,-]+$.
- Zetaris (2026-10-27): a COUNT over a view on 12 live Eurostat REST tables took 3.5 minutes; a per-table
  timing or a "live REST source" hint in the query plan would help to spot which source to cache.
- Meterless Nightshift (2026-10-27, live agent run): a one-line Bolt (nemotron-3.5-bolt-e4b) guard check
  took 20.7 s and a Lightning evaluator call 22.2 s, slower than the Ultra planner call (3.2 s). This may
  be a model load. A load-state hint in the receipt (cold, warm) would help to tell queueing from
  inference time.
