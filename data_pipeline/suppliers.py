"""Load and validate data_pipeline/suppliers.yaml, the single list of suppliers.

The ERP generator and the EDGAR SQL generation both read the suppliers from here.
"""

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ConfigDict, Field, model_validator

SUPPLIERS_YAML = Path(__file__).resolve().parent / "suppliers.yaml"
CATEGORIES = ("electronics", "chemicals", "metals", "logistics")
COUNTRIES = ("DE", "PL", "IT", "CZ", "FR", "ES")
REVENUE_TAGS = ("Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax")

Category = Literal["electronics", "chemicals", "metals", "logistics"]
Country = Literal["DE", "PL", "IT", "CZ", "FR", "ES"]
RevenueTag = Literal["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax"]


class Supplier(BaseModel):
    """One supplier of the manufacturer.

    Attributes:
        supplier_id: Id in the form S0001.
        name: Company name (real for public suppliers, fictional for private ones).
        cik: 10-digit SEC CIK for public suppliers, None for private ones.
        category: Spend category.
        country_iso: Synthetic site country (an EU country).
        revenue_tag: us-gaap revenue tag verified on EDGAR; set exactly when cik is set.
    """

    model_config = ConfigDict(extra="forbid", frozen=True)

    supplier_id: str = Field(pattern=r"^S[0-9]{4}$")
    name: str = Field(min_length=1, max_length=120)
    cik: str | None = Field(pattern=r"^[0-9]{10}$")
    category: Category
    country_iso: Country
    revenue_tag: RevenueTag | None

    @model_validator(mode="after")
    def _tag_matches_cik(self) -> "Supplier":
        if (self.cik is None) != (self.revenue_tag is None):
            raise ValueError("revenue_tag must be set exactly when cik is set")
        return self


def load_suppliers(path: Path = SUPPLIERS_YAML) -> list[Supplier]:
    """Return the validated suppliers from a YAML file.

    Args:
        path: YAML file with a top-level `suppliers` list.

    Returns:
        The suppliers in file order.

    Raises:
        ValueError: If a supplier id is duplicated.
        pydantic.ValidationError: If a supplier is invalid.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    suppliers = [Supplier(**item) for item in raw["suppliers"]]
    ids = [s.supplier_id for s in suppliers]
    if len(ids) != len(set(ids)):
        raise ValueError("duplicate supplier_id in suppliers.yaml")
    return suppliers
