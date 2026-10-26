---
paths:
  - "backend/tests/**"
  - "loadtests/**"
  - "context-service/test/**"
  - "frontend/**/*.test.*"
---
# Test rules
- Tests are written before the code they test (TDD). A new test must fail before the fix and pass after.
- Python: pytest + pytest-asyncio; mock HTTP with respx; no network in unit tests.
  TypeScript: vitest; the Meterless conformance suites must pass.
- Layout: backend/tests/unit, backend/tests/integration (@pytest.mark.integration; CI skips them, developers
  run them locally and record results in docs/test-runs/), backend/tests/security.
- Security tests: a known, not-yet-fixed issue is marked @pytest.mark.xfail(strict=True, reason="SEC-<id>")
  so it turns red when fixed and the marker must be removed.
- Name tests after behaviour (test_returns_unauthorized_on_401).
