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
