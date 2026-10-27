"""Tests for app.store.db: investigations, results, proposals, decisions, retention, erasure."""

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from app.store.db import AlreadyDecided, Store, check_database_url

NOW = datetime(2026, 10, 27, 12, 0, tzinfo=UTC)


def _proposal(action_id: str = "act-1", impact: str = "low") -> dict[str, Any]:
    return {
        "action_id": action_id,
        "kind": "safety_stock",
        "supplier_id": "S0003",
        "impact": impact,
        "evidence_ids": ["ev-1"],
        "rationale": "Late deliveries.",
        "status": "pending",
        "idempotency_key": f"key-{action_id}",
        "proposed_by": "buyer",
        "role": "buyer",
    }


@pytest.fixture
def store() -> Store:
    return Store("sqlite://")


def _finished(store: Store, inv: str = "inv-1", user: str = "buyer", **kwargs: Any) -> None:  # noqa: ANN401
    store.create_investigation(inv, user, "buyer", "electronics", "Why?", kwargs.get("at", NOW))
    store.save_result(
        inv,
        status="completed",
        reason=None,
        payload={"investigation_id": inv},
        proposals=kwargs.get("proposals", [_proposal(f"act-{inv}")]),
        model_calls=9,
        at=kwargs.get("at", NOW),
    )


@pytest.mark.parametrize(
    "url",
    ["postgresql://app:pw@localhost/app", "mysql://x", "sqlite+pysqlite:///../../etc/x.db?x=1"],
)
def test_only_plain_sqlite_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError, match="sqlite"):
        check_database_url(url)


def test_file_database_is_created_with_its_folder(tmp_path: Path) -> None:
    path = tmp_path / "data" / "app.db"
    store = Store(f"sqlite:///{path}")
    assert store.ping()
    assert path.exists()


def test_create_and_read_an_investigation(store: Store) -> None:
    store.create_investigation("inv-1", "buyer", "buyer", "electronics", "Why?", NOW)
    row = store.get_investigation("inv-1")
    assert row is not None
    assert (row.user_id, row.role, row.category, row.status) == (
        "buyer",
        "buyer",
        "electronics",
        "queued",
    )
    assert store.get_investigation("inv-2") is None


def test_list_is_newest_first_and_filters_by_user(store: Store) -> None:
    store.create_investigation("inv-1", "buyer", "buyer", "electronics", "a", NOW)
    store.create_investigation("inv-2", "risk", "risk_manager", None, "b", NOW + timedelta(1))
    assert [r.id for r in store.list_investigations()] == ["inv-2", "inv-1"]
    assert [r.id for r in store.list_investigations(user_id="buyer")] == ["inv-1"]


def test_save_result_stores_payload_and_pending_proposals(store: Store) -> None:
    _finished(store)
    row = store.get_investigation("inv-1")
    assert row is not None and row.status == "completed" and row.model_calls == 9
    assert store.get_result("inv-1") == {"investigation_id": "inv-1"}
    (proposal,) = store.list_proposals(investigation_id="inv-1")
    assert proposal.status == "pending" and proposal.impact == "low"


def test_a_decision_is_recorded_once(store: Store) -> None:
    _finished(store)
    decided = store.decide("act-inv-1", "approved", "buyer", "buyer", "ok", NOW)
    assert decided.status == "approved" and decided.decided_by == "buyer"
    with pytest.raises(AlreadyDecided):
        store.decide("act-inv-1", "rejected", "risk", "risk_manager", None, NOW)
    proposal = store.get_proposal("act-inv-1")
    assert proposal is not None and proposal.status == "approved"


def test_delete_removes_every_row_of_the_investigation(store: Store) -> None:
    _finished(store)
    store.decide("act-inv-1", "approved", "buyer", "buyer", None, NOW)
    _finished(store, "inv-2")
    assert store.delete_investigation("inv-1")
    assert store.get_investigation("inv-1") is None
    assert store.get_result("inv-1") is None
    assert store.get_proposal("act-inv-1") is None
    assert store.counts() == {"investigations": 1, "results": 1, "proposals": 1, "decisions": 0}
    assert not store.delete_investigation("inv-1")


def test_purge_removes_investigations_older_than_the_cutoff(store: Store) -> None:
    _finished(store, "inv-old", at=NOW - timedelta(days=31))
    _finished(store, "inv-new", at=NOW)
    assert store.purge_older_than(NOW - timedelta(days=30)) == ["inv-old"]
    assert [r.id for r in store.list_investigations()] == ["inv-new"]
    assert store.get_proposal("act-inv-old") is None


def test_unfinished_investigations_are_marked_interrupted(store: Store) -> None:
    store.create_investigation("inv-1", "buyer", "buyer", "electronics", "a", NOW)
    store.create_investigation("inv-2", "buyer", "buyer", "electronics", "b", NOW)
    store.set_status("inv-2", "running")
    _finished(store, "inv-3")
    assert sorted(store.mark_interrupted()) == ["inv-1", "inv-2"]
    row = store.get_investigation("inv-1")
    assert row is not None and (row.status, row.reason) == ("failed", "interrupted")


def test_question_text_is_stored_as_given_and_never_interpreted(store: Store) -> None:
    question = "'); DROP TABLE investigations; --"
    store.create_investigation("inv-1", "buyer", "buyer", "electronics", question, NOW)
    row = store.get_investigation("inv-1")
    assert row is not None and row.question == question
