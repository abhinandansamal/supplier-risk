"""Summarise one load-test step: Locust CSVs, the monitor CSV and the app's log files.

usage: python loadtests/summarize.py <label> <csv dir> <step scratch dir>

Prints Markdown: per endpoint group requests, req/s, p50/p95/p99 and failures; the failures
split into designed 429s and real errors; the queue and RSS/CPU from the monitor; and the
WARNING/ERROR/CRITICAL counts (by event) in the step's logs/errors.jsonl.
"""

import csv
import json
import sys
from collections import Counter
from pathlib import Path


def _stats(path: Path) -> list[dict[str, str]]:
    with path.open() as handle:
        return list(csv.DictReader(handle))


def _failures(path: Path) -> tuple[Counter[str], Counter[str]]:
    designed: Counter[str] = Counter()
    errors: Counter[str] = Counter()
    for row in _stats(path):
        message = row["Error"].strip().strip("'\"")
        message = message.removeprefix("CatchResponseError(").removeprefix("Exception(")
        message = message.strip("()'\"")
        key = f"{row['Name']}: {message}"
        if "designed_429" in message:
            designed[key] += int(row["Occurrences"])
        else:
            errors[key] += int(row["Occurrences"])
    return designed, errors


def _logs(step: Path) -> tuple[Counter[str], Counter[str]]:
    levels: Counter[str] = Counter()
    by_event: Counter[str] = Counter()
    for path in sorted((step / "logs").glob("errors.jsonl*")):
        for line in path.read_text().splitlines():
            try:
                record = json.loads(line)
            except ValueError:
                continue
            levels[record.get("level", "?")] += 1
            by_event[f"{record.get('level')} {record.get('event')}"] += 1
    return levels, by_event


def _monitor(path: Path) -> list[dict[str, str]]:
    return _stats(path) if path.exists() else []


def main(label: str, out: Path, step: Path) -> None:
    """Print the Markdown summary of one step."""
    rows = _stats(out / f"{label}_stats.csv")
    designed, errors = _failures(out / f"{label}_failures.csv")
    total = next(r for r in rows if r["Name"] == "Aggregated")
    requests_total = int(total["Request Count"])
    designed_total = sum(designed.values())
    error_total = sum(errors.values())
    print(f"### {label}\n")
    print("| endpoint | requests | req/s | p50 ms | p95 ms | p99 ms | max ms | failures |")
    print("| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: |")
    for r in rows:
        print(
            f"| {r['Type']} {r['Name']} | {r['Request Count']} | {float(r['Requests/s']):.1f} "
            f"| {r['50%']} | {r['95%']} | {r['99%']} | {float(r['Max Response Time']):.0f} "
            f"| {r['Failure Count']} |"
        )
    share = 100 * error_total / requests_total if requests_total else 0.0
    print(
        f"\nRequests {requests_total}; designed 429s {designed_total}; "
        f"errors {error_total} ({share:.2f} % excluding designed 429s)\n"
    )
    for name, counts in (("Designed 429s", designed), ("Errors", errors)):
        if counts:
            print(f"{name}:")
            for key, count in counts.most_common():
                print(f"- {key}: {count}")
            print()
    samples = _monitor(out / f"{label}_monitor.csv")
    if samples:
        rss = [float(s["rss_mb"]) for s in samples if float(s["rss_mb"]) > 0]
        cpu = [float(s["cpu"]) for s in samples if float(s["cpu"]) >= 0]
        queued = [int(s["queued"]) for s in samples if int(s["queued"]) >= 0]
        list_ms = [int(s["list_ms"]) for s in samples if int(s["list_ms"]) >= 0]
        print(
            f"Monitor ({len(samples)} samples): queued max {max(queued, default=-1)}, "
            f"last {queued[-1] if queued else -1}; RSS start {rss[0] if rss else -1:.0f} MB, "
            f"max {max(rss, default=-1):.0f} MB; CPU max {max(cpu, default=-1):.0f} %; "
            f"monitor list latency max {max(list_ms, default=-1)} ms\n"
        )
        print(
            "Queue over time (t_s:queued/running): "
            + ", ".join(f"{s['t_s']}:{s['queued']}/{s['running']}" for s in samples)
            + "\n"
        )
    levels, by_event = _logs(step)
    print(f"errors.jsonl levels: {dict(levels) or 'none'}")
    for key, count in by_event.most_common(12):
        print(f"- {key}: {count}")
    finished = Counter()
    app_log = step / "logs" / "app.jsonl"
    if app_log.exists():
        for line in app_log.read_text().splitlines():
            if '"investigation_finished"' in line or '"investigation_queued"' in line:
                finished[json.loads(line).get("event")] += 1
    print(f"\napp.jsonl investigation events: {dict(finished) or 'none'}")


if __name__ == "__main__":
    main(sys.argv[1], Path(sys.argv[2]), Path(sys.argv[3]))
