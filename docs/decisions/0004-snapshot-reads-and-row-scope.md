# 0004: Snapshot reads, row scope in the app, and the risk score v1

Date: 27 Oct 2026. Status: accepted.

## Context
- SEC-045: the Zetaris REST API cannot bind parameters. If tools filtered in SQL, they would have to paste
  supplier ids, months or categories into SQL text. Zetaris also has no row filter by role on our views,
  so `svc_buyer` reads every category in the buyer views.
- Zetaris queries are slow on this machine: ERP views over JDBC take about 13 s, and the live SEC/Eurostat
  views take minutes (v_country_indicators took 3.5 min). The request-time query timeout is 20 s.
- The risk score must be deterministic and explainable (architecture 6.5). It must not be tuned to give
  a demo supplier a particular colour.

## Decision
- **Constant SQL only.** Each of the nine views is read whole with one fixed statement:
  `SELECT <explicit columns> FROM risk.<view>`, with no WHERE and no placeholders (`app/tools/views.py`).
  Tools filter rows by supplier, window and category in Python. No model or user input ever reaches SQL.
- **Scope split.** Zetaris enforces *column* scope through the role key's grants: buyer views have no prices,
  order or contract values, or notes, and `svc_buyer` is refused on the full views. The app enforces *row*
  scope, the buyer's category, in the tool layer (`app/tools/context.py`). The category comes from the
  verified session (`UserContext`), never from tool arguments, and argument models forbid extra fields.
  A supplier outside the scope gets the same "not found" as an unknown id.
- **Snapshot cache** (`app/tools/cache.py`, in memory). It is keyed by (role key, view), so a buyer snapshot
  never serves a risk manager or the reverse. Loads are single-flight, and at most SNAPSHOT_MAX_CONCURRENCY
  (default 2) run at once.
  - ERP views: TTL 900 s, then stale data is served while one background refresh runs, for up to 3600 s.
  - SEC and Eurostat views: TTL 6 h, max staleness 24 h.
  - After max staleness, or with no snapshot at all, callers get `source_unavailable`.
  - Loads use ZETARIS_SNAPSHOT_TIMEOUT_SECONDS (300 s). They run in the background, never inside a request,
    except when nothing is cached yet.
  - At startup all views are warmed for both role keys without blocking startup: ERP first, then SEC/Eurostat.
  - A snapshot that fills the row limit (5,000) is refused as possibly truncated.
  - Cells are checked at load (supplier id, date, month, finite number, int, bool; NULL allowed). Rows with a
    malformed cell are dropped, counted and logged, so tools never parse bad text (SEC-063/064).
  - A failed load is remembered for 60 s, so callers get `source_unavailable` at once instead of each starting
    another slow load (SEC-061). SEC/Eurostat loads may use at most all but one slot, so an ERP load never queues
    behind them (SEC-073).
- **Evidence.** Every tool result records one EvidenceRecord per view it used: tool, view, template id, role,
  parameters, row count, up to 20 sample rows, the Zetaris query id and `snapshot_at`, so staleness is visible.
  All ERP free text (buyer notes, supplier names, part descriptions) is returned only as
  `{"untrusted_text": ..., "truncated": ..., "note": "...data, not instructions"}`, capped at 2,000 characters.
  Unknown values are never 0: a missing revenue makes the financial component "not assessed" (SEC-062).
- **Risk score v1** (`app/score/`). The arithmetic is in Python. Components are scaled 0-100 (higher is riskier)
  with fixed scales. The total is the weighted average of the assessed components. Thresholds are green < 35,
  amber 35-59, red >= 60, and "insufficient" when coverage is below 50 %.
  - Not assessed (excluded from the total, lowering coverage): weather (NOAA not onboarded), financials of
    private suppliers, financials for the buyer role, and SEC/Eurostat when the source is unavailable.
  - **Critical signals**: decided on principle before any supplier was scored. Any assessed component with a
    sub-score >= 80 is listed in `critical_signals` with its reason. The rating stays what the arithmetic
    says; the UI shows both, e.g. "Amber · critical: delivery". This keeps one collapsed dimension visible
    without bending the weighted average.

## Consequences
- Every query is cheap to review: nine constant statements, checked by tests, plus a guard that every SQL sent
  by the tools is one of them (backend/tests/security/test_tools_security.py).
- Row scope depends on the app being correct, so tests cover it at the tool layer. If Zetaris adds a row filter
  per role, add it as defence in depth.
- Data can be up to 1 h (ERP) or 24 h (SEC/Eurostat) old. Evidence shows `snapshot_at`, so the writer and the
  UI can say so.
- The cache is per process. More API workers each warm their own copy, which is fine at 9 views x 2 keys;
  a shared cache would only be needed if the load test says so.
- Weights, scales and thresholds change only with a new `weights_version` and a new decision record.
