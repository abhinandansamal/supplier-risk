# 0006: World Model case board in the context service

Date: 27 Oct 2026. Status: accepted.

## Context
- The agents need a shared, append-only case board per investigation (architecture §7, §7.1). The
  Meterless World Model reference implementation (Apache 2.0) has to be used unchanged, and its
  conformance suite must pass in CI against the module we actually run.
- The reference is a general entity graph, not a case-board API. It is event-sourced and keeps its state
  in memory. It persists by rewriting one JSON file per namespace (`<dir>/.world/<namespace>.json`),
  pretty-printed and synchronously, after every mutation: about 20 times per case event (entity, edges,
  audit entries).
- The Mac running the demo is short on memory. Nightshift and Zetaris already use most of it.

## Decision
- **Vendor and wrap.** `context-service/vendor/world-model/` holds `reference/`, `conformance/` and
  `LICENSE` from github.com/Meterless/Meterless at commit `1742dec56a66e58eef9dda2d2b865823758ca8cd`,
  byte-identical to upstream. CI checks this in two ways:
  - `npm run vendor:verify` compares every file with `vendor/world-model.sha256`, and fails on any edit,
    addition, removal, symlink or other non-regular file.
  - A second step fetches the upstream commit and runs `diff -r` against the vendored files.

  `src/engine.ts` is the only import of `vendor/`. CI runs the conformance suite against the reference
  and with `WORLD_MODEL_IMPL=src/engine.ts`. Both runs must exit with 0.
- **One engine namespace (one file) per case** (`.world/case-<case_id>.json`). This was changed after
  SEC-074. With one shared file, each append rewrote every case, and a store at the caps ran out of
  memory under the 160 MB heap.
  - Every key is an external key, so the engine's fuzzy name merging never runs.
  - In a case, the case is the entity `case:board:<case_id>`.
  - An event is the entity `case_event:<case_id>:<event_id>`. Its `attrs` hold the event body, a
    checksum of the canonical JSON, and `seq`. It has one `part-of` edge to its case.
  - Each subject (`supplier:erp:S0001`, `part:erp:P0001`, `site:erp:<id>`, `country:iso:DE`) gets one
    `about-<kind>` edge from the event. GET /entities/:id merges the references from all cases.
- **At most one subject per kind per event.** The engine *closes* an open edge when a new edge has the
  same (from, type, context) and a different target. Two suppliers on one `about-supplier` edge type would
  therefore silently close the first edge.
- **Idempotency.** The duplicate check and the write share one lock in the wrapper, so concurrent requests
  cannot race.
  - The same event id with the same body returns 200 `duplicate: true` and writes nothing.
  - The same id with a different body returns 409 `event_conflict`.
  - `at` is normalised to `YYYY-MM-DDTHH:MM:SS.sssZ` before the checksum, so the same instant written
    two ways is the same event.
- **Failed writes.** The engine updates its memory before writing the file. If a write fails, the wrapper
  reloads that case from disk, logs ERROR `store_write_failed`, and returns 503 `store_unavailable`. Its
  index, caps and seq numbers then match what is actually stored (SEC-075).
- **Limits.** All limits are environment settings.
  - Request body: 16 KB.
  - Per case: at most 500 events and 1 MB of store file. Above either, the service returns 409
    `case_full`.
  - Whole store: at most 5,000 events and 16 MB of store files. Above either, it returns 409
    `store_full`.
  - GET /entities/:id returns at most 100 references and sets `truncated` when it leaves some out.
  - Validation (zod, unknown fields rejected): the event kinds and agents in architecture §6.1 plus
    `decision_made`; length and pattern limits on every field; at most 20 flat data keys.
  - Text must be well-formed Unicode with no control or format characters (SEC-079). Integers must be
    within ±2^53-1 (SEC-080).
- **Auth and network.** Every route except GET/HEAD /health needs `X-Internal-Token`, compared in constant
  time. Failed attempts are logged at most 5 times per minute, plus a count of the ones left out
  (SEC-083). The service refuses to start if CONTEXT_SERVICE_TOKEN is missing, a placeholder, or shorter
  than 32 visible ASCII characters.
  - Server timeouts: connection 10 s, request 10 s, keep-alive 5 s.
  - In Compose the service is only on the network `context_net` (`internal: true`): no host port and no
    route out.
  - Container: read-only root filesystem, `cap_drop: ALL`, no-new-privileges, `pids_limit: 64`, runs as
    non-root. The runtime image has no npm, npx, yarn or corepack.
  - Limits: `mem_limit: 256m`, `--max-old-space-size=160`, `cpus: 0.5`. Measured usage when idle is
    about 34 MiB.
  - `docker-compose.dev.yml` publishes `127.0.0.1:7070` only, for a backend running on the host.
- **Python client** (`backend/app/memory/world_model.py`).
  - Errors are typed: `WorldModelUnavailable`, `WorldModelRejected` (with the service's error code) and
    `WorldModelBadResponse`.
  - Each call has a hard deadline (CONTEXT_SERVICE_TIMEOUT_SECONDS, default 5 s). Response bodies are
    capped at 10 MB, and timeline events are validated one by one, so an event it can't read is skipped
    and counted.
  - Only the service's own `case_not_found` or `entity_not_found` counts as "unknown". An append answer
    about a different event is refused (SEC-082).
  - `record_event()` is what the agent will call. It returns `memory_unavailable` and lets the
    investigation continue in these cases:
    - the service is down, slow or answers 5xx (logged at WARNING);
    - the board is full, the token is rejected, or the answer is unusable (logged at ERROR, because an
      operator has to act).

    It raises only on an invalid event or an event-id conflict, because those are bugs (SEC-077).
  - CONTEXT_SERVICE_TOKEN is optional in the backend, so the app starts without it.
    CONTEXT_SERVICE_URL must be https unless the host is local or `context-service` (SEC-081).

## Known weakness: whole-file, synchronous, non-atomic persistence
The vendored store rewrites its file with `fs.writeFileSync` after every mutation, and we may not edit it.

**Cost (measured, SEC-074).** We filled a store to the default caps with maximum-size events (about
12.5 KB on the wire) under `--max-old-space-size=160`:
- It stops at about 1,130 events (store_full at 16 MB).
- The heap peaks at 48–57 MB, and reopening takes about 0.3 s. Before per-case files, the same test ran
  out of memory.
- Append latency: p50 17–19 ms, p95 107–332 ms, p99 0.6–1.6 s, with single outliers of 4–8 s.

The outliers come from the host disk: on the same Mac, a raw 1 MB `writeFileSync` has p99 30 ms and a
maximum of 1.1 s, and the engine writes about 20 times per event. While a write runs, the event loop is
blocked. We handle this as follows:
- The healthcheck allows 5 s.
- The Python client's 5 s deadline turns a stall into `memory_unavailable` instead of an error.
- Retries are safe, because appends are idempotent.

Real events are about 1–2 KB, so cases stay far below the 1 MB file cap. The load test before submission
will measure this again.

**Crash safety.** The write is not atomic. A crash, OOM kill or full disk during a write can leave a
truncated case file.

**Mitigation (startup recovery).** On startup each case file is opened inside a try:
- Only parse or replay errors count as corruption. Such a file is moved to
  `/data/corrupt-<UTC timestamp>-<case_id>.json`, kept for inspection, with CRITICAL
  `memory_store_corrupt` (investigation_id, moved_to, error_type) logged. That case starts empty, and
  other cases are not affected.
- File-system errors (unreadable file, a directory at the path) stop startup and leave the file where it
  is (SEC-076).
- A symlinked case file is refused.

The file name carries the case id because there is now one file per case. Losing a case board degrades
its timeline, not the answers: facts come from Zetaris, and the audit log is in the app database. Tests
cover a file truncated after a real write, a file that parses but cannot be replayed, an unreadable file,
a symlink, and a retry after a failed write.

## Consequences
- Agents get a durable, idempotent, append-only case board per investigation. The UI timeline and the
  Evaluator read it in order.
- GET /entities/:id returns references across cases without filtering them by role. The backend has to
  apply the user's data scope before showing them (SEC-084, open until Feature 7).
- If disk stalls hurt in the load test, the next step is an upstream fix: an async store with atomic writes.
