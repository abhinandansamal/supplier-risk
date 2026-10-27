"""Live warm-up of the snapshot cache with the real role keys (local Zetaris, SEC, Eurostat).

Slow (minutes): the SEC and Eurostat views read live REST sources. Uses the same concurrency
limit as the app (SNAPSHOT_MAX_CONCURRENCY, at most 2).
"""

import pytest

from app.core.settings import Settings
from app.tools.cache import SnapshotCache
from app.tools.views import VIEWS

pytestmark = [pytest.mark.integration, pytest.mark.slow_live]

# Row counts measured 2026-10-27 (docs/data-contract.md). ERP is fixed (seed 42); SEC and
# Eurostat grow when new filings or months are published.
ERP_ROWS = {
    "v_suppliers_full": 12,
    "v_suppliers_buyer": 12,
    "v_open_orders_full": 141,
    "v_open_orders_buyer": 141,
    "v_part_sourcing": 50,
    "v_deliveries": 1555,
    "v_contracts_full": 12,
}
EXTERNAL_MIN_ROWS = {"v_supplier_revenue": 921, "v_country_indicators": 1686}


async def test_warm_up_loads_every_view_with_the_real_role_keys(live_settings: Settings) -> None:
    if live_settings.zetaris_key_buyer is None or live_settings.zetaris_key_risk is None:
        pytest.skip("ZETARIS_KEY_BUYER and ZETARIS_KEY_RISK must be set")
    settings = live_settings.model_copy(
        update={"snapshot_max_concurrency": min(live_settings.snapshot_max_concurrency, 2)}
    )
    cache = SnapshotCache(settings)
    try:
        report = await cache.warm_all()
        assert report == {f"{k}/{v}": "ok" for v, spec in VIEWS.items() for k in spec.keys}
        for view, spec in VIEWS.items():
            for key in spec.keys:
                snapshot = await cache.get(key, view)
                assert snapshot.key == key
                assert snapshot.query_id
                if view in ERP_ROWS:
                    assert len(snapshot.rows) == ERP_ROWS[view], f"{key}/{view}"
                else:
                    assert len(snapshot.rows) >= EXTERNAL_MIN_ROWS[view], f"{key}/{view}"
        buyer = await cache.get("buyer", "v_country_indicators")
        risk = await cache.get("risk", "v_country_indicators")
        assert buyer is not risk
    finally:
        await cache.aclose()
