### step1_u50

| endpoint | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GET list history | 268 | 4.5 | 5 | 9 | 21 | 31 | 0 |
| POST login | 50 | 0.8 | 86 | 120 | 130 | 128 | 0 |
| GET me | 47 | 0.8 | 3 | 6 | 8 | 8 | 0 |
| SSE open stream | 48 | 0.8 | 6 | 11 | 21 | 21 | 2 |
| GET poll status | 266 | 4.4 | 6 | 13 | 22 | 38 | 0 |
| POST start investigation | 60 | 1.0 | 5 | 16 | 17 | 17 | 36 |
|  Aggregated | 739 | 12.4 | 5 | 80 | 110 | 128 | 38 |

Requests 739; designed 429s 38; errors 0 (0.00 % excluding designed 429s)

Designed 429s:
- start investigation: designed_429 queue_full: 21
- start investigation: designed_429 rate_limited: 15
- open stream: designed_429 too_many_streams: 2

Monitor (13 samples): queued max 20, last 20; RSS start 252 MB, max 252 MB; CPU max 386 %; monitor list latency max 22 ms

Queue over time (t_s:queued/running): 0:2/0, 5:4/1, 10:8/1, 15:12/1, 20:20/1, 25:20/1, 30:20/1, 35:20/1, 40:20/1, 45:20/1, 50:20/1, 55:20/1, 60:20/1

errors.jsonl levels: {'WARNING': 37}
- WARNING queue_full: 21
- WARNING user_rate_limited: 15
- WARNING memory_unavailable: 1

app.jsonl investigation events: {'investigation_queued': 24, 'investigation_finished': 3}
