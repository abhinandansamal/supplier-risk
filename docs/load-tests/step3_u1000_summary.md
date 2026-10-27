### step3_u1000

| endpoint | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GET list history | 4718 | 76.1 | 2 | 20 | 86 | 283 | 0 |
| POST login | 1515 | 24.4 | 66 | 240 | 290 | 339 | 516 |
| GET me | 987 | 15.9 | 1 | 26 | 100 | 195 | 0 |
| SSE open stream | 768 | 12.4 | 3 | 10 | 89 | 284 | 629 |
| GET poll status | 4730 | 76.3 | 3 | 9 | 64 | 197 | 0 |
| POST start investigation | 999 | 16.1 | 2 | 29 | 110 | 277 | 975 |
|  Aggregated | 13717 | 221.2 | 3 | 87 | 200 | 339 | 2120 |

Requests 13717; designed 429s 2119; errors 1 (0.01 % excluding designed 429s)

Designed 429s:
- start investigation: designed_429 queue_full: 816
- open stream: designed_429 too_many_streams: 629
- login: designed_429 too_many_attempts: 515
- start investigation: designed_429 rate_limited: 159

Errors:
- login: error conn HTTPConnectionClosed: 1

Monitor (13 samples): queued max 20, last 20; RSS start 246 MB, max 280 MB; CPU max 445 %; monitor list latency max 48 ms

Queue over time (t_s:queued/running): 0:0/0, 5:15/1, 10:20/1, 15:20/1, 20:20/1, 25:20/1, 30:20/1, 35:20/1, 40:20/1, 45:20/1, 50:20/1, 55:20/1, 60:20/1

errors.jsonl levels: {'WARNING': 1493}
- WARNING queue_full: 816
- WARNING login_locked: 515
- WARNING user_rate_limited: 159
- WARNING model_retry: 3

app.jsonl investigation events: {'investigation_queued': 24, 'investigation_finished': 3}
