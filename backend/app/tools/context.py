"""Tool context and the helpers every tool uses: scoped reads, supplier lookup, evidence.

Security: the role key comes from the session role (ROLE_KEY), never from arguments, and
row scope is applied here: a buyer only sees rows of their own category. A supplier outside
the scope gets the same SupplierNotFound as an unknown id, so its existence does not leak.
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict

from app.evidence.models import (
    MAX_SAMPLE_ROWS,
    EvidenceLog,
    EvidenceRecord,
    EvidenceSource,
    FactValue,
    rows_digest,
)
from app.security.identity import UserContext
from app.tools.cache import Row, Snapshot, SnapshotCache
from app.tools.errors import SupplierNotFound, ToolForbidden
from app.tools.views import ROLE_KEY, VIEWS, KeyName

ToolStatus = Literal["ok", "no_data", "not_assessed"]


@dataclass(frozen=True)
class ToolContext:
    """Everything a tool may use besides its validated arguments.

    Attributes:
        user: Verified session identity (role and category scope).
        cache: Snapshot cache shared by the app.
        evidence: Evidence log of the current investigation.
        today: Reference date for windows and overdue checks.
    """

    user: UserContext
    cache: SnapshotCache
    evidence: EvidenceLog
    today: date

    @property
    def key(self) -> KeyName:
        """Return the Zetaris role key of the session.

        Raises:
            ToolForbidden: For roles without Zetaris access (auditor).
        """
        key = ROLE_KEY.get(self.user.role)
        if key is None:
            raise ToolForbidden("this role has no access to supplier data")
        return key


class ToolResult(BaseModel):
    """Compact result returned to the model.

    Attributes:
        tool: Tool name.
        status: ok, no_data, or not_assessed.
        evidence_ids: Evidence records this result rests on.
        data: The result; free text from the ERP appears only as quoted untrusted data.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    tool: str
    status: ToolStatus
    evidence_ids: list[str]
    data: dict[str, Any]


async def read(ctx: ToolContext, view: str) -> Snapshot:
    """Return the snapshot of a view for the session's role key."""
    return await ctx.cache.get(ctx.key, view)


def in_scope(ctx: ToolContext, row: Row) -> bool:
    """Return True if the session may see a row with a category column."""
    return ctx.user.role != "buyer" or row.get("category") == ctx.user.category


def scoped_rows(ctx: ToolContext, snapshot: Snapshot, supplier_id: str) -> list[Row]:
    """Return the rows of one supplier that the session may see."""
    return [r for r in snapshot.rows if r["supplier_id"] == supplier_id and in_scope(ctx, r)]


def suppliers_view(ctx: ToolContext) -> str:
    """Return the supplier view of the session's key (buyers never get contract values)."""
    return "v_suppliers_full" if ctx.key == "risk" else "v_suppliers_buyer"


async def supplier(ctx: ToolContext, supplier_id: str) -> tuple[Row, Snapshot]:
    """Return a supplier row the session may see, with its snapshot.

    Raises:
        SupplierNotFound: If the supplier is unknown or outside the session's scope.
    """
    snapshot = await read(ctx, suppliers_view(ctx))
    rows = scoped_rows(ctx, snapshot, supplier_id)
    if not rows:
        raise SupplierNotFound("supplier not found")
    return rows[0], snapshot


def record(
    ctx: ToolContext,
    tool: str,
    snapshot: Snapshot,
    params: dict[str, Any],
    rows: list[Row],
    note: str | None = None,
    facts: dict[str, FactValue] | None = None,
) -> str:
    """Add an evidence record for rows read from a snapshot and return its id.

    Args:
        ctx: Tool context.
        tool: Tool (or score step) that read the rows.
        snapshot: Snapshot the rows come from.
        params: Validated parameters.
        rows: Every row the result rests on (samples are cut to MAX_SAMPLE_ROWS).
        note: Short fixed text.
        facts: Numbers computed from all rows (SEC-066), checked by the claim verifier.

    Returns:
        The evidence id.
    """
    spec = VIEWS[snapshot.view]
    return ctx.evidence.add(
        EvidenceRecord(
            tool=tool,
            source=spec.source,
            view=snapshot.view,
            sql_template_id=spec.template_id,
            role=ctx.user.role,
            params=params,
            rows=[dict(r) for r in rows[:MAX_SAMPLE_ROWS]],
            row_count=len(rows),
            zetaris_query_id=snapshot.query_id,
            snapshot_at=snapshot.snapshot_at,
            retrieved_at=datetime.now(UTC),
            note=note,
            facts=facts or {},
            rows_digest=rows_digest(rows),
        )
    )


def record_computed(
    ctx: ToolContext,
    tool: str,
    params: dict[str, Any],
    facts: dict[str, FactValue],
    evidence_ids: list[str] | None = None,
    note: str | None = None,
) -> str:
    """Add a record for numbers computed in Python from other evidence, and return its id.

    Args:
        ctx: Tool context.
        tool: Step that computed the numbers (e.g. risk_score).
        params: Inputs, e.g. the supplier id.
        facts: The computed numbers.
        evidence_ids: Records the numbers were computed from (kept in params).
        note: Short fixed text.

    Returns:
        The evidence id.
    """
    if evidence_ids:
        params = {**params, "from_evidence": sorted(evidence_ids)}
    return ctx.evidence.add(
        EvidenceRecord(
            tool=tool,
            source="computed",
            view=None,
            sql_template_id=None,
            role=ctx.user.role,
            params=params,
            rows=[],
            row_count=0,
            zetaris_query_id=None,
            snapshot_at=None,
            retrieved_at=datetime.now(UTC),
            note=note,
            facts=facts,
        )
    )


def record_gap(
    ctx: ToolContext, tool: str, source: EvidenceSource, params: dict[str, Any], note: str
) -> str:
    """Add an evidence record for something that could not be read, and return its id."""
    return ctx.evidence.add(
        EvidenceRecord(
            tool=tool,
            source=source,
            view=None,
            sql_template_id=None,
            role=ctx.user.role,
            params=params,
            rows=[],
            row_count=0,
            zetaris_query_id=None,
            snapshot_at=None,
            retrieved_at=datetime.now(UTC),
            note=note,
        )
    )
