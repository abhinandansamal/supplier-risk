# Load tests

Locust flows plus fake upstreams for the Supplier Risk Investigator (architecture 13.1).
Results: `docs/load-test-report.md`, raw CSVs: `docs/load-tests/`.

## Files
- `fake_upstreams.py`: one process that serves fake Nightshift (`/v1/chat/completions`,
  `/v1/models`), fake Zetaris (`/api/v1.0/sql-editor/sqls/run`) and a fake context service
  (`/health`, `/cases/{id}/events`, `/cases/{id}/timeline`) on loopback. The model answers come
  from `backend/tests/fakes/llm.py`, the Zetaris rows from `backend/tests/fakes/zetaris.py`, so
  the real agent graph runs and its verifier accepts the claims. Each fake has `GET /_stats`.
- `locustfile.py`: virtual users log in as `buyer`, `risk_manager` or `director` (round robin)
  and then list history, poll status, read reports, start investigations, open the SSE stream
  for 3 s and read `/auth/me`, with 1-5 s think time. A monitor greenlet records the queue and
  the app's RSS/CPU every 5 s.
- `run_step.sh`: runs one step in isolation: fakes, app and Locust, then the summary.
- `summarize.py`: prints the Markdown summary of one step.

## Isolation
- The app under test runs from a scratch directory, so pydantic-settings never reads the
  repo's `.env`. Every setting is passed as an environment variable with fake values that
  point at the fakes. Logs, traces and SQLite go into the scratch directory too.
- Default ports: app 8100, fakes 18088 / 18888 / 17070. The script refuses to start if any of
  them is taken. It never uses 8000, 8088, 8888 or 7070.
- `--workers 1`, as in `scripts/run_app.sh` (decision 0008).

## Run
```bash
export LOADTEST_SCRATCH=/path/to/scratch          # default: ./.loadtest-scratch
export FAKE_LLM_LATENCY_MS=1500 FAKE_LLM_429_RATE=0.02 FAKE_LLM_ERROR_RATE=0.01
export FAKE_ZETARIS_LATENCY_MS=150 FAKE_ZETARIS_ERROR_RATE=0.01
export FAKE_CONTEXT_LATENCY_MS=20 FAKE_CONTEXT_ERROR_RATE=0.01
loadtests/run_step.sh step1_u50 50 10 60
loadtests/run_step.sh step2_u200 200 20 60
loadtests/run_step.sh step3_u1000 1000 50 60
loadtests/run_step.sh step4_u3000 3000 100 60
# Throughput with raised limits (extra KEY=VALUE pairs are app settings):
loadtests/run_step.sh tput_u200_raised 200 20 120 QUEUE_MAX_LENGTH=200 INVESTIGATIONS_PER_USER_PER_HOUR=100
```
Fault settings per fake (prefix `FAKE_LLM_`, `FAKE_ZETARIS_`, `FAKE_CONTEXT_`): `LATENCY_MS`,
`JITTER` (relative, default 0.5), `ERROR_RATE` (503), `429_RATE` (429 + Retry-After) and
`RETRY_AFTER` (seconds, default 2).

## Counting
- Designed 429s (`rate_limited`, `queue_full`, `too_many_attempts`, `too_many_streams`) are
  recorded as failures with the message `designed_429 <code>`. `summarize.py` counts them
  apart from real errors (`error <status> <code>` and `error conn <type> <cause>`).
- The latency percentiles of `start investigation`, `login` and `open stream` include these
  fast 429 answers.
- The monitor reads `GET /api/v1/investigations` as the director. That list returns at most
  the 50 newest investigations, so with a deeper queue it reports at most 50 queued. The
  app's `investigation_queued` log lines carry the true `queue_position`.

## Real-upstream profile (at most 5 users)
`LOADTEST_PROFILE=real` refuses to start with more than 5 users and lets each user start only
one investigation. Point an app instance at the real Nightshift, Zetaris and context service
(that app reads its own `.env`, so run it from the repo the usual way) and run:
```bash
LOADTEST_PROFILE=real LOADTEST_PASSWORD_BUYER=... LOADTEST_PASSWORD_RISK_MANAGER=... \
LOADTEST_PASSWORD_DIRECTOR=... uv run locust -f loadtests/locustfile.py --headless \
  -H http://127.0.0.1:8000 -u 5 -r 1 -t 180s --csv docs/load-tests/real_u5
```
This profile spends real NVIDIA free-tier requests (about 8 per investigation) and is never
run automatically.
