# Load test report

Date: 27 Oct 2026. Branch: docs/load-test-and-privacy. Tooling: `loadtests/` (see its README).
Raw Locust CSVs, monitor CSVs and per-step summaries are in `docs/load-tests/`.

## Summary
- **No step crossed the breaking-point criteria.** The criteria were: errors above 1 % not
  counting designed 429s, read p95 above 5 s, a crash, or a memory blow-up. All four steps
  (50, 200, 1000 and 3000 users) ran.
- **3000 users is the knee.** The single worker levels off at about 770-800 req/s, and its
  CPU stays near 100 % (one core) after the ramp. In the steady state, read p95 is 0.5-1.3 s
  and p99 is 4-6 s. 0.32 % of requests fail at the connection level. The first breaking
  point is therefore just above 3000 users on this machine. That is an extrapolation; no
  larger step was run.
- **Investigations are the real capacity limit, long before HTTP is.** One worker completes
  about 3.5 investigations per minute; at the 30 calls/minute model limit with 8 calls each,
  the ceiling is 3.75. The 20-slot queue fills within 10-20 s at 50 users or more. After
  that, every new start gets the designed 429 `queue_full` or `rate_limited`.
- **Problems found that are not about latency:**
  - The login throttle locks out users who typed the right password when several log in at
    once (`too_many_attempts`).
  - Log volume is high: 13 MB/min in `app.jsonl` at 3000 users, written twice (file and
    stdout).
  - Two log fields are missing.
  - The history list has no pagination (at most 50 rows).

## Setup
| Item | Value |
| --- | --- |
| Machine | Apple M2, 8 cores, 8 GB RAM, macOS 26.6.2; Python 3.12.9; Locust 2.47.0 (one local process, `FastHttpUser`) |
| App under test | `uvicorn --factory app.main:create --workers 1 --no-access-log` on 127.0.0.1:8100, run from a scratch dir (no `.env` read), SQLite in scratch, `APP_ENV=prod`, `LOG_LEVEL=INFO`, `LANGCHAIN_TRACING_V2=false` |
| Fake Nightshift (18088) | 1500 ms +-50 % per chat call, 2 % 429 (Retry-After 2 s), 1 % 503; replies from `backend/tests/fakes/llm.py` |
| Fake Zetaris (18888) | 150 ms +-50 %, 1 % 503; rows from `backend/tests/fakes/zetaris.py` |
| Fake context service (17070) | 20 ms +-50 %, 1 % 503; idempotent case board |
| App limits, steps 1-4 | Defaults: `QUEUE_MAX_LENGTH=20`, `INVESTIGATIONS_PER_USER_PER_HOUR=10`, model limiter 30/min with 2 in flight, budget 15 |
| App limits, throughput step | `QUEUE_MAX_LENGTH=200`, `INVESTIGATIONS_PER_USER_PER_HOUR=100` |
| User flow | Log in as buyer / risk_manager / director (round robin, up to 3 tries when locked). Weighted tasks: list history 5, poll status 6, read report 3, start 1, SSE 3 s 1, `/auth/me` 1. Think time 1-5 s. |
| Duration | 60 s per step (the throughput step ran 120 s). Spawn rates: 10, 20, 50 and 100 users/s. The app was restarted with an empty DB and empty logs for each step. |

Notes on method:
- Designed 429s (`rate_limited`, `queue_full`, `too_many_attempts`, `too_many_streams`) are
  counted apart from errors.
- The latency figures for start, login and SSE include those fast 429 answers.
- In steps 1-4 a bug in the locustfile left completed investigations in the "poll status"
  group, so "read report" shows no rows there. It is the same endpoint. A rerun at 200
  users with the fix (`check_u200_report`) measured "read report" separately: about 28 KB
  per answer, p50 5 ms, p95 12 ms, p99 18 ms, no errors.
- The real-upstream profile (at most 5 users) is built (`LOADTEST_PROFILE=real`) but was
  **not run**. The real services must stay untouched during the live demo.

## Results per step

### Step 1: 50 users (default limits)
| Endpoint group | Requests | req/s | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| list history | 268 | 4.5 | 5 | 9 | 21 |
| poll status (incl. reports) | 266 | 4.4 | 6 | 13 | 22 |
| me | 47 | 0.8 | 3 | 6 | 8 |
| SSE open stream | 48 | 0.8 | 6 | 11 | 21 |
| start investigation | 60 | 1.0 | 5 | 16 | 17 |
| login | 50 | 0.8 | 86 | 120 | 130 |
| **all** | **739** | **12.4** | **5** | **80** | **110** |

- Errors: 0 (0.00 %).
- Designed 429s: 21 `queue_full`, 15 `rate_limited`, 2 `too_many_streams`.
- Queue: 2, 4, 8, 12, then 20 (full) from t=20 s to the end. 24 queued, 3 completed.
- App RSS: 252 MB (first sample, startup included), stable.
- errors.jsonl: 37 WARNING (21 `queue_full`, 15 `user_rate_limited`, 1 `memory_unavailable`),
  0 ERROR, 0 CRITICAL.

### Step 2: 200 users (default limits)
| Endpoint group | Requests | req/s | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| list history | 1045 | 17.3 | 4 | 9 | 27 |
| poll status | 1009 | 16.7 | 5 | 10 | 16 |
| me | 239 | 4.0 | 2 | 8 | 14 |
| SSE open stream | 179 | 3.0 | 5 | 13 | 22 |
| start investigation | 225 | 3.7 | 3 | 8 | 30 |
| login | 258 | 4.3 | 83 | 170 | 200 |
| **all** | **2955** | **48.9** | **4** | **72** | **130** |

- Errors: 0.
- Designed 429s: 177 `queue_full`, 74 `too_many_streams`, 58 `too_many_attempts`,
  24 `rate_limited`.
- Queue: full (20) from t=10 s.
- App RSS: max 277 MB.
- errors.jsonl: 260 WARNING (177 `queue_full`, 58 `login_locked`, 24 `user_rate_limited`,
  1 `memory_unavailable`), 0 ERROR.

### Step 3: 1000 users (default limits)
| Endpoint group | Requests | req/s | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| list history | 4718 | 76.1 | 2 | 20 | 86 |
| poll status | 4730 | 76.3 | 3 | 9 | 64 |
| me | 987 | 15.9 | 1 | 26 | 100 |
| SSE open stream | 768 | 12.4 | 3 | 10 | 89 |
| start investigation | 999 | 16.1 | 2 | 29 | 110 |
| login | 1515 | 24.4 | 66 | 240 | 290 |
| **all** | **13717** | **221.2** | **3** | **87** | **200** |

- Errors: 1 (0.01 %), a login `HTTPConnectionClosed`.
- Designed 429s: 816 `queue_full`, 629 `too_many_streams`, 515 `too_many_attempts`,
  159 `rate_limited`.
- Queue: full (20) from t=10 s.
- App RSS: 246-280 MB. App CPU peaked at 445 % during the ramp (scrypt in threads).
- errors.jsonl: 1493 WARNING (816 `queue_full`, 515 `login_locked`, 159 `user_rate_limited`,
  3 `model_retry`), 0 ERROR.
- Log volume: app.jsonl 5.1 MB in 60 s, with the same 5.1 MB again on stdout.

### Step 4: 3000 users (default limits)
| Endpoint group | Requests | req/s | p50 ms | p95 ms | p99 ms | max ms |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| list history | 12571 | 202.4 | 45 | 570 | 4300 | 7826 |
| poll status | 11718 | 188.6 | 29 | 460 | 4100 | 7803 |
| me | 2546 | 41.0 | 43 | 570 | 4100 | 7817 |
| SSE open stream | 1868 | 30.1 | 85 | 690 | 4100 | 7803 |
| start investigation | 2600 | 41.9 | 48 | 570 | 4200 | 7797 |
| login | 3898 | 62.8 | 270 | 4200 | 6200 | 7804 |
| **all** | **35201** | **566.7** | **57** | **670** | **4300** | **7826** |

- Errors: 112 (0.32 %), all at the connection level. `RetriesExceeded`: 38 list, 35 login,
  21 poll, 7 me, 5 start. Also 5 SSE `ConnectTimeout` (10 s) and 1 `HTTPConnectionClosed`.
  No 5xx at all.
- Designed 429s: 1718 `too_many_streams`, 1705 `queue_full`, 866 `rate_limited`,
  863 `too_many_attempts`.
- Over time (10 s Locust windows):

  | t (s) | users | req/s | p50 ms | p95 ms | p99 ms |
  | ---: | ---: | ---: | ---: | ---: | ---: |
  | 18 | 1800 | 341 | 80 | 490 | 670 |
  | 28 | 2800 | 522 | 130 | 680 | 2600 |
  | 33 | 3000 | 574 | 150 | 1300 | 4400 |
  | 43 | 3000 | 755 | 74 | 830 | 5900 |
  | 58 | 3000 | 772 | 63 | 690 | 4300 |

- App CPU: 290-500 % during the ramp (logins), then 87-105 % in the steady state. That is
  one core: the event loop is saturated.
- App RSS: 130-290 MB, falling after the ramp. No memory growth.
- Monitor: the queue stayed full (20). Four of 13 probes failed within 1-5 ms on the
  monitor's kept-alive connection. Successful probes on that connection took at most 394 ms,
  while VU p99 was over 4 s. The delay is therefore in connection setup or accept, not in
  request handling.
- errors.jsonl: 3434 WARNING (1705 `queue_full`, 866 `user_rate_limited`, 863
  `login_locked`), 0 ERROR, 0 CRITICAL.
- Log volume: app.jsonl 13.0 MB in 60 s, which rotated once (10 MB cap); 13 MB more on
  stdout; errors.jsonl 0.85 MB.

### Throughput step: 200 users, 120 s, `QUEUE_MAX_LENGTH=200`, `INVESTIGATIONS_PER_USER_PER_HOUR=100`
| Endpoint group | Requests | req/s | p50 ms | p95 ms | p99 ms |
| --- | ---: | ---: | ---: | ---: | ---: |
| list history | 2273 | 18.7 | 5 | 11 | 24 |
| poll status | 2393 | 19.7 | 5 | 10 | 17 |
| start investigation | 396 | 3.3 | 4 | 9 | 25 |
| **all** | **6161** | **50.7** | **5** | **18** | **130** |

- 208 investigations accepted, 7 completed in 120 s (3.5 per minute). Each took 13-23 s:
  8 model calls each, at most 2 in flight.
- The queue reached position 200 (from the `investigation_queued` logs) after about 50 s.
  Then 188 `queue_full` answers followed.
- At 3.5 per minute, the last queued investigation waits about 57 minutes. That is nearly
  twice the 30-minute session.
- Errors: 0. App RSS max 307 MB. errors.jsonl: 237 WARNING, 0 ERROR.

### Cache, rate limiter, DB, memory
- **Snapshot cache.** The hit rate was 100 % after warm-up in every step. Fake Zetaris
  received exactly 14 queries per step: the warm-up of 7 views x 2 keys. Tool calls took
  about 3 ms. `tool_call_done` does not log hit/miss (see fixes).
- **Model rate limiter.** It was never exceeded: 31-32 chat calls per 60 s step, under
  30/min plus the warm-up minute. The injected 429/503s were retried (`model_retry`
  WARNING) and no investigation failed.
- **SQLite.** No `database is locked` errors and no store errors. app.db stayed under
  200 KB.
- **Memory recall (H-MEM).** Not implemented yet (decision 0008), so not measured.
- **Context service.** The injected 503s were degraded to `memory_unavailable` (WARNING),
  as designed.

## Breaking point and likely cause
None of the measured steps broke. The nearest is step 4 (3000 users):
1. **The single-core event loop is the bottleneck (confirmed by metrics).**
   - Throughput levels off at about 770-800 req/s, at 1 core of CPU, with p99 at 4-6 s.
   - Every request runs in one asyncio loop (one worker by design, decision 0008).
   - Each request does on that loop: JWT verification, a synchronous SQLAlchemy/SQLite query,
     JSON serialisation, and two synchronous JSON log writes (file and stdout).
   - Likely, not profiled: synchronous SQLite and synchronous logging take a large share.
2. **Connection setup suffers first.**
   - The flat p99 of about 4.1 s on every endpoint, the 10 s `ConnectTimeout`s and
     `RetriesExceeded` all point to new connections waiting to be accepted while the loop
     is busy.
   - On the same run, requests on an existing keep-alive connection stayed under 400 ms.
   - The macOS `kern.ipc.somaxconn` is 128, but `netstat -s` showed 0 listen-queue
     overflows, so a backlog overflow is not proven.
   - The underlying error text was not captured in this run. The locustfile now records it.
3. **The login ramp burns CPU.** 60 logins/s means 60 scrypt checks/s (16 MB, tens of ms
   each) in the thread pool. That gave 400-500 % CPU and a login p95 of 4.2 s during the
   ramp.
4. **Extrapolation.** Read p95 is expected to pass 5 s at roughly 3500-4000 users on this
   machine, once the offered load exceeds the 800 req/s ceiling for a sustained period.
   This was not measured.

## What failed or degraded (by category)
| Category | Observed |
| --- | --- |
| Timeouts | 5 SSE connect timeouts (10 s) at 3000 users. No investigation deadline was hit. |
| 5xx | None in any step. |
| Memory | Stable at 130-307 MB RSS. No growth. |
| DB connections | No SQLite lock or connection errors. |
| Rate limiter (model) | Held at 30/min. Injected 429s were retried. |
| Investigation capacity | About 3.5 per minute. The queue is full within 10-20 s at 50 users or more. A full 200-slot queue means about 57 minutes of wait. |
| Login throttle | Correct-password logins are locked when 5 or more run at once for one username: 58 at 200 users, 515 at 1000, 863 at 3000. SEC-100 counts each attempt before the check. |
| SSE limit | `too_many_streams` (3 per user) is hit constantly, because thousands of VUs share 3 accounts. This is an artefact of the demo accounts. |
| Memory recall | Not implemented. |
| Log volume | 13 MB/min of app.jsonl at 3000 users, plus the same again on stdout. The 10 MB x 5 rotation keeps only about 4 minutes. errors.jsonl gets about 3400 WARNINGs/min, all designed rejections. |
| Logging fields | `investigation_finished` has `investigation_id: null`. `tool_call_done` has no cache hit/miss field (`.claude/rules/logging.md` asks for both). |
| API | `GET /api/v1/investigations` returns at most 50 rows with no pagination. A director (or anyone with more than 50 investigations) cannot see older ones, and the queue cannot be seen past 50. |

## Prioritised fixes
1. **(High) Login throttle locks out valid users under concurrency.**
   - Serialise the check per username, for example with an `asyncio.Lock` per bucket, and
     count only failures. Concurrent brute force stays limited (SEC-100) and correct
     logins no longer lock.
   - Test first: N parallel correct logins all succeed; 6 parallel wrong ones lock.
2. **(High) Match queue limits to the real drain rate.**
   - At about 3.5 investigations/min (fewer on the real Nightshift), `QUEUE_MAX_LENGTH`
     should stay at or below 3.5 x 30 = about 100 for the 30-minute session. Better, size
     it to the wait you are willing to accept, and show an ETA.
   - Send `Retry-After` on `queue_full` / `rate_limited`.
   - Keep the default of 20 for the demo.
3. **(Medium) Log volume and logging on the event loop.**
   - Move file handlers behind a `QueueHandler`/`QueueListener`, so disk writes leave the
     loop.
   - Do not write to both console and file in prod.
   - Log designed rejections (`queue_full`, `user_rate_limited`, `login_locked`) at INFO,
     or sample them, and keep counters.
   - Consider not logging every poll at INFO.
   - Raise the rotation size, or ship logs off the box.
4. **(Medium) Raise the single-worker HTTP ceiling (about 800 req/s).**
   - Run synchronous SQLite reads with `asyncio.to_thread`, or cache the detail of finished
     investigations (they never change).
   - Bound scrypt concurrency with a small semaphore, so a login burst cannot starve the
     loop's thread pool.
   - Set an explicit uvicorn `--backlog` and keep-alive.
   - Long term: Postgres, a shared limiter and several workers (decision 0008 consequences).
5. **(Medium) Paginate `GET /investigations`** (limit and cursor). Expose queue length and
   investigations-per-minute as a metric, or on `/ready`, instead of only in logs.
6. **(Low) Logging fields.**
   - Set `investigation_id` on `investigation_finished` and `investigation_started`.
   - Add `cache: hit|miss` to `tool_call_done`.
   - Each with a caplog test.
7. **(Low) Before submission, run the real-upstream profile (at most 5 users)** to confirm
   queueing under the real 40 RPM limit and real model latency.
