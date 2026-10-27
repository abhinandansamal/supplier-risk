"""Registry of the nine app-facing Zetaris views and their one fixed SQL statement each.

Security (SEC-045, docs/decisions/0004-snapshot-reads-and-row-scope.md): the app never puts
model or user input into SQL. Each view is read whole with a constant SELECT listing its
columns; filtering by supplier, months or category happens in Python on the snapshot.
Zetaris enforces column scope (buyer views have no prices, values or notes) through the role
key's grants; the tool layer enforces row scope (a buyer's category) from the session.
"""

from collections.abc import Mapping
from dataclasses import dataclass, replace
from types import MappingProxyType
from typing import Literal

KeyName = Literal["buyer", "risk"]
# Cell kinds checked when a snapshot loads; columns not listed are free text.
Kind = Literal["supplier_id", "date", "month", "num", "int", "bool"]
Source = Literal["erp", "edgar", "eurostat"]
Role = Literal["buyer", "risk_manager", "director", "auditor"]

# Director decisions use the risk manager's data (docs/zetaris-setup.md); auditor has no key.
ROLE_KEY: Mapping[str, KeyName] = MappingProxyType(
    {"buyer": "buyer", "risk_manager": "risk", "director": "risk"}
)
# The Zetaris client is bound to an app role; each key name maps to one role.
KEY_CLIENT_ROLE: Mapping[KeyName, Literal["buyer", "risk_manager"]] = MappingProxyType(
    {"buyer": "buyer", "risk": "risk_manager"}
)

_BOTH: frozenset[KeyName] = frozenset({"buyer", "risk"})
_RISK: frozenset[KeyName] = frozenset({"risk"})


@dataclass(frozen=True)
class ViewSpec:
    """One app-facing view.

    Attributes:
        name: View name in container risk.
        columns: Columns in the order the snapshot SQL selects them.
        source: Where the data comes from (erp, edgar, eurostat).
        keys: Role keys granted SELECT on the view (docs/data-contract.md).
        kinds: Kind of each typed column; a row with a malformed cell is dropped at load.
    """

    name: str
    columns: tuple[str, ...]
    source: Source
    keys: frozenset[KeyName]
    kinds: Mapping[str, Kind] = MappingProxyType({})

    @property
    def sql(self) -> str:
        """Return the fixed snapshot SQL (explicit columns, no filter, no placeholder)."""
        return f"SELECT {', '.join(self.columns)} FROM risk.{self.name}"  # noqa: S608

    @property
    def template_id(self) -> str:
        """Return the SQL template id recorded in evidence."""
        return f"snapshot.{self.name}.v1"


_SUPPLIER_KINDS: Mapping[str, Kind] = MappingProxyType(
    {
        "supplier_id": "supplier_id",
        "site_lat": "num",
        "site_lon": "num",
        "annual_value_eur": "num",
        "contract_end_date": "date",
    }
)
_ORDER_KINDS: Mapping[str, Kind] = MappingProxyType(
    {
        "supplier_id": "supplier_id",
        "qty": "int",
        "unit_price_eur": "num",
        "order_value_eur": "num",
        "due_date": "date",
    }
)
_SUPPLIER_HEAD = ("supplier_id", "name", "cik", "country_iso", "category", "site_lat", "site_lon")
_ORDER_HEAD = ("po_id", "supplier_id", "part_id", "part_description", "criticality", "category")

_RAW_SPECS: tuple[ViewSpec, ...] = (
    ViewSpec(
        "v_suppliers_full",
        (*_SUPPLIER_HEAD, "annual_value_eur", "contract_end_date"),
        "erp",
        _RISK,
        _SUPPLIER_KINDS,
    ),
    ViewSpec(
        "v_suppliers_buyer",
        (*_SUPPLIER_HEAD, "contract_end_date"),
        "erp",
        _BOTH,
        _SUPPLIER_KINDS,
    ),
    ViewSpec(
        "v_open_orders_full",
        (*_ORDER_HEAD, "qty", "unit_price_eur", "order_value_eur", "due_date"),
        "erp",
        _RISK,
        _ORDER_KINDS,
    ),
    ViewSpec("v_open_orders_buyer", (*_ORDER_HEAD, "qty", "due_date"), "erp", _BOTH, _ORDER_KINDS),
    ViewSpec(
        "v_part_sourcing",
        (
            "part_id",
            "supplier_id",
            "part_description",
            "category",
            "criticality",
            "share_pct",
            "is_single_source",
        ),
        "erp",
        _BOTH,
        MappingProxyType(
            {"supplier_id": "supplier_id", "share_pct": "num", "is_single_source": "bool"}
        ),
    ),
    ViewSpec(
        "v_deliveries",
        (
            "po_id",
            "supplier_id",
            "part_id",
            "category",
            "status",
            "promised_date",
            "delivered_date",
            "days_late",
        ),
        "erp",
        _BOTH,
        MappingProxyType(
            {
                "supplier_id": "supplier_id",
                "promised_date": "date",
                "delivered_date": "date",
                "days_late": "int",
            }
        ),
    ),
    ViewSpec(
        "v_contracts_full",
        ("supplier_id", "annual_value_eur", "end_date", "buyer_notes"),
        "erp",
        _RISK,
        MappingProxyType(
            {"supplier_id": "supplier_id", "annual_value_eur": "num", "end_date": "date"}
        ),
    ),
    ViewSpec(
        "v_supplier_revenue",
        (
            "supplier_id",
            "cik",
            "fiscal_year",
            "fiscal_period",
            "period_start",
            "period_end",
            "form_type",
            "value_usd",
        ),
        "edgar",
        _RISK,
        MappingProxyType(
            {
                "supplier_id": "supplier_id",
                "fiscal_year": "int",
                "period_start": "date",
                "period_end": "date",
                "value_usd": "num",
            }
        ),
    ),
    ViewSpec(
        "v_country_indicators",
        ("country_iso", "indicator", "period", "value"),
        "eurostat",
        _BOTH,
        MappingProxyType({"period": "month", "value": "num"}),
    ),
)

# Shared kind maps may name columns a narrower (buyer) view does not have; keep only its own.
_SPECS: tuple[ViewSpec, ...] = tuple(
    replace(
        spec, kinds=MappingProxyType({c: k for c, k in spec.kinds.items() if c in spec.columns})
    )
    for spec in _RAW_SPECS
)
VIEWS: Mapping[str, ViewSpec] = MappingProxyType({spec.name: spec for spec in _SPECS})
VIEW_SQL: Mapping[str, str] = MappingProxyType({spec.name: spec.sql for spec in _SPECS})
ERP_VIEWS: tuple[str, ...] = tuple(spec.name for spec in _SPECS if spec.source == "erp")
# Live REST sources behind Zetaris: minutes per load, so they get a longer TTL.
EXTERNAL_VIEWS: tuple[str, ...] = tuple(spec.name for spec in _SPECS if spec.source != "erp")
