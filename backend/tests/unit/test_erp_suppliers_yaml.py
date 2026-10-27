"""data_pipeline/suppliers.yaml: schema and the mix of public and private suppliers."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from data_pipeline.suppliers import CATEGORIES, COUNTRIES, Supplier, load_suppliers


def _valid(**changes: object) -> dict[str, object]:
    base: dict[str, object] = {
        "supplier_id": "S0001",
        "name": "Example Corp",
        "cik": "0000097476",
        "category": "electronics",
        "country_iso": "DE",
        "revenue_tag": "Revenues",
    }
    return base | changes


def test_has_twelve_suppliers_with_unique_sequential_ids():
    suppliers = load_suppliers()
    assert [s.supplier_id for s in suppliers] == [f"S{i:04d}" for i in range(1, 13)]


def test_eight_public_and_four_private_suppliers():
    suppliers = load_suppliers()
    assert sum(s.cik is not None for s in suppliers) == 8
    assert sum(s.cik is None for s in suppliers) == 4


def test_every_category_and_only_eu_site_countries_are_used():
    suppliers = load_suppliers()
    assert {s.category for s in suppliers} == set(CATEGORIES)
    assert {s.country_iso for s in suppliers} <= set(COUNTRIES)


def test_public_suppliers_have_a_revenue_tag_and_private_ones_do_not():
    for supplier in load_suppliers():
        assert (supplier.revenue_tag is not None) == (supplier.cik is not None)


def test_ciks_are_unique_ten_digit_strings():
    ciks = [s.cik for s in load_suppliers() if s.cik]
    assert len(ciks) == len(set(ciks))
    assert all(len(c) == 10 and c.isdigit() for c in ciks)


@pytest.mark.parametrize(
    "changes",
    [
        {"supplier_id": "S001"},
        {"supplier_id": "X0001"},
        {"cik": "97476"},
        {"category": "food"},
        {"country_iso": "US"},
        {"revenue_tag": "NetIncomeLoss"},
        {"revenue_tag": None},
        {"cik": None},
        {"name": ""},
    ],
)
def test_rejects_invalid_supplier(changes: dict[str, object]):
    with pytest.raises(ValidationError):
        Supplier(**_valid(**changes))  # type: ignore[arg-type]


def test_rejects_duplicate_ids(tmp_path: Path):
    path = tmp_path / "s.yaml"
    path.write_text(yaml.safe_dump({"suppliers": [_valid(), _valid()]}), encoding="utf-8")
    with pytest.raises(ValueError, match="duplicate"):
        load_suppliers(path)


def test_rejects_unknown_fields():
    with pytest.raises(ValidationError):
        Supplier(**_valid(email="x@example.invalid"))  # type: ignore[arg-type]
