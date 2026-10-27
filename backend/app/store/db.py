"""App database (SQLite via SQLAlchemy 2): investigations, results, proposals and decisions.

Decision 0008: SQLite instead of Postgres for the hackathon (one API process, one worker).
Questions, results and proposals are the user's own data: DELETE removes all of them (GDPR
erasure) and a retention purge at startup removes anything older than RETENTION_DAYS.
Only bound parameters reach SQL; nothing here builds SQL from text.
"""

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    delete,
    func,
    select,
    text,
    update,
)
from sqlalchemy.engine import Engine, make_url
from sqlalchemy.exc import ArgumentError, IntegrityError, SQLAlchemyError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column
from sqlalchemy.pool import StaticPool

UNFINISHED = ("queued", "running")


class Base(DeclarativeBase):
    """Declarative base of the app tables."""


class InvestigationTable(Base):
    """One investigation (the question is the user's own data)."""

    __tablename__ = "investigations"

    id: Mapped[str] = mapped_column(String(32), primary_key=True)
    user_id: Mapped[str] = mapped_column(String(128), index=True)
    role: Mapped[str] = mapped_column(String(32))
    category: Mapped[str | None] = mapped_column(String(32))
    question: Mapped[str] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(32))
    reason: Mapped[str | None] = mapped_column(String(64))
    model_calls: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class ResultTable(Base):
    """The stored result of a finished investigation (evidence rows already removed)."""

    __tablename__ = "results"

    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), primary_key=True
    )
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class ProposalTable(Base):
    """A proposed action; only a decision is ever recorded, nothing is executed."""

    __tablename__ = "proposals"

    action_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    investigation_id: Mapped[str] = mapped_column(
        ForeignKey("investigations.id", ondelete="CASCADE"), index=True
    )
    kind: Mapped[str] = mapped_column(String(32))
    supplier_id: Mapped[str] = mapped_column(String(8))
    impact: Mapped[str] = mapped_column(String(8))
    status: Mapped[str] = mapped_column(String(16))
    idempotency_key: Mapped[str] = mapped_column(String(256), unique=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON)


class DecisionTable(Base):
    """The human decision on a proposal (one per proposal)."""

    __tablename__ = "decisions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action_id: Mapped[str] = mapped_column(
        ForeignKey("proposals.action_id", ondelete="CASCADE"), unique=True
    )
    decision: Mapped[str] = mapped_column(String(16))
    decided_by: Mapped[str] = mapped_column(String(128))
    role: Mapped[str] = mapped_column(String(32))
    comment: Mapped[str | None] = mapped_column(Text)
    decided_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class AlreadyDecided(Exception):  # noqa: N818
    """The proposal already has a decision (idempotency: one decision per proposal)."""


@dataclass(frozen=True)
class InvestigationRow:
    """An investigation as read from the store."""

    id: str
    user_id: str
    role: str
    category: str | None
    question: str
    status: str
    reason: str | None
    model_calls: int
    created_at: datetime
    finished_at: datetime | None


@dataclass(frozen=True)
class ProposalRow:
    """A proposal with its decision, if any."""

    action_id: str
    investigation_id: str
    owner_id: str
    kind: str
    supplier_id: str
    impact: str
    status: str
    payload: dict[str, Any]
    decided_by: str | None = None
    decided_role: str | None = None
    comment: str | None = None
    decided_at: datetime | None = None


def check_database_url(url: str) -> str:
    """Return the URL if it is a plain SQLite URL (decision 0008).

    Raises:
        ValueError: For any other database or for query options in the URL.
    """
    try:
        parsed = make_url(url)
    except ArgumentError:
        raise ValueError("APP_DATABASE_URL must be a sqlite URL") from None
    if parsed.drivername not in ("sqlite", "sqlite+pysqlite") or parsed.query:
        raise ValueError("APP_DATABASE_URL must be a sqlite URL without options")
    return url


def _utc(value: datetime | None) -> datetime | None:
    # SQLite drops the time zone; every stored time is UTC.
    return value.replace(tzinfo=UTC) if value is not None and value.tzinfo is None else value


def _investigation(row: InvestigationTable) -> InvestigationRow:
    created = _utc(row.created_at)
    assert created is not None  # noqa: S101 - the column is required
    return InvestigationRow(
        row.id,
        row.user_id,
        row.role,
        row.category,
        row.question,
        row.status,
        row.reason,
        row.model_calls,
        created,
        _utc(row.finished_at),
    )


class Store:
    """Synchronous access to the app tables (short SQLite transactions)."""

    def __init__(self, url: str) -> None:
        """Open (and create) the database.

        Args:
            url: sqlite:///path/app.db, or sqlite:// for an in-memory database (tests).

        Raises:
            ValueError: If the URL is not a plain SQLite URL.
        """
        check_database_url(url)
        database = make_url(url).database
        if database and database != ":memory:":
            Path(database).parent.mkdir(parents=True, exist_ok=True)
            self._engine: Engine = create_engine(
                url, connect_args={"check_same_thread": False, "timeout": 5}
            )
        else:
            # One shared connection, or every connection would see its own empty database.
            self._engine = create_engine(
                url, connect_args={"check_same_thread": False}, poolclass=StaticPool
            )
        with self._engine.connect() as connection:
            connection.execute(text("PRAGMA foreign_keys=ON"))
        Base.metadata.create_all(self._engine)

    @contextmanager
    def _session(self) -> Iterator[Session]:
        with Session(self._engine) as session, session.begin():
            session.execute(text("PRAGMA foreign_keys=ON"))
            yield session

    def close(self) -> None:
        """Close the connection pool."""
        self._engine.dispose()

    def ping(self) -> bool:
        """Return True when the database answers."""
        try:
            with self._engine.connect() as connection:
                return connection.execute(text("SELECT 1")).scalar() == 1
        except SQLAlchemyError:
            return False

    def create_investigation(
        self,
        investigation_id: str,
        user_id: str,
        role: str,
        category: str | None,
        question: str,
        at: datetime,
    ) -> None:
        """Insert a queued investigation."""
        with self._session() as session:
            session.add(
                InvestigationTable(
                    id=investigation_id,
                    user_id=user_id,
                    role=role,
                    category=category,
                    question=question,
                    status="queued",
                    model_calls=0,
                    created_at=at,
                )
            )

    def get_investigation(self, investigation_id: str) -> InvestigationRow | None:
        """Return one investigation, or None."""
        with self._session() as session:
            row = session.get(InvestigationTable, investigation_id)
            return _investigation(row) if row else None

    def list_investigations(
        self, user_id: str | None = None, limit: int = 50
    ) -> list[InvestigationRow]:
        """Return investigations, newest first (one user's, or all when user_id is None)."""
        query = select(InvestigationTable).order_by(InvestigationTable.created_at.desc())
        if user_id is not None:
            query = query.where(InvestigationTable.user_id == user_id)
        with self._session() as session:
            return [_investigation(r) for r in session.scalars(query.limit(limit))]

    def set_status(self, investigation_id: str, status: str, reason: str | None = None) -> None:
        """Change the status of an investigation."""
        with self._session() as session:
            session.execute(
                update(InvestigationTable)
                .where(InvestigationTable.id == investigation_id)
                .values(status=status, reason=reason)
            )

    def save_result(
        self,
        investigation_id: str,
        *,
        status: str,
        reason: str | None,
        payload: dict[str, Any],
        proposals: list[dict[str, Any]],
        model_calls: int,
        at: datetime,
    ) -> bool:
        """Store the result and its proposals; return False if the investigation is gone."""
        with self._session() as session:
            row = session.get(InvestigationTable, investigation_id)
            if row is None:
                return False
            row.status, row.reason, row.model_calls, row.finished_at = (
                status,
                reason,
                model_calls,
                at,
            )
            session.merge(ResultTable(investigation_id=investigation_id, payload=payload))
            for item in proposals:
                session.add(
                    ProposalTable(
                        action_id=item["action_id"],
                        investigation_id=investigation_id,
                        kind=item["kind"],
                        supplier_id=item["supplier_id"],
                        impact=item["impact"],
                        status="pending",
                        idempotency_key=item["idempotency_key"],
                        payload=json.loads(json.dumps(item, default=str)),
                    )
                )
        return True

    def get_result(self, investigation_id: str) -> dict[str, Any] | None:
        """Return the stored result payload, or None."""
        with self._session() as session:
            row = session.get(ResultTable, investigation_id)
            return dict(row.payload) if row else None

    def _proposals(self, session: Session, *conditions: Any) -> list[ProposalRow]:  # noqa: ANN401
        query = (
            select(ProposalTable, InvestigationTable.user_id, DecisionTable)
            .join(InvestigationTable, InvestigationTable.id == ProposalTable.investigation_id)
            .outerjoin(DecisionTable, DecisionTable.action_id == ProposalTable.action_id)
            .where(*conditions)
            .order_by(ProposalTable.investigation_id, ProposalTable.action_id)
            .limit(200)
        )
        rows = []
        for proposal, owner, decision in session.execute(query):
            rows.append(
                ProposalRow(
                    action_id=proposal.action_id,
                    investigation_id=proposal.investigation_id,
                    owner_id=owner,
                    kind=proposal.kind,
                    supplier_id=proposal.supplier_id,
                    impact=proposal.impact,
                    status=proposal.status,
                    payload=dict(proposal.payload),
                    decided_by=decision.decided_by if decision else None,
                    decided_role=decision.role if decision else None,
                    comment=decision.comment if decision else None,
                    decided_at=_utc(decision.decided_at) if decision else None,
                )
            )
        return rows

    def list_proposals(
        self,
        *,
        investigation_id: str | None = None,
        owner_id: str | None = None,
        status: str | None = None,
    ) -> list[ProposalRow]:
        """Return proposals, filtered by investigation, owner and status."""
        conditions = []
        if investigation_id is not None:
            conditions.append(ProposalTable.investigation_id == investigation_id)
        if owner_id is not None:
            conditions.append(InvestigationTable.user_id == owner_id)
        if status is not None:
            conditions.append(ProposalTable.status == status)
        with self._session() as session:
            return self._proposals(session, *conditions)

    def get_proposal(self, action_id: str) -> ProposalRow | None:
        """Return one proposal with its decision, or None."""
        with self._session() as session:
            found = self._proposals(session, ProposalTable.action_id == action_id)
            return found[0] if found else None

    def decide(
        self,
        action_id: str,
        decision: str,
        decided_by: str,
        role: str,
        comment: str | None,
        at: datetime,
    ) -> ProposalRow:
        """Record the one decision on a pending proposal. Nothing is executed.

        Raises:
            AlreadyDecided: If the proposal already has a decision.
            KeyError: If the proposal does not exist.
        """
        try:
            with self._session() as session:
                changed = session.execute(
                    update(ProposalTable)
                    .where(ProposalTable.action_id == action_id, ProposalTable.status == "pending")
                    .values(status=decision)
                )
                if changed.rowcount != 1:  # type: ignore[attr-defined]
                    if session.get(ProposalTable, action_id) is None:
                        raise KeyError(action_id)
                    raise AlreadyDecided(action_id)
                session.add(
                    DecisionTable(
                        action_id=action_id,
                        decision=decision,
                        decided_by=decided_by,
                        role=role,
                        comment=comment,
                        decided_at=at,
                    )
                )
        except IntegrityError:
            raise AlreadyDecided(action_id) from None
        found = self.get_proposal(action_id)
        assert found is not None  # noqa: S101 - just written
        return found

    def delete_investigation(self, investigation_id: str) -> bool:
        """Delete an investigation and everything stored with it; return True if it existed."""
        with self._session() as session:
            return self._delete(session, [investigation_id]) > 0

    @staticmethod
    def _delete(session: Session, ids: list[str]) -> int:
        if not ids:
            return 0
        actions = select(ProposalTable.action_id).where(ProposalTable.investigation_id.in_(ids))
        session.execute(delete(DecisionTable).where(DecisionTable.action_id.in_(actions)))
        session.execute(delete(ProposalTable).where(ProposalTable.investigation_id.in_(ids)))
        session.execute(delete(ResultTable).where(ResultTable.investigation_id.in_(ids)))
        result = session.execute(delete(InvestigationTable).where(InvestigationTable.id.in_(ids)))
        return int(result.rowcount)  # type: ignore[attr-defined]

    def purge_older_than(self, cutoff: datetime) -> list[str]:
        """Delete investigations created before the cutoff; return their ids."""
        with self._session() as session:
            ids = list(
                session.scalars(
                    select(InvestigationTable.id).where(InvestigationTable.created_at < cutoff)
                )
            )
            self._delete(session, ids)
        return ids

    def mark_interrupted(self) -> list[str]:
        """Mark investigations left queued or running by a stopped process as failed."""
        with self._session() as session:
            ids = list(
                session.scalars(
                    select(InvestigationTable.id).where(InvestigationTable.status.in_(UNFINISHED))
                )
            )
            if ids:
                session.execute(
                    update(InvestigationTable)
                    .where(InvestigationTable.id.in_(ids))
                    .values(status="failed", reason="interrupted")
                )
        return ids

    def counts(self) -> dict[str, int]:
        """Return the number of rows per table."""
        tables = {
            "investigations": InvestigationTable,
            "results": ResultTable,
            "proposals": ProposalTable,
            "decisions": DecisionTable,
        }
        with self._session() as session:
            return {
                name: int(session.scalar(select(func.count()).select_from(table)) or 0)
                for name, table in tables.items()
            }
