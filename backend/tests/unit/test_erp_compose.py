"""docker-compose.yml and the erp-db init scripts: static checks (no Docker needed)."""

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

ROOT = Path(__file__).resolve().parents[3]
INIT = ROOT / "data_pipeline" / "synthetic_erp" / "init"


@pytest.fixture(scope="module")
def service() -> dict[str, Any]:
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    return compose["services"]["erp-db"]


def test_uses_postgres_16(service: dict[str, Any]):
    assert service["image"].startswith("postgres:16")


def test_port_is_bound_to_localhost_only(service: dict[str, Any]):
    assert service["ports"] == ["127.0.0.1:5433:5432"]


def test_data_lives_in_a_named_volume(service: dict[str, Any]):
    volumes = service["volumes"]
    data = [v for v in volumes if "/var/lib/postgresql/data" in v]
    assert data and not data[0].startswith((".", "/"))
    compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
    assert data[0].split(":")[0] in compose["volumes"]


def test_init_scripts_are_mounted_read_only(service: dict[str, Any]):
    mounts = [v for v in service["volumes"] if "docker-entrypoint-initdb.d" in v]
    assert mounts == ["./data_pipeline/synthetic_erp/init:/docker-entrypoint-initdb.d:ro"]


def test_has_a_healthcheck(service: dict[str, Any]):
    assert "pg_isready" in " ".join(service["healthcheck"]["test"])


def test_passwords_come_from_env_variables_and_are_required(service: dict[str, Any]):
    env = service["environment"]
    assert env["POSTGRES_PASSWORD"].startswith("${ERP_DB_PASSWORD:?")
    assert env["ERP_READER_PASSWORD"].startswith("${ERP_READER_PASSWORD:?")


def test_env_example_has_placeholders_without_values():
    lines = (ROOT / ".env.example").read_text(encoding="utf-8").splitlines()
    assert "ERP_DB_PASSWORD=" in lines
    assert "ERP_READER_PASSWORD=" in lines


def test_reader_role_grants_select_only():
    lines = (INIT / "03_reader_role.sh").read_text(encoding="utf-8").splitlines()
    script = "\n".join(line for line in lines if not line.lstrip().startswith("#")).upper()
    assert "CREATE ROLE ZETARIS_READER" in script
    assert "GRANT SELECT ON ALL TABLES" in script
    granted = re.findall(r"GRANT\s+(.*?)\s+ON\b", script)
    assert granted and all(g in {"SELECT", "CONNECT", "USAGE"} for g in granted)
    for forbidden in (
        r"\bINSERT\b",
        r"\bUPDATE\b",
        r"\bDELETE\b",
        r"\bTRUNCATE\b",
        r"\bALL PRIVILEGES\b",
    ):
        assert not re.search(forbidden, script), forbidden
    assert "NOSUPERUSER" in script and "NOCREATEROLE" in script


def test_reader_password_is_passed_as_a_psql_variable_not_in_the_script():
    script = (INIT / "03_reader_role.sh").read_text(encoding="utf-8")
    assert ":'reader_pw'" in script
    assert "$ERP_READER_PASSWORD" in script


def test_generated_sql_is_git_ignored():
    ignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
    assert "data_pipeline/synthetic_erp/out/" in ignore
    assert "data_pipeline/synthetic_erp/init/01_schema.sql" in ignore
    assert "data_pipeline/synthetic_erp/init/02_seed.sql" in ignore


def _role_sql() -> str:
    lines = (INIT / "03_reader_role.sh").read_text(encoding="utf-8").splitlines()
    return "\n".join(line for line in lines if not line.lstrip().startswith("#"))


def test_reader_cannot_use_large_object_write_functions():
    """Security: SEC-031. Large objects persist even when the session's read-only flag is undone."""
    sql = _role_sql()
    revoke = re.search(r"REVOKE EXECUTE ON FUNCTION(.*?)FROM PUBLIC", sql, re.S | re.I)
    assert revoke, "no REVOKE EXECUTE ON FUNCTION ... FROM PUBLIC"
    for function in ("lo_create", "lo_creat", "lo_from_bytea", "lo_put", "lowrite", "lo_import"):
        assert function in revoke.group(1), function


def test_reader_cannot_create_temp_tables():
    assert re.search(r"REVOKE TEMP(ORARY)? ON DATABASE erp FROM PUBLIC", _role_sql(), re.I)


def test_reader_has_timeouts_and_a_connection_limit():
    """Security: SEC-033."""
    sql = _role_sql()
    assert re.search(r"CONNECTION LIMIT\s+\d+", sql)
    assert re.search(r"SET statement_timeout\s*=\s*'\d+s'", sql)
    assert re.search(r"SET idle_in_transaction_session_timeout\s*=\s*'\d+s'", sql)


def test_container_has_memory_and_cpu_limits(service: dict[str, Any]):
    """Security: SEC-033."""
    assert service["mem_limit"]
    assert float(service["cpus"]) > 0
