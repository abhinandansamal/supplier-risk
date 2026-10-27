"""Red-team tests for the synthetic ERP (feat/erp-data): reader role, scripts, SQL, SEC checks.

Each xfail(strict=True) test reproduces an open finding in docs/security-review.md. When the
finding is fixed, the test XPASSes (strict -> failure), so remove the marker in the fixing commit.
"""

import os
import shutil
import stat
import subprocess  # noqa: S404
import textwrap
from pathlib import Path

import httpx
import numpy as np
import psycopg
import pytest
import respx
import yaml
from dotenv import dotenv_values

from data_pipeline.synthetic_erp import generate as gen
from scripts import verify_ciks

ROOT = Path(__file__).resolve().parents[3]
ROLE_SCRIPT = ROOT / "data_pipeline/synthetic_erp/init/03_reader_role.sh"


def _reader() -> psycopg.Connection:
    password = os.environ.get("ERP_READER_PASSWORD") or dotenv_values(ROOT / ".env").get(
        "ERP_READER_PASSWORD"
    )
    if not password:
        pytest.skip("ERP_READER_PASSWORD is not set")
    return psycopg.connect(
        host="127.0.0.1",
        port=5433,
        dbname="erp",
        user="zetaris_reader",
        password=password,
        connect_timeout=5,
        autocommit=True,
    )


# --- reader role (integration; every statement runs inside a transaction that is rolled back) ---


@pytest.mark.integration
def test_reader_cannot_write_after_turning_read_only_off():
    """The read-only default is a session setting the reader can undo; writes must still fail."""
    with _reader() as conn:
        conn.execute(
            "SET default_transaction_read_only = off"
        )  # session only; conn is closed below
        conn.execute("BEGIN")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("SELECT lo_create(0)")  # persistent large object, owned by reader
        finally:
            conn.execute("ROLLBACK")


@pytest.mark.integration
@pytest.mark.xfail(strict=True, reason="SEC-032")
def test_reader_cannot_change_its_own_role_settings_or_password():
    """ALTER ROLE on itself is always allowed in Postgres; the deployment must not rely on it."""
    with _reader() as conn:
        conn.execute(
            "SET default_transaction_read_only = off"
        )  # session only; conn is closed below
        conn.execute("BEGIN")
        try:
            with pytest.raises(psycopg.errors.InsufficientPrivilege):
                conn.execute("ALTER ROLE zetaris_reader SET default_transaction_read_only = off")
        finally:
            conn.execute("ROLLBACK")


def test_reader_role_has_resource_limits():
    """The reader needs a statement timeout and a connection limit (no unbounded queries)."""
    text = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert "statement_timeout" in text
    assert "CONNECTION LIMIT" in text.upper()
    assert "idle_in_transaction_session_timeout" in text
    service = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))["services"][
        "erp-db"
    ]
    assert service["mem_limit"] and service["cpus"]


# --- erp_up.sh ---


@pytest.mark.xfail(strict=True, reason="SEC-034")
def test_erp_up_copies_the_files_that_the_generator_just_wrote(tmp_path: Path):
    """With --out the script must not copy the stale default out/ files into the init folder."""
    repo = tmp_path / "repo"
    (repo / "scripts").mkdir(parents=True)
    shutil.copy(ROOT / "scripts/erp_up.sh", repo / "scripts/erp_up.sh")
    erp = repo / "data_pipeline/synthetic_erp"
    (erp / "out").mkdir(parents=True)
    (erp / "init").mkdir()
    (erp / "out/schema.sql").write_text("STALE")
    (erp / "out/seed.sql").write_text("STALE")
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    fake_uv = bin_dir / "uv"
    fake_uv.write_text(
        textwrap.dedent(
            """\
            #!/bin/bash
            while [ $# -gt 0 ]; do
              if [ "$1" = "--out" ]; then out="$2"; fi
              shift
            done
            mkdir -p "$out"; echo FRESH > "$out/schema.sql"; echo FRESH > "$out/seed.sql"
            """
        )
    )
    fake_uv.chmod(fake_uv.stat().st_mode | stat.S_IEXEC)
    env = {**os.environ, "PATH": f"{bin_dir}:{os.environ['PATH']}"}
    other = tmp_path / "other_out"
    subprocess.run(  # noqa: S603
        ["bash", str(repo / "scripts/erp_up.sh"), "generate", "--out", str(other)],  # noqa: S607
        check=False,
        env=env,
        capture_output=True,
    )
    assert (erp / "init/01_schema.sql").read_text().strip() == "FRESH"


# --- compose / env ---


@pytest.mark.xfail(strict=True, reason="SEC-039")
def test_reader_password_is_not_in_the_container_environment():
    """`docker inspect` shows container env; the reader password must not live there."""
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    env = compose["services"]["erp-db"]["environment"]
    assert "ERP_READER_PASSWORD" not in env


@pytest.mark.xfail(strict=True, reason="SEC-040")
def test_env_example_app_url_uses_the_read_only_role():
    """The app must read the ERP as zetaris_reader, not as the owner/superuser `erp`."""
    values = dotenv_values(ROOT / ".env.example")
    assert "//zetaris_reader:" in str(values["ERP_DATABASE_URL"])


# --- generate.py SQL output ---


@pytest.mark.xfail(strict=True, reason="SEC-041")
def test_sql_literal_handles_numpy_scalars_and_non_finite_floats():
    """repr(np.float64) is 'np.float64(1.5)' on numpy 2 and nan/inf are not valid SQL numbers."""
    assert gen._sql_literal(np.float64(1.5)) == "1.5"
    with pytest.raises(ValueError):
        gen._sql_literal(float("nan"))


def test_sql_literal_escapes_quotes_backslashes_and_newlines():
    """Sound today: standard_conforming_strings is on, so only the quote needs doubling."""
    assert gen._sql_literal("a'b\\c\nd") == "'a''b\\c\nd'"


# --- verify_ciks.py ---

UA = "red-team redteam@example.invalid"


def _client(handler: httpx.MockTransport) -> httpx.Client:
    return httpx.Client(headers={"User-Agent": UA}, transport=handler)


@pytest.mark.xfail(strict=True, reason="SEC-035")
def test_rate_limit_holds_across_consecutive_ciks(monkeypatch: pytest.MonkeyPatch):
    """Two requests (even for different CIKs) must be at least 0.5 s apart on the clock."""
    now = [1000.0]
    stamps: list[float] = []
    monkeypatch.setattr(verify_ciks.time, "monotonic", lambda: now[0])
    monkeypatch.setattr(verify_ciks.time, "sleep", lambda s: now.__setitem__(0, now[0] + s))

    def handler(request: httpx.Request) -> httpx.Response:
        stamps.append(now[0])
        now[0] += 0.01  # network latency
        return httpx.Response(200, json={"name": "X"})

    client = _client(httpx.MockTransport(handler))
    verify_ciks.verify_cik(client, "97476")
    verify_ciks.verify_cik(client, "6281")
    gaps = [b - a for a, b in zip(stamps, stamps[1:], strict=False)]
    assert min(gaps) >= verify_ciks.MIN_INTERVAL_SECONDS - 1e-9


@pytest.mark.xfail(strict=True, reason="SEC-036")
def test_verify_fails_when_yaml_tag_differs_from_the_tag_found(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
):
    """suppliers.yaml says Revenues, SEC only has the other tag: the row must not be PASS."""
    other = verify_ciks.REVENUE_TAGS[1]

    def handler(request: httpx.Request) -> httpx.Response:
        if "companyconcept" in str(request.url) and "/Revenues.json" in str(request.url):
            return httpx.Response(404)
        return httpx.Response(200, json={"name": "X"})

    supplier = {"supplier_id": "S0002", "name": "A", "cik": "0000006281", "revenue_tag": "Revenues"}
    monkeypatch.setenv(verify_ciks.USER_AGENT_ENV, UA)
    monkeypatch.setattr(verify_ciks, "_load_suppliers", lambda: [supplier])
    monkeypatch.setattr(verify_ciks, "_client", lambda ua: _client(httpx.MockTransport(handler)))
    monkeypatch.setattr(verify_ciks, "MIN_INTERVAL_SECONDS", 0)
    code = verify_ciks.main(["verify"])
    assert other in verify_ciks.REVENUE_TAGS
    assert code == 1, capsys.readouterr().out


@pytest.mark.xfail(strict=True, reason="SEC-037")
def test_markdown_row_neutralises_pipes_and_newlines_from_sec_names():
    """The entity name comes from the SEC response; it must stay inside one table cell."""
    evil = "ACME |\n| S9999 | Fake | 0000000000 | X | Revenues | PASS"
    check = verify_ciks.CikCheck("0000000001", evil, "Revenues", ok=True)
    row = verify_ciks.markdown_row("S0001", "Real", check)
    assert "\n" not in row
    assert row.count("|") == 7  # 6 cells = 7 separators, no injected cells


@pytest.mark.xfail(strict=True, reason="SEC-038")
def test_non_json_200_is_reported_as_a_failed_check_not_a_traceback():
    """A proxy/captive-portal HTML page with status 200 must not crash the script."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, text="<html>blocked</html>")

    check = verify_ciks.verify_cik(_client(httpx.MockTransport(handler)), "97476", min_interval=0)
    assert check.ok is False


@pytest.mark.xfail(strict=True, reason="SEC-038")
@respx.mock
def test_rate_limited_response_is_not_reported_as_an_invalid_cik():
    """403/429 from the SEC means 'slow down', not 'CIK does not exist'; retry or abort."""
    respx.get("https://data.sec.gov/submissions/CIK0000097476.json").respond(429)
    with pytest.raises((httpx.HTTPStatusError, RuntimeError)):
        verify_ciks.verify_cik(
            _client(httpx.MockTransport(lambda r: httpx.Response(429))), "97476", min_interval=0
        )
