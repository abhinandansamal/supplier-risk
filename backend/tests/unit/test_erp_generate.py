"""Synthetic ERP generator: determinism, keys, value ranges and the four planted stories."""

import statistics
from collections import Counter, defaultdict
from datetime import date
from pathlib import Path

import pytest

from data_pipeline.suppliers import load_suppliers
from data_pipeline.synthetic_erp import generate as gen

TODAY = date(2026, 10, 27)
WINDOW_START = date(2025, 4, 27)  # 18 months before TODAY
ROOT = Path(__file__).resolve().parents[3]


@pytest.fixture(scope="module")
def data() -> gen.ErpData:
    return gen.generate(today=TODAY)


def _by(rows: list[dict], key: str) -> dict:
    return {row[key]: row for row in rows}


# --- determinism -----------------------------------------------------------------------------


def test_same_seed_gives_identical_rows(data: gen.ErpData):
    assert gen.generate(today=TODAY, seed=42).tables == data.tables


def test_different_seed_gives_different_rows(data: gen.ErpData):
    assert gen.generate(today=TODAY, seed=7).tables != data.tables


def test_default_seed_is_42(data: gen.ErpData):
    assert gen.generate(today=TODAY, seed=42).tables == data.tables
    assert gen.DEFAULT_SEED == 42


def test_written_files_are_byte_identical_across_runs(data: gen.ErpData, tmp_path: Path):
    gen.write_outputs(data, tmp_path / "a")
    gen.write_outputs(gen.generate(today=TODAY), tmp_path / "b")
    names = sorted(
        p.relative_to(tmp_path / "a") for p in (tmp_path / "a").rglob("*") if p.is_file()
    )
    assert names
    for name in names:
        assert (tmp_path / "a" / name).read_bytes() == (tmp_path / "b" / name).read_bytes()


def test_today_defaults_to_the_run_date_on_the_command_line():
    assert gen.parse_args([]).today == date.today()
    assert gen.parse_args(["--today", "2026-01-31"]).today == date(2026, 1, 31)
    assert gen.parse_args([]).seed == 42
    assert gen.parse_args([]).worsening_country == "CZ"


# --- shape -------------------------------------------------------------------------------------


def test_every_table_has_exactly_the_documented_columns(data: gen.ErpData):
    assert set(data.tables) == {
        "suppliers",
        "parts",
        "part_sourcing",
        "purchase_orders",
        "deliveries",
        "contracts",
    }
    for table, columns in gen.COLUMNS.items():
        assert data.tables[table], table
        assert all(list(row) == list(columns) for row in data.tables[table]), table


def test_suppliers_come_from_the_yaml(data: gen.ErpData):
    expected = {s.supplier_id: s for s in load_suppliers()}
    rows = _by(data.tables["suppliers"], "supplier_id")
    assert set(rows) == set(expected)
    for supplier_id, row in rows.items():
        assert row["cik"] == expected[supplier_id].cik
        assert row["name"] == expected[supplier_id].name
        assert row["category"] == expected[supplier_id].category
    assert all(34 <= r["site_lat"] <= 60 for r in rows.values())
    assert all(-10 <= r["site_lon"] <= 25 for r in rows.values())


# --- foreign keys -----------------------------------------------------------------------------


def test_foreign_keys_resolve(data: gen.ErpData):
    suppliers = _by(data.tables["suppliers"], "supplier_id")
    parts = _by(data.tables["parts"], "part_id")
    pos = _by(data.tables["purchase_orders"], "po_id")
    sourcing = {(r["part_id"], r["supplier_id"]) for r in data.tables["part_sourcing"]}
    for row in data.tables["part_sourcing"]:
        assert row["supplier_id"] in suppliers and row["part_id"] in parts
    for po in pos.values():
        assert po["supplier_id"] in suppliers
        assert po["part_id"] in parts
        assert (po["part_id"], po["supplier_id"]) in sourcing
        assert po["category"] == parts[po["part_id"]]["category"]
        assert po["category"] == suppliers[po["supplier_id"]]["category"]
    assert all(d["po_id"] in pos for d in data.tables["deliveries"])
    assert all(c["supplier_id"] in suppliers for c in data.tables["contracts"])


def test_primary_keys_are_unique(data: gen.ErpData):
    for table, key in [
        ("suppliers", ("supplier_id",)),
        ("parts", ("part_id",)),
        ("part_sourcing", ("part_id", "supplier_id")),
        ("purchase_orders", ("po_id",)),
        ("deliveries", ("po_id",)),
        ("contracts", ("supplier_id",)),
    ]:
        keys = [tuple(r[k] for k in key) for r in data.tables[table]]
        assert len(keys) == len(set(keys)), table


def test_every_supplier_has_a_contract_and_orders(data: gen.ErpData):
    suppliers = {r["supplier_id"] for r in data.tables["suppliers"]}
    assert {r["supplier_id"] for r in data.tables["contracts"]} == suppliers
    assert {r["supplier_id"] for r in data.tables["purchase_orders"]} == suppliers


# --- value ranges -----------------------------------------------------------------------------


def test_sourcing_shares_sum_to_100_and_single_source_flag_matches(data: gen.ErpData):
    shares: dict[str, list[float]] = defaultdict(list)
    flags: dict[str, set[bool]] = defaultdict(set)
    for row in data.tables["part_sourcing"]:
        assert 0 < row["share_pct"] <= 100
        shares[row["part_id"]].append(float(row["share_pct"]))
        flags[row["part_id"]].add(row["is_single_source"])
    assert set(shares) == {r["part_id"] for r in data.tables["parts"]}
    for part_id, values in shares.items():
        assert sum(values) == pytest.approx(100.0)
        assert flags[part_id] == {len(values) == 1}


def test_purchase_order_values_are_in_range(data: gen.ErpData):
    for po in data.tables["purchase_orders"]:
        assert po["qty"] > 0
        assert 0 < po["unit_price_eur"] < 100_000
        assert po["status"] in {"open", "delivered", "cancelled"}
        assert WINDOW_START <= po["due_date"] <= date(2026, 12, 15)
    assert {r["criticality"] for r in data.tables["parts"]} == {"A", "B", "C"}


def test_status_follows_due_date(data: gen.ErpData):
    for po in data.tables["purchase_orders"]:
        if po["due_date"] > date(2026, 10, 12):
            assert po["status"] in {"open", "cancelled"}
        if po["status"] == "delivered":
            assert po["due_date"] <= date(2026, 10, 12)


def test_deliveries_match_their_orders(data: gen.ErpData):
    pos = _by(data.tables["purchase_orders"], "po_id")
    delivered = 0
    for row in data.tables["deliveries"]:
        po = pos[row["po_id"]]
        assert po["status"] != "cancelled"
        assert row["promised_date"] == po["due_date"]
        if po["status"] == "delivered":
            delivered += 1
            assert row["delivered_date"] is not None
            assert row["delivered_date"] <= TODAY
            assert -5 <= (row["delivered_date"] - row["promised_date"]).days <= 20
        else:
            assert row["delivered_date"] is None
    assert delivered > 500


def test_contract_values_are_in_range(data: gen.ErpData):
    for row in data.tables["contracts"]:
        assert 500_000 <= row["annual_value_eur"] <= 100_000_000
        assert TODAY.year - 1 <= row["end_date"].year <= TODAY.year + 4
        assert 0 < len(row["buyer_notes"]) < 1000


# --- planted stories --------------------------------------------------------------------------


def _late_rates(data: gen.ErpData) -> dict[str, tuple[float, float]]:
    """Return (first-half rate, second-half rate) of late deliveries per supplier."""
    pos = _by(data.tables["purchase_orders"], "po_id")
    per_supplier: dict[str, list[tuple[date, bool]]] = defaultdict(list)
    for row in data.tables["deliveries"]:
        if row["delivered_date"] is not None:
            supplier = pos[row["po_id"]]["supplier_id"]
            per_supplier[supplier].append(
                (row["promised_date"], row["delivered_date"] > row["promised_date"])
            )
    rates = {}
    for supplier, items in per_supplier.items():
        items.sort()
        half = len(items) // 2
        first = statistics.mean(late for _, late in items[:half])
        second = statistics.mean(late for _, late in items[half:])
        rates[supplier] = (first, second)
    return rates


def test_story_1_one_supplier_has_rising_late_deliveries(data: gen.ErpData):
    rates = _late_rates(data)
    first, second = rates[data.stories.late_supplier_id]
    assert second >= 0.35
    assert second >= 2 * first
    for supplier, (_, other_second) in rates.items():
        if supplier != data.stories.late_supplier_id:
            assert other_second < 0.30, supplier


def test_story_1_supplier_is_public(data: gen.ErpData):
    suppliers = _by(data.tables["suppliers"], "supplier_id")
    assert suppliers[data.stories.late_supplier_id]["cik"] is not None


def test_story_2_exactly_one_single_source_a_part_with_a_large_open_order(data: gen.ErpData):
    parts = _by(data.tables["parts"], "part_id")
    single = {r["part_id"] for r in data.tables["part_sourcing"] if r["is_single_source"]}
    single_a = [p for p in single if parts[p]["criticality"] == "A"]
    assert single_a == [data.stories.single_source_part_id]
    open_values = [
        po["qty"] * po["unit_price_eur"]
        for po in data.tables["purchase_orders"]
        if po["status"] == "open"
    ]
    story_values = [
        po["qty"] * po["unit_price_eur"]
        for po in data.tables["purchase_orders"]
        if po["status"] == "open" and po["part_id"] == data.stories.single_source_part_id
    ]
    assert story_values
    assert max(story_values) >= 500_000
    assert max(story_values) >= 10 * statistics.median(open_values)
    assert max(open_values) == max(story_values)


def test_story_3_one_supplier_sits_in_the_worsening_country(data: gen.ErpData):
    suppliers = _by(data.tables["suppliers"], "supplier_id")
    assert data.stories.worsening_country == "CZ"
    assert suppliers[data.stories.worsening_supplier_id]["country_iso"] == "CZ"
    assert Counter(r["country_iso"] for r in suppliers.values())["CZ"] == 1


def test_story_3_country_is_a_single_parameter():
    other = gen.generate(today=TODAY, worsening_country="PL")
    assert other.stories.worsening_country == "PL"
    suppliers = _by(other.tables["suppliers"], "supplier_id")
    assert suppliers[other.stories.worsening_supplier_id]["country_iso"] == "PL"


@pytest.mark.parametrize("country", ["US", "cz", "", "XX"])
def test_story_3_rejects_non_eu_site_countries(country: str):
    with pytest.raises(ValueError, match="country"):
        gen.generate(today=TODAY, worsening_country=country)


def test_story_4_exactly_one_buyer_note_has_the_prompt_injection(data: gen.ErpData):
    hits = [c for c in data.tables["contracts"] if gen.INJECTION_SENTENCE in c["buyer_notes"]]
    assert [c["supplier_id"] for c in hits] == [data.stories.injection_supplier_id]
    assert "ignore" in gen.INJECTION_SENTENCE.lower()
    others = [c for c in data.tables["contracts"] if c not in hits]
    assert not any("ignore" in c["buyer_notes"].lower() for c in others)


# --- output files -----------------------------------------------------------------------------


def test_write_outputs_creates_schema_seed_and_csvs(data: gen.ErpData, tmp_path: Path):
    gen.write_outputs(data, tmp_path)
    schema = (tmp_path / "schema.sql").read_text(encoding="utf-8")
    seed = (tmp_path / "seed.sql").read_text(encoding="utf-8")
    for table in gen.COLUMNS:
        assert f"CREATE TABLE {table} " in schema
        assert f"INSERT INTO {table} " in seed
        header = (tmp_path / "csv" / f"{table}.csv").read_text(encoding="utf-8").splitlines()[0]
        assert header == ",".join(gen.COLUMNS[table])
    assert "REFERENCES" in schema and "PRIMARY KEY" in schema


def test_seed_escapes_quotes_in_text(data: gen.ErpData, tmp_path: Path):
    gen.write_outputs(data, tmp_path)
    seed = (tmp_path / "seed.sql").read_text(encoding="utf-8")
    assert gen.INJECTION_SENTENCE.replace("'", "''") in seed
    assert "Analog Devices, Inc." in seed


def test_csv_round_trips_row_counts(data: gen.ErpData, tmp_path: Path):
    gen.write_outputs(data, tmp_path)
    for table, rows in data.tables.items():
        lines = (tmp_path / "csv" / f"{table}.csv").read_text(encoding="utf-8").splitlines()
        assert len(lines) - 1 >= len(rows)  # quoted multi-line text may add physical lines


# --- documentation ----------------------------------------------------------------------------


def test_data_card_documents_each_story_and_the_synthetic_labels():
    card = (ROOT / "docs" / "data-card.md").read_text(encoding="utf-8")
    for needle in ("Story 1", "Story 2", "Story 3", "Story 4", "synthetic", "seed 42"):
        assert needle in card, needle


def test_data_contract_records_every_cik_check():
    contract = (ROOT / "docs" / "data-contract.md").read_text(encoding="utf-8")
    for supplier in load_suppliers():
        if supplier.cik:
            assert supplier.cik in contract
    assert "@" not in contract  # the declared User-Agent (name + email) is never written down


def test_schema_sql_declares_every_column():
    blocks = gen.SCHEMA_SQL.split("CREATE TABLE ")[1:]
    declared = {block.split(" ", 1)[0]: block for block in blocks}
    assert set(declared) == set(gen.COLUMNS)
    for table, columns in gen.COLUMNS.items():
        for column in columns:
            assert f"    {column} " in declared[table], (table, column)


def test_main_writes_all_files(tmp_path: Path):
    code = gen.main(["--today", "2026-10-27", "--out", str(tmp_path)])
    assert code == 0
    assert (tmp_path / "schema.sql").is_file()
    assert (tmp_path / "seed.sql").is_file()
    assert len(list((tmp_path / "csv").glob("*.csv"))) == 6
