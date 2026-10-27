### step2_u200

| endpoint | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GET list history | 1045 | 17.3 | 4 | 9 | 27 | 63 | 0 |
| POST login | 258 | 4.3 | 83 | 170 | 200 | 206 | 58 |
| GET me | 239 | 4.0 | 2 | 8 | 14 | 40 | 0 |
| SSE open stream | 179 | 3.0 | 5 | 13 | 22 | 41 | 74 |
| GET poll status | 1009 | 16.7 | 5 | 10 | 16 | 39 | 0 |
| POST start investigation | 225 | 3.7 | 3 | 8 | 30 | 42 | 201 |
|  Aggregated | 2955 | 48.9 | 4 | 72 | 130 | 206 | 333 |

Requests 2955; designed 429s 333; errors 0 (0.00 % excluding designed 429s)

Designed 429s:
- start investigation: designed_429 queue_full: 177
- open stream: designed_429 too_many_streams: 74
- login: designed_429 too_many_attempts: 58
- start investigation: designed_429 rate_limited: 24

Monitor (13 samples): queued max 20, last 20; RSS start 277 MB, max 277 MB; CPU max 397 %; monitor list latency max 41 ms

Queue over time (t_s:queued/running): 0:0/1, 5:10/1, 10:20/1, 15:20/1, 20:20/1, 25:20/1, 30:20/1, 35:20/1, 40:20/1, 45:20/1, 50:20/1, 55:20/1, 60:20/1

errors.jsonl levels: {'WARNING': 260}
- WARNING queue_full: 177
- WARNING login_locked: 58
- WARNING user_rate_limited: 24
- WARNING memory_unavailable: 1

app.jsonl investigation events: {'investigation_queued': 24, 'investigation_finished': 3}
