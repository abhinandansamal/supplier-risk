# Application logging rules (always loaded)

## Setup
- One logging setup in backend/app/core/logging.py, called once at startup. Python stdlib logging with a
  JSON formatter: one JSON object per line. Every module uses logger = logging.getLogger(__name__).
- Handlers: console (stdout) + logs/app.jsonl (rotating, 10 MB x 5 files, all levels >= LOG_LEVEL)
  + logs/errors.jsonl (rotating, WARNING and above). LOG_LEVEL and LOG_DIR come from settings.
- No print() anywhere in app code.

## Fields on every record
timestamp (UTC ISO 8601), level, logger, message, event (short snake_case name, e.g. investigation_started),
request_id, investigation_id (when inside an investigation), plus context fields (agent, step, tool,
duration_ms, status_code, error_type). request_id and investigation_id are carried with contextvars.

## Levels
- DEBUG: details for local debugging only (off by default).
- INFO: normal events - app startup/shutdown, HTTP request done (method, path, status, duration_ms),
  investigation started/finished, tool call done (tool, rows, duration_ms, cache hit/miss), model call done
  (model, routed_model, receipt_id, tokens, duration_ms), memory recall (memory ids and scores, not contents).
- WARNING: problems handled automatically - 429 retry, timeout retry, call budget above 80 %,
  evaluator asked for another round, input truncated, context service slow.
- ERROR: an operation failed after retries, or bad upstream/model output. Use logger.exception(...)
  inside except blocks so the stack trace is kept.
- CRITICAL: the app cannot serve - missing required settings, Zetaris, Nightshift or the context service
  unreachable at startup.

## Errors seen by users
- API errors return {code, message, request_id} (request_id also in the X-Request-ID header); details stay in
  the logs. Never return stack traces or upstream error bodies to users.

## Agent activity trace
- Separate from app logs: runs/<investigation_id>.jsonl, one line per agent step (agent, step, action, tool,
  sql_template_id, rows, query_id, model_requested, routed_model, receipt_id, tokens, latency_ms, retries,
  verdict, budget_left). Shown in the UI as the "How we got here" timeline. Linked to app logs by investigation_id.

## Privacy and security
- A redaction filter on all handlers removes Authorization headers, bearer tokens, nvapi- keys, the
  Zetaris and Meterless keys, and any value of a SecretStr setting. Never log full prompts, full rows or memory
  contents; log ids, counts and short previews only.

## Tests
- Every logging change has tests (caplog): correct level and event name, request_id/investigation_id present,
  secrets redacted, no stack traces in API error responses.
