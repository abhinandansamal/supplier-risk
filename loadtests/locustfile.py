"""Locust flows for the Supplier Risk Investigator API (architecture 13.1).

Each virtual user logs in as one of the three demo users (round robin), then mixes: list
history, poll a queued/running investigation, read a finished report, start an investigation,
open the live SSE stream for a few seconds and read /auth/me, with 1-5 s think time.

Classification (so designed limits are not counted as errors):
- 2xx: success.
- 429 with code rate_limited, queue_full, too_many_attempts or too_many_streams: recorded as a
  failure with the message "designed_429 <code>" (summarize.py counts them apart).
- anything else (5xx, 4xx, connection errors, timeouts): "error <status> <code or type>".

A monitor greenlet (LOADTEST_MONITOR_CSV) logs in as the director every 5 s and records the
queue (queued, running, max queue_position from GET /api/v1/investigations) and the app
process RSS and CPU (LOADTEST_APP_PID). Profile "real" (LOADTEST_PROFILE=real) refuses to run
with more than 5 users and lets each user start only one investigation.
"""

import csv
import itertools
import os
import random
import time
from typing import Any

import gevent
import psutil
import requests
from locust import FastHttpUser, between, events, task
from locust.env import Environment
from locust.runners import WorkerRunner

API = "/api/v1"
COOKIE = "sr_session"
DESIGNED_429 = frozenset({"rate_limited", "queue_full", "too_many_attempts", "too_many_streams"})
FINISHED = frozenset({"completed", "incomplete", "escalated", "rejected", "failed"})
SSE_SECONDS = 3.0
MONITOR_SECONDS = 5.0
REAL_MAX_USERS = 5
PROFILE = os.environ.get("LOADTEST_PROFILE", "fake")
USERNAMES = ("buyer", "risk_manager", "director")
QUESTIONS = {
    "buyer": ["Why is Microchip (S0003) risky?", "Is S0001 a delivery risk?"],
    "risk_manager": ["Why is Microchip (S0003) risky?", "Why is Celanese (S0005) risky?"],
    "director": ["Why is Celanese (S0005) risky?", "Compare S0001 and S0003."],
}
_next_user = itertools.cycle(USERNAMES)


def password(username: str) -> str:
    """Return the demo password of a user from LOADTEST_PASSWORD_<USER> (fake values only)."""
    return os.environ[f"LOADTEST_PASSWORD_{username.upper()}"]


def classify(status: int, body: str, error: object = None) -> str | None:
    """Return None for success, else the failure message used in the failures CSV."""
    if 200 <= status < 300:
        return None
    code = "-"
    try:
        code = str(requests.models.complexjson.loads(body).get("code", "-"))
    except (ValueError, AttributeError, TypeError):
        pass
    if status == 429 and code in DESIGNED_429:
        return f"designed_429 {code}"
    if status == 0:
        # The cause (reset, refused, timeout) is in the message; keep it short for the CSV.
        cause = str(error).replace(",", ";")[:80]
        return f"error conn {type(error).__name__} {cause}"
    return f"error {status} {code}"


class SupplierRiskUser(FastHttpUser):
    """A buyer, risk manager or director using the UI's API."""

    wait_time = between(1, 5)
    network_timeout = 30.0
    connection_timeout = 10.0

    def on_start(self) -> None:
        """Log in (retrying a locked login twice) and remember the session cookie."""
        self.username = next(_next_user)
        self.token: str | None = None
        self.open_ids: list[str] = []
        self.done_ids: list[str] = []
        self.started = 0
        for _ in range(3):
            if self._login():
                return
            gevent.sleep(2 + random.random() * 3)  # noqa: S311 - not crypto

    def _login(self) -> bool:
        body = {"username": self.username, "password": password(self.username)}
        with self.client.post(
            f"{API}/auth/login", json=body, name="login", catch_response=True
        ) as resp:
            failure = classify(resp.status_code, resp.text or "", getattr(resp, "error", None))
            if failure:
                resp.failure(failure)
                return False
            cookie = resp.headers.get("set-cookie", "")
            self.token = (
                cookie.split(f"{COOKIE}=", 1)[1].split(";", 1)[0] if COOKIE in cookie else None
            )
            return self.token is not None

    def _get(self, path: str, name: str) -> dict[str, Any] | None:
        if self.token is None:
            return None
        headers = {"Cookie": f"{COOKIE}={self.token}"}
        with self.client.get(path, name=name, headers=headers, catch_response=True) as resp:
            failure = classify(resp.status_code, resp.text or "", getattr(resp, "error", None))
            if failure:
                resp.failure(failure)
                return None
            data: dict[str, Any] = resp.json()
            return data

    @task(5)
    def list_history(self) -> None:
        """List the user's investigations and sort them into open and finished."""
        data = self._get(f"{API}/investigations", "list history")
        if data is None:
            return
        rows = data.get("investigations", [])
        self.open_ids = [r["id"] for r in rows if r["status"] not in FINISHED][:20]
        self.done_ids = [r["id"] for r in rows if r["status"] in FINISHED][:20]

    @task(6)
    def poll_status(self) -> None:
        """Poll one queued or running investigation."""
        if not self.open_ids:
            return
        investigation_id = random.choice(self.open_ids)  # noqa: S311 - not crypto
        data = self._get(f"{API}/investigations/{investigation_id}", "poll status")
        if data and data.get("status") in FINISHED:
            self.open_ids.remove(investigation_id)
            self.done_ids.append(investigation_id)

    @task(3)
    def read_report(self) -> None:
        """Read one finished report with its claims, evidence and proposals."""
        if self.done_ids:
            investigation_id = random.choice(self.done_ids)  # noqa: S311 - not crypto
            self._get(f"{API}/investigations/{investigation_id}", "read report")

    @task(1)
    def start_investigation(self) -> None:
        """Start an investigation (the real profile allows one per user)."""
        if self.token is None or (PROFILE == "real" and self.started >= 1):
            return
        question = random.choice(QUESTIONS[self.username])  # noqa: S311 - not crypto
        headers = {"Cookie": f"{COOKIE}={self.token}"}
        with self.client.post(
            f"{API}/investigations",
            json={"question": question},
            headers=headers,
            name="start investigation",
            catch_response=True,
        ) as resp:
            failure = classify(resp.status_code, resp.text or "", getattr(resp, "error", None))
            if failure:
                resp.failure(failure)
                return
            self.started += 1
            self.open_ids.append(resp.json()["id"])

    @task(1)
    def me(self) -> None:
        """Read the session identity."""
        self._get(f"{API}/auth/me", "me")

    @task(1)
    def open_stream(self) -> None:
        """Open the SSE stream of one investigation for a few seconds, then leave."""
        ids = self.open_ids or self.done_ids
        if self.token is None or not ids:
            return
        investigation_id = random.choice(ids)  # noqa: S311 - not crypto
        url = f"{self.host}{API}/investigations/{investigation_id}/events"
        started = time.perf_counter()
        status, body, error, size = 0, "", None, 0
        try:
            with requests.get(
                url,
                cookies={COOKIE: self.token},
                stream=True,
                timeout=(10, SSE_SECONDS),
            ) as resp:
                status = resp.status_code
                elapsed = (time.perf_counter() - started) * 1000
                if status != 200:
                    body = resp.text
                else:
                    size = self._drain(resp)
        except requests.RequestException as exc:
            error = exc
            elapsed = (time.perf_counter() - started) * 1000
        failure = classify(status, body, error)
        events.request.fire(
            request_type="SSE",
            name="open stream",
            response_time=elapsed,
            response_length=size,
            exception=Exception(failure) if failure else None,
            context={},
        )

    @staticmethod
    def _drain(resp: requests.Response) -> int:
        """Read the stream until "done" or SSE_SECONDS pass; return the bytes read."""
        size = 0
        ends = time.monotonic() + SSE_SECONDS
        try:
            for chunk in resp.iter_content(chunk_size=1024):
                size += len(chunk)
                if b"event: done" in chunk or time.monotonic() > ends:
                    break
        except requests.RequestException:
            pass  # the read timeout is how a quiet stream ends here
        return size


def _director_session(host: str) -> requests.Session | None:
    session = requests.Session()
    try:
        resp = session.post(
            f"{host}{API}/auth/login",
            json={"username": "director", "password": password("director")},
            timeout=15,
        )
    except requests.RequestException:
        return None
    return session if resp.status_code == 200 else None


def _monitor(environment: Environment, path: str) -> None:
    """Record queue length and app RSS/CPU every MONITOR_SECONDS until the test stops."""
    pid = int(os.environ.get("LOADTEST_APP_PID", "0"))
    proc = psutil.Process(pid) if pid else None
    if proc:
        proc.cpu_percent(None)
    session: requests.Session | None = None
    started = time.monotonic()
    with open(path, "w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(
            ["t_s", "users", "queued", "running", "max_queue_position", "list_ms", "rss_mb", "cpu"]
        )
        while True:
            session = session or _director_session(environment.host or "")
            queued = running = max_pos = -1
            list_ms = -1.0
            if session is not None:
                t0 = time.perf_counter()
                try:
                    resp = session.get(f"{environment.host}{API}/investigations", timeout=30)
                    list_ms = (time.perf_counter() - t0) * 1000
                    if resp.status_code == 200:
                        rows = resp.json()["investigations"]
                        positions = [r["queue_position"] or 0 for r in rows]
                        queued = sum(1 for r in rows if r["status"] == "queued")
                        running = sum(1 for r in rows if r["status"] == "running")
                        max_pos = max(positions, default=0)
                    elif resp.status_code == 401:
                        session = None
                except requests.RequestException:
                    list_ms = (time.perf_counter() - t0) * 1000
            rss = cpu = -1.0
            if proc:
                try:
                    rss = proc.memory_info().rss / 2**20
                    cpu = proc.cpu_percent(None)
                except psutil.Error:
                    pass
            users = environment.runner.user_count if environment.runner else 0
            writer.writerow(
                [
                    round(time.monotonic() - started),
                    users,
                    queued,
                    running,
                    max_pos,
                    round(list_ms),
                    round(rss, 1),
                    cpu,
                ]
            )
            handle.flush()
            gevent.sleep(MONITOR_SECONDS)


@events.test_start.add_listener
def on_test_start(environment: Environment, **_: object) -> None:
    """Enforce the real-profile user cap and start the monitor."""
    options = environment.parsed_options
    users = getattr(options, "num_users", 0) or 0
    if PROFILE == "real" and users > REAL_MAX_USERS:
        raise SystemExit(f"profile real allows at most {REAL_MAX_USERS} users (NVIDIA 40 RPM)")
    path = os.environ.get("LOADTEST_MONITOR_CSV")
    if path and not isinstance(environment.runner, WorkerRunner):
        gevent.spawn(_monitor, environment, path)
