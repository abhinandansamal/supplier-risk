### check_u200_report

| endpoint | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GET list history | 1066 | 17.4 | 4 | 11 | 26 | 102 | 0 |
| POST login | 246 | 4.0 | 96 | 240 | 270 | 275 | 46 |
| GET me | 220 | 3.6 | 2 | 10 | 26 | 94 | 0 |
| SSE open stream | 161 | 2.6 | 6 | 12 | 18 | 81 | 63 |
| GET poll status | 1060 | 17.3 | 5 | 10 | 16 | 63 | 0 |
| GET read report | 257 | 4.2 | 5 | 12 | 18 | 27 | 0 |
| POST start investigation | 199 | 3.3 | 3 | 10 | 58 | 89 | 175 |
|  Aggregated | 3209 | 52.5 | 4 | 73 | 130 | 275 | 284 |

Requests 3209; designed 429s 284; errors 0 (0.00 % excluding designed 429s)

Designed 429s:
- start investigation: designed_429 queue_full: 128
- open stream: designed_429 too_many_streams: 63
- start investigation: designed_429 rate_limited: 47
- login: designed_429 too_many_attempts: 46

Monitor (13 samples): queued max 20, last 20; RSS start 260 MB, max 260 MB; CPU max 454 %; monitor list latency max 20 ms

Queue over time (t_s:queued/running): 0:0/0, 5:5/1, 10:20/1, 15:20/1, 20:20/1, 25:20/1, 30:20/1, 35:20/1, 40:20/1, 45:20/1, 50:20/1, 55:20/1, 60:20/1

errors.jsonl levels: {'WARNING': 223}
- WARNING queue_full: 128
- WARNING user_rate_limited: 47
- WARNING login_locked: 46
- WARNING model_retry: 2

app.jsonl investigation events: {'investigation_queued': 24, 'investigation_finished': 3}
