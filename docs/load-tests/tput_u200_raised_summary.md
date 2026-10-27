### tput_u200_raised

| endpoint | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GET list history | 2273 | 18.7 | 5 | 11 | 24 | 143 | 0 |
| POST login | 246 | 2.0 | 88 | 170 | 180 | 195 | 46 |
| GET me | 424 | 3.5 | 2 | 8 | 54 | 125 | 0 |
| SSE open stream | 429 | 3.5 | 6 | 13 | 28 | 140 | 204 |
| GET poll status | 2393 | 19.7 | 5 | 10 | 17 | 103 | 0 |
| POST start investigation | 396 | 3.3 | 4 | 9 | 25 | 60 | 188 |
|  Aggregated | 6161 | 50.7 | 5 | 18 | 130 | 195 | 438 |

Requests 6161; designed 429s 438; errors 0 (0.00 % excluding designed 429s)

Designed 429s:
- open stream: designed_429 too_many_streams: 204
- start investigation: designed_429 queue_full: 188
- login: designed_429 too_many_attempts: 46

Monitor (25 samples): queued max 50, last 50; RSS start 278 MB, max 307 MB; CPU max 521 %; monitor list latency max 37 ms

Queue over time (t_s:queued/running): 0:0/0, 5:8/1, 10:18/1, 15:33/1, 20:48/1, 25:50/0, 30:50/0, 35:50/0, 40:50/0, 45:50/0, 50:50/0, 55:50/0, 60:50/0, 65:50/0, 70:50/0, 75:50/0, 80:50/0, 85:50/0, 90:50/0, 95:50/0, 100:50/0, 105:50/0, 110:50/0, 115:50/0, 120:50/0

errors.jsonl levels: {'WARNING': 237}
- WARNING queue_full: 188
- WARNING login_locked: 46
- WARNING memory_unavailable: 2
- WARNING model_retry: 1

app.jsonl investigation events: {'investigation_queued': 208, 'investigation_finished': 7}
