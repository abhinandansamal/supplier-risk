"""Argument models of the tools: everything the model may pass, strictly validated.

Security: scope fields (user, role, category) are never arguments; extra="forbid" rejects
them, and strict=True rejects type coercion (a supplier id must be a string like S0003).
"""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

SupplierId = Annotated[str, StringConstraints(pattern=r"^S[0-9]{4}$")]
CountryIso = Literal["DE", "PL", "IT", "CZ", "FR", "ES"]
Indicator = Literal["unemployment_rate", "hicp_annual_rate"]
RiskFilter = Literal["any", "green", "amber", "red", "insufficient"]
MAX_MONTHS = 24
MAX_YEARS = 5


class ToolArgs(BaseModel):
    """Base of all argument models: frozen, strict, no unknown fields."""

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)


class NoArgs(ToolArgs):
    """No arguments."""


class ListSuppliersArgs(ToolArgs):
    """Arguments of list_my_suppliers.

    Attributes:
        risk_filter: None lists suppliers only; "any" adds each score; a colour keeps only
            suppliers with that rating.
    """

    risk_filter: RiskFilter | None = None


class SupplierArgs(ToolArgs):
    """Arguments of tools about one supplier.

    Attributes:
        supplier_id: Supplier id, S followed by four digits.
    """

    supplier_id: SupplierId


class SupplierMonthsArgs(SupplierArgs):
    """Arguments of tools over a window of calendar months.

    Attributes:
        months: Calendar months back from the current one, 1 to MAX_MONTHS.
    """

    months: int = Field(default=6, ge=1, le=MAX_MONTHS)


class FinancialArgs(SupplierArgs):
    """Arguments of financial_trend.

    Attributes:
        metric: Financial metric; only revenue is onboarded.
        years: Number of fiscal years, 1 to MAX_YEARS.
    """

    metric: Literal["revenue"] = "revenue"
    years: int = Field(default=3, ge=1, le=MAX_YEARS)


class CountryArgs(ToolArgs):
    """Arguments of country_indicators.

    Attributes:
        country_iso: One of the six site countries.
        indicator: Eurostat indicator.
        months: Months of series to return, 12 to MAX_MONTHS (the 6-month change needs 12).
    """

    country_iso: CountryIso
    indicator: Indicator
    months: int = Field(default=12, ge=12, le=MAX_MONTHS)
