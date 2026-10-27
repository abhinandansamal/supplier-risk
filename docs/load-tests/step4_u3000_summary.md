### step4_u3000

| endpoint | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failures |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| GET list history | 12571 | 202.4 | 45 | 570 | 4300 | 7826 | 38 |
| POST login | 3898 | 62.8 | 270 | 4200 | 6200 | 7804 | 899 |
| GET me | 2546 | 41.0 | 43 | 570 | 4100 | 7817 | 7 |
| SSE open stream | 1868 | 30.1 | 85 | 690 | 4100 | 7803 | 1723 |
| GET poll status | 11718 | 188.6 | 29 | 460 | 4100 | 7803 | 21 |
| POST start investigation | 2600 | 41.9 | 48 | 570 | 4200 | 7797 | 2576 |
|  Aggregated | 35201 | 566.7 | 57 | 670 | 4300 | 7826 | 5264 |

Requests 35201; designed 429s 5152; errors 112 (0.32 % excluding designed 429s)

Designed 429s:
- open stream: designed_429 too_many_streams: 1718
- start investigation: designed_429 queue_full: 1705
- start investigation: designed_429 rate_limited: 866
- login: designed_429 too_many_attempts: 863

Errors:
- list history: error conn RetriesExceeded: 38
- login: error conn RetriesExceeded: 35
- poll status: error conn RetriesExceeded: 21
- me: error conn RetriesExceeded: 7
- open stream: error conn ConnectTimeout: 5
- start investigation: error conn RetriesExceeded: 5
- login: error conn HTTPConnectionClosed: 1

Monitor (13 samples): queued max 20, last 20; RSS start 265 MB, max 290 MB; CPU max 497 %; monitor list latency max 394 ms

Queue over time (t_s:queued/running): 0:0/0, 6:20/1, 11:-1/-1, 16:20/1, 21:-1/-1, 26:20/1, 31:20/1, 36:20/1, 41:-1/-1, 46:20/1, 51:20/1, 56:20/1, 61:20/1

errors.jsonl levels: {'WARNING': 3434}
- WARNING queue_full: 1705
- WARNING user_rate_limited: 866
- WARNING login_locked: 863

app.jsonl investigation events: none
