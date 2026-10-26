---
paths:
  - "context-service/**"
  - "frontend/**"
---
# TypeScript rules
- Strict TypeScript ("strict": true). ESLint + Prettier. No any without a comment explaining why.
- TSDoc comments on exported functions, classes and types (summary, @param, @returns, @throws).
- context-service: vendored Meterless engine code in context-service/vendor/ is not edited; wrap it.
  Its conformance suites must pass in CI. Validate every request body (e.g. zod). JSON logs with request_id
  and investigation_id; same levels and redaction rules as .claude/rules/logging.md.
- frontend: never store tokens in localStorage; render model text as text (no dangerouslySetInnerHTML);
  accessible components (labels, keyboard focus, contrast) per WCAG 2.2 AA basics.
- Tests with vitest, written before the code (TDD).
