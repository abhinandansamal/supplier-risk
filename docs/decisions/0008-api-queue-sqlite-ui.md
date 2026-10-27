# 0008: API, in-process queue, SQLite app database and a plain-JS UI

Date: 27 Oct 2026. Status: accepted (Feature 7b scope set by the user: keep it simple, TDD and the
security review still apply; Postgres, H-MEM, multi-worker and React are out of scope).

## Context
- Architecture §8.3 to §8.4 plan Postgres 16 with Alembic and the LangGraph Postgres checkpointer.
  §9 plans a React UI.
- Time is short. The NVIDIA free tier (40 requests/minute) allows about 3 investigations per minute
  anyway (§13).
- SEC-024: the model rate limiter is per process and binds to one event loop.
- SEC-084: the World Model does not scope reads.
- Decision 0007: the agent returns an `InvestigationResult` and records a trace.

## Decision
- **One process, one worker.**
  - `scripts/run_app.sh` starts uvicorn with `--workers 1` on 127.0.0.1:8000.
  - The queue is an in-process `deque` with one asyncio worker. It runs one investigation at a time.
  - SEC-024 is handled by this deployment. The limiter's own loop binding stays a strict xfail.
- **Queue limits.**
  - `QUEUE_MAX_LENGTH` (20): a full queue answers 429 `queue_full`.
  - `INVESTIGATIONS_PER_USER_PER_HOUR` (10): over the limit, the API answers 429 `rate_limited` with a
    friendly message.
  - The hourly count is kept in memory, so deleting investigations does not reset it.
- **Caller-chosen id and a live trace.**
  - `Investigator.run_investigation(..., investigation_id=, on_event=)`: the API creates the id
    (`inv-<16 hex>`, validated, because it names the trace file).
  - It also receives every `TraceEvent` as it happens. A failing listener is dropped and logged and
    never fails the investigation.
- **SQLite via SQLAlchemy 2 instead of Postgres.**
  - `APP_DATABASE_URL=sqlite:///data/local/app.db`. Any other URL stops startup.
  - Tables: `investigations`, `results`, `proposals`, `decisions`. They are created at startup, with no
    Alembic.
  - Restarting the process does not resume an investigation: unfinished ones are marked `failed`
    (`interrupted`). There is no checkpointer.
  - `RETENTION_DAYS` (30): older investigations, with their results, proposals, decisions and trace
    files, are purged at startup.
- **Login.**
  - Three seeded demo users: `buyer` (category `DEMO_BUYER_CATEGORY`, default electronics),
    `risk_manager` and `director`.
  - Passwords come from `DEMO_*_PASSWORD` and are hashed with scrypt at startup. A blank password
    disables that user.
  - The session is an HS256 JWT (`JWT_SECRET`, at least 32 characters) with iss and aud set. It lasts
    30 minutes and sits in an httpOnly, SameSite=Strict cookie. It is never in a response body.
  - The token carries only the username. Role and category come from the server-side directory.
  - Logout revokes the token id.
  - 5 failed logins in 15 minutes lock the username.
  - Writes with a foreign `Origin` header get 403 (defence in depth to SameSite).
- **Access (SEC-084, API side).**
  - Users read only their own investigations; a director may read all.
  - Only the owner may delete.
  - Anyone else gets 404, so ids are never confirmed.
  - The API has no route that reads the World Model, which a test pins. The live timeline is the
    investigation's own trace.
  - `scope_entity_view()` is still the rule for any future entity route.
- **Evidence rows never reach the browser.**
  - Before a result is stored, `rows` are removed from every evidence record, because they hold
    untrusted ERP text such as the S0011 buyer note.
  - The evidence panel shows tool, source, view, SQL template, snapshot time, retrieval time, Zetaris
    query id, row count, parameters and facts.
  - The audit value of the rows is kept by `rows_digest` and the Zetaris query id.
- **Proposals are only decided, never executed.**
  - `POST /proposals/{id}/approve|reject` records one decision per proposal: the conditional update,
    the unique `decisions.action_id` and 409 on a second decision.
  - Buyers decide low and medium impact on their own investigations. Risk managers decide any impact
    and have an inbox (`GET /proposals`). Directors do not decide.
  - Nothing resumes a graph or calls a tool.
- **GDPR erasure.**
  - `DELETE /investigations/{id}` (owner only) removes the investigation, its result, proposals,
    decisions and the `runs/<id>.jsonl` trace.
  - A queued investigation is removed from the queue. A running one is cancelled, and its trace is
    removed again when it stops.
- **UI.**
  - One page from `backend/app/web/static` (HTML, CSS, JS), with no build step and no CDN.
  - CSP: `default-src 'none'`, plus `'self'` for script, style, img, connect and font, and
    `frame-ancestors 'none'`. `nosniff`, `no-referrer` and `DENY` framing are set too.
  - Data is rendered only with `textContent`. A test forbids HTML and code sinks, inline code and
    external URLs.
  - A banner shows while `/ready` reports `cache: warming`.
- **Startup.**
  - The snapshot cache warms in the background.
  - Startup stops with CRITICAL when `LANGCHAIN_TRACING_V2`, `LANGSMITH_TRACING` or `LANGCHAIN_TRACING`
    is true. Otherwise it logs `langchain_tracing_off`.
- **SEC-098 (guard).** Without ids, a supplier counts only when its full name appears in the question:
  - matching is case-insensitive, NFKC-normalised and on whole words, ignoring punctuation;
  - it looks only within the user's scope;
  - no match (`no_supplier`) or several matches (`ambiguous_supplier`) ask for the supplier id.

## Consequences
- The demo cannot scale out. A second worker would double the model rate and split the queue, the live
  streams, the login throttle and the revocation list. Moving to Postgres plus a shared limiter is the
  path back to §8.4 and §13.
- Approved proposals do not resume the paused graph and do not write H-MEM yet (§5 step 10). That
  comes with H-MEM.
- The `decisions` table is not the hash-chained audit log of §8.4. GDPR erasure deletes decisions
  together with their investigation, so a chain would break. The audit log stays a separate feature.
- World Model case-board events are not erased by DELETE (the context service has no delete). They
  hold only fixed summaries, ids and counts, never the question or data text.
- The UI's example questions include the supplier id, because a first word like "Microchip" alone no
  longer resolves a supplier.
