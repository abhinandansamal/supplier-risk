"""Tests for the Zetaris SQL files in data_pipeline/zetaris_sql and the generator behind them."""

import re
from pathlib import Path

import pytest

from data_pipeline.suppliers import COUNTRIES, load_suppliers
from data_pipeline.zetaris_sql import generate
from data_pipeline.zetaris_sql.statements import split_statements

SQL_DIR = Path(generate.__file__).resolve().parent
SQL_FILES = sorted(SQL_DIR.glob("*.sql"))
PUBLIC = [s for s in load_suppliers() if s.cik is not None]
PRIVATE = [s for s in load_suppliers() if s.cik is None]
USER_AGENT_PLACEHOLDER = "YOUR_APP_NAME YOUR_CONTACT_EMAIL"
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")
SECRET_PATTERNS = [
    re.compile(r"nvapi-", re.I),
    re.compile(r"\bBearer\s+\S", re.I),
    re.compile(r"\bsk-[A-Za-z0-9]{8,}"),
    re.compile(r"\bey[A-Za-z0-9_-]{20,}\.[A-Za-z0-9_-]{10,}"),  # JWT
]
RISK_VIEWS = (
    "v_suppliers_full",
    "v_suppliers_buyer",
    "v_open_orders_full",
    "v_open_orders_buyer",
    "v_part_sourcing",
    "v_deliveries",
    "v_contracts_full",
    "v_supplier_revenue",
    "v_country_indicators",
)
BUYER_VIEWS = {
    "v_suppliers_buyer",
    "v_open_orders_buyer",
    "v_part_sourcing",
    "v_deliveries",
    "v_country_indicators",
}
# Columns a buyer must never see, directly or derived (order value = qty x unit price).
BUYER_HIDDEN_COLUMNS = ("unit_price_eur", "order_value_eur", "annual_value_eur", "buyer_notes")


def _sql(name: str) -> str:
    return (SQL_DIR / name).read_text(encoding="utf-8")


def _statements(name: str) -> list[str]:
    return split_statements(_sql(name))


def _view_body(name: str, view: str) -> str:
    for statement in _statements(name):
        if re.match(rf"CREATE (OR REPLACE )?SCHEMASTORE VIEW {view}\b", statement):
            return statement
    raise AssertionError(f"view {view} not found in {name}")


# --- statement splitter ----------------------------------------------------------------------


def test_split_handles_quotes_comments_and_a_last_statement_without_semicolon() -> None:
    text = (
        "-- header; with a semicolon\n"
        "SELECT 'a;b' AS x;\n"
        '/* block; comment */ SELECT "c;d" AS y ;\n'
        "SELECT `e;f` FROM t -- tail; comment\n"
    )
    assert split_statements(text) == [
        "SELECT 'a;b' AS x",
        'SELECT "c;d" AS y',
        "SELECT `e;f` FROM t",
    ]


def test_split_ignores_empty_statements() -> None:
    assert split_statements(";;\n  ;-- only a comment\n") == []


def test_split_keeps_doubled_quotes_inside_strings() -> None:
    assert split_statements("SELECT 'it''s; fine' AS x;") == ["SELECT 'it''s; fine' AS x"]


# --- every file ------------------------------------------------------------------------------


def test_every_create_file_has_a_select_file() -> None:
    creates = {p.name.removesuffix("_create.sql") for p in SQL_DIR.glob("*_create.sql")}
    selects = {p.name.removesuffix("_select.sql") for p in SQL_DIR.glob("*_select.sql")}
    assert creates == selects
    assert {"10_erp_datasource", "20_edgar_suppliers", "30_eurostat_countries"} <= creates
    assert {"40_risk_views", "60_roles_grants"} <= creates


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_file_names_follow_the_convention(path: Path) -> None:
    assert re.fullmatch(r"[0-9]{2}_[a-z0-9_]+_(create|select)\.sql", path.name)


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_statements_have_no_trailing_semicolon_and_none_is_empty(path: Path) -> None:
    statements = split_statements(path.read_text(encoding="utf-8"))
    assert statements, f"{path.name} has no statements"
    for statement in statements:
        assert statement.strip()
        assert not statement.rstrip().endswith(";")


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_select_star(path: Path) -> None:
    for statement in split_statements(path.read_text(encoding="utf-8")):
        assert not re.search(r"SELECT\s+(DISTINCT\s+)?\*", statement, re.I), statement[:80]
        assert not re.search(r"\w\.\*", statement), statement[:80]


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_secrets_or_emails(path: Path) -> None:
    text = path.read_text(encoding="utf-8")
    assert not EMAIL.search(text)
    for pattern in SECRET_PATTERNS:
        assert not pattern.search(text), pattern.pattern
    # The only password in any file is the placeholder the user replaces in the SQL Editor.
    for match in re.finditer(r'password\s+"([^"]*)"', text, re.I):
        assert match.group(1) == "<ERP_READER_PASSWORD>"


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_user_agent_headers_use_the_placeholder(path: Path) -> None:
    for value in re.findall(r'user-agent\s+"([^"]*)"', path.read_text(encoding="utf-8")):
        assert value == USER_AGENT_PLACEHOLDER


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_select_files_only_read(path: Path) -> None:
    if not path.name.endswith("_select.sql"):
        pytest.skip("create file")
    for statement in split_statements(path.read_text(encoding="utf-8")):
        assert re.match(r"(SELECT|SHOW|DESCRIBE)\b", statement, re.I), statement[:80]


# --- generated files are current -------------------------------------------------------------


def test_committed_files_match_the_generator() -> None:
    rendered = generate.render_all()
    for name, content in rendered.items():
        assert _sql(name) == content, (
            f"{name} is stale: run python -m data_pipeline.zetaris_sql.generate"
        )


def test_check_mode_reports_a_stale_file(tmp_path: Path) -> None:
    assert generate.main(["--out", str(tmp_path)]) == 0
    assert generate.main(["--check", "--out", str(tmp_path)]) == 0
    (tmp_path / "40_risk_views_create.sql").write_text("-- edited\n", encoding="utf-8")
    assert generate.main(["--check", "--out", str(tmp_path)]) == 1


# --- 10: ERP datasource ----------------------------------------------------------------------


def test_erp_datasource_uses_the_reader_and_a_password_placeholder() -> None:
    statements = _statements("10_erp_datasource_create.sql")
    create = next(s for s in statements if s.startswith("CREATE DATASOURCE erp_pg"))
    assert 'jdbcdriver "org.postgresql.Driver"' in create
    assert 'jdbcurl "jdbc:postgresql://host.docker.internal:5433/erp"' in create
    assert 'username "zetaris_reader"' in create
    assert 'password "<ERP_READER_PASSWORD>"' in create
    assert "REGISTER DATASOURCE TABLES FROM erp_pg" in statements


# --- 20: EDGAR -------------------------------------------------------------------------------


def test_edgar_has_one_rest_table_and_one_view_per_public_supplier() -> None:
    statements = _statements("20_edgar_suppliers_create.sql")
    assert statements[0].startswith("CREATE LIGHTNING DATABASE SR_EDGAR")
    assert "CREATE SCHEMASTORE CONTAINER sr_edgar" in statements
    tables = [s for s in statements if s.startswith("CREATE LIGHTNING REST TABLE")]
    views = [s for s in statements if s.startswith("CREATE OR REPLACE SCHEMASTORE VIEW")]
    assert len(tables) == len(views) == len(PUBLIC) == 8
    for supplier in PUBLIC:
        sid = supplier.supplier_id.lower()
        assert supplier.revenue_tag is not None
        table_name = f"{sid}_{generate.TAG_SLUGS[supplier.revenue_tag]}_facts"
        url = (
            "https://data.sec.gov/api/xbrl/companyconcept/"
            f"CIK{supplier.cik}/us-gaap/{supplier.revenue_tag}.json"
        )
        table = next(t for t in tables if f" {table_name} FROM SR_EDGAR" in t)
        assert f'endpoint "{url}"' in table
        assert f'user-agent "{USER_AGENT_PLACEHOLDER}"' in table
        view = _view_body("20_edgar_suppliers_create.sql", f"{sid}_revenue")
        assert "WITH CONTAINER sr_edgar" in view
        assert f"FROM sr_edgar.{table_name}" in view
        for column in ("period_start", "period_end", "fiscal_period", "form_type", "value_usd"):
            assert f"AS {column}" in view


def test_edgar_skips_private_suppliers() -> None:
    text = _sql("20_edgar_suppliers_create.sql")
    for supplier in PRIVATE:
        assert supplier.supplier_id.lower() not in text


# --- 30: Eurostat ----------------------------------------------------------------------------


def test_eurostat_has_both_indicators_for_all_six_countries() -> None:
    statements = _statements("30_eurostat_countries_create.sql")
    assert statements[0].startswith("CREATE LIGHTNING DATABASE SR_EUROSTAT")
    assert "CREATE SCHEMASTORE CONTAINER sr_eurostat" in statements
    tables = [s for s in statements if s.startswith("CREATE LIGHTNING REST TABLE")]
    assert len(tables) == 2 * len(COUNTRIES) == 12
    for country in COUNTRIES:
        geo = f"geo={country}&"
        assert any("/data/une_rt_m?" in t and geo in t for t in tables), country
        assert any("/data/prc_hicp_minr?" in t and geo in t for t in tables), country


def test_eurostat_uses_the_live_hicp_dataset() -> None:
    """prc_hicp_manr stopped at 2025-12; prc_hicp_minr (ECOICOP 2) replaces it."""
    text = _sql("30_eurostat_countries_create.sql")
    assert "prc_hicp_manr" not in text
    assert "coicop18=TOTAL&unit=RCH_A" in text
    assert "sex=T&age=TOTAL&unit=PC_ACT&s_adj=SA" in text


def test_eurostat_views_decode_with_both_coercions() -> None:
    for statement in _statements("30_eurostat_countries_create.sql"):
        if statement.startswith("CREATE SCHEMASTORE VIEW"):
            assert "from_json(to_json(value), 'map<string,double>')" in statement
            assert "from_json(to_json(dimension.time.category.index)" in statement


# --- 40: risk views --------------------------------------------------------------------------


def test_risk_views_are_exactly_the_app_facing_views() -> None:
    statements = _statements("40_risk_views_create.sql")
    assert statements[0] == "CREATE SCHEMASTORE CONTAINER risk"
    names = re.findall(
        r"CREATE OR REPLACE SCHEMASTORE VIEW (\w+) WITH CONTAINER risk",
        _sql("40_risk_views_create.sql"),
    )
    assert tuple(names) == RISK_VIEWS
    views = [s for s in statements if "SCHEMASTORE VIEW" in s]
    assert all(v.startswith("CREATE OR REPLACE SCHEMASTORE VIEW") for v in views)


@pytest.mark.parametrize("view", sorted(BUYER_VIEWS))
def test_buyer_views_hide_prices_and_contract_values(view: str) -> None:
    body = _view_body("40_risk_views_create.sql", view).lower()
    for column in BUYER_HIDDEN_COLUMNS:
        assert column not in body, f"{view} exposes {column}"


def test_full_views_carry_the_hidden_columns() -> None:
    assert "unit_price_eur" in _view_body("40_risk_views_create.sql", "v_open_orders_full")
    assert "annual_value_eur" in _view_body("40_risk_views_create.sql", "v_suppliers_full")
    assert "buyer_notes" in _view_body("40_risk_views_create.sql", "v_contracts_full")


def test_supplier_revenue_maps_each_cik_to_its_supplier() -> None:
    body = _view_body("40_risk_views_create.sql", "v_supplier_revenue")
    branches = body.split("UNION ALL")
    assert len(branches) == len(PUBLIC)
    for supplier, branch in zip(PUBLIC, branches, strict=True):
        assert f"'{supplier.supplier_id}' AS supplier_id" in branch
        assert f"'{supplier.cik}' AS cik" in branch
        assert f"FROM sr_edgar.{supplier.supplier_id.lower()}_revenue" in branch


def test_country_indicators_cover_every_country_and_indicator() -> None:
    body = _view_body("40_risk_views_create.sql", "v_country_indicators")
    branches = body.split("UNION ALL")
    assert len(branches) == 12
    for country in COUNTRIES:
        for indicator in ("unemployment_rate", "hicp_annual_rate"):
            assert any(
                f"'{country}' AS country_iso" in b and f"'{indicator}' AS indicator" in b
                for b in branches
            ), (country, indicator)


def test_risk_views_read_only_registered_sources() -> None:
    text = _sql("40_risk_views_create.sql")
    sources = set(re.findall(r"(?:FROM|JOIN)\s+(\w+)\.\w+", text))
    assert sources == {"erp_pg", "sr_edgar", "sr_eurostat"}


# --- 60: roles and grants --------------------------------------------------------------------


def _grants() -> dict[str, set[str]]:
    grants: dict[str, set[str]] = {}
    for statement in _statements("60_roles_grants_create.sql"):
        match = re.fullmatch(r"GRANT SELECT ON VIEW TABLE risk\.(\w+) TO ROLE '(\w+)'", statement)
        if match:
            grants.setdefault(match.group(2), set()).add(match.group(1))
    return grants


def test_roles_are_created_for_buyer_and_risk_manager_only() -> None:
    roles = [s for s in _statements("60_roles_grants_create.sql") if s.startswith("CREATE ROLE")]
    assert [re.search(r"'(\w+)'", r).group(1) for r in roles] == ["buyer", "risk_manager"]  # type: ignore[union-attr]


def test_role_descriptions_use_only_the_characters_zetaris_accepts() -> None:
    """CREATE ROLE ... DESCRIBE BY refuses spaces and colons (seen 2026-10-27)."""
    roles = [s for s in _statements("60_roles_grants_create.sql") if s.startswith("CREATE ROLE")]
    for statement in roles:
        match = re.fullmatch(r"CREATE ROLE '\w+' DESCRIBE BY '([^']*)'", statement)
        assert match, statement
        assert re.fullmatch(r"[A-Za-z0-9_.,-]+", match.group(1)), statement


def test_role_descriptions_are_the_ones_that_ran_in_zetaris() -> None:
    assert dict(generate.ROLES) == {
        "buyer": "Category_buyers,buyer_views_without_prices_or_contract_values",
        "risk_manager": "Risk_managers_and_directors,all_risk_views",
    }


def test_buyer_gets_only_the_buyer_views() -> None:
    assert _grants()["buyer"] == BUYER_VIEWS


def test_risk_manager_gets_every_risk_view() -> None:
    assert _grants()["risk_manager"] == set(RISK_VIEWS)


def test_grants_file_holds_only_roles_and_grants() -> None:
    for statement in _statements("60_roles_grants_create.sql"):
        assert re.match(r"(CREATE ROLE|GRANT SELECT ON VIEW TABLE risk\.)", statement)
        assert "WITH GRANT OPTION" not in statement


# --- docs/zetaris-setup.md lists what the user runs by hand ----------------------------------


@pytest.mark.parametrize("name", ["10_erp_datasource_create.sql", "60_roles_grants_create.sql"])
def test_setup_doc_lists_every_manual_statement(name: str) -> None:
    doc = (SQL_DIR.parents[1] / "docs" / "zetaris-setup.md").read_text(encoding="utf-8")
    flat_doc = " ".join(doc.split())
    for statement in _statements(name):
        assert " ".join(statement.split()) in flat_doc, statement[:80]


# --- re-runs never drop and never keep a stale table ------------------------------------------


@pytest.mark.parametrize("path", SQL_FILES, ids=lambda p: p.name)
def test_no_file_drops_anything(path: Path) -> None:
    for statement in split_statements(path.read_text(encoding="utf-8")):
        assert not re.match(r"(DROP|DELETE|TRUNCATE|REMOVE)\b", statement, re.I), statement[:80]


def test_every_candidate_tag_has_a_distinct_short_table_slug() -> None:
    """A tag change gives a new REST table name, so --skip-exists cannot keep the stale one."""
    from data_pipeline.suppliers import REVENUE_TAGS

    assert set(generate.TAG_SLUGS) == set(REVENUE_TAGS)
    slugs = list(generate.TAG_SLUGS.values())
    assert len(set(slugs)) == len(slugs)
    assert all(re.fullmatch(r"[a-z][a-z0-9_]{1,20}", s) for s in slugs)
