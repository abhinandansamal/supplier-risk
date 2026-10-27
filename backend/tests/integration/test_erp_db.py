"""erp-db (docker compose): the read-only role zetaris_reader can read but never write."""

import os

import psycopg
import pytest
from dotenv import dotenv_values
from psycopg import errors

pytestmark = pytest.mark.integration

TABLES = ("suppliers", "parts", "part_sourcing", "purchase_orders", "deliveries", "contracts")


@pytest.fixture(scope="module")
def reader() -> psycopg.Connection:
    """Connect as zetaris_reader; the password comes from the environment or .env, never printed."""
    password = os.environ.get("ERP_READER_PASSWORD") or dotenv_values(".env").get(
        "ERP_READER_PASSWORD"
    )
    if not password:
        pytest.skip("ERP_READER_PASSWORD is not set")
    conn = psycopg.connect(
        host=os.environ.get("ERP_DB_HOST", "127.0.0.1"),
        port=int(os.environ.get("ERP_DB_PORT", "5433")),
        dbname=os.environ.get("ERP_DB_NAME", "erp"),
        user="zetaris_reader",
        password=password,
        connect_timeout=5,
        autocommit=True,
    )
    yield conn
    conn.close()


def _count(conn: psycopg.Connection, table: str) -> int:
    row = conn.execute(f"SELECT count(*) FROM {table}").fetchone()  # noqa: S608 - fixed names
    assert row is not None
    return int(row[0])


def test_reader_counts_rows_in_every_table(reader: psycopg.Connection):
    counts = {table: _count(reader, table) for table in TABLES}
    assert counts["suppliers"] == 12
    assert all(n > 0 for n in counts.values()), counts


def test_reader_sees_the_planted_prompt_injection_note(reader: psycopg.Connection):
    row = reader.execute(
        "SELECT count(*) FROM contracts WHERE buyer_notes ILIKE '%ignore all previous%'"
    ).fetchone()
    assert row is not None and row[0] == 1


@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO suppliers (supplier_id, name, country_iso, category, site_lat, site_lon) "
        "VALUES ('S9999', 'x', 'DE', 'metals', 50, 10)",
        "UPDATE suppliers SET name = 'x'",
        "DELETE FROM suppliers",
        "TRUNCATE suppliers",
        "DROP TABLE suppliers",
        "CREATE TABLE evil (id int)",
        "CREATE ROLE evil",
    ],
)
def test_reader_cannot_write_or_change_the_schema(reader: psycopg.Connection, statement: str):
    before = _count(reader, "suppliers")
    with pytest.raises((errors.InsufficientPrivilege, errors.ReadOnlySqlTransaction)):
        reader.execute(statement)  # type: ignore[arg-type]
    assert _count(reader, "suppliers") == before
