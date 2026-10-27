"""Evidence records: what a tool read, from which snapshot, with which parameters.

Every tool result creates one record per view it used. Rows are untrusted data copied from
Zetaris (at most MAX_SAMPLE_ROWS per record); claims cite records by evidence_id.
"""

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

EvidenceSource = Literal["erp", "edgar", "eurostat", "noaa"]
MAX_SAMPLE_ROWS = 20
# Bounds one investigation's log (tools x views x suppliers stays far below this).
MAX_RECORDS = 2000


class EvidenceLogFull(Exception):  # noqa: N818
    """The investigation's evidence log reached MAX_RECORDS."""


def new_evidence_id() -> str:
    """Return a new, unguessable evidence id such as "ev-1a2b3c4d5e6f"."""
    return f"ev-{uuid.uuid4().hex[:12]}"


class EvidenceRecord(BaseModel):
    """One piece of evidence.

    Attributes:
        evidence_id: Id cited by claims.
        tool: Tool that produced it.
        source: Data source behind the view (noaa for the not-assessed weather gap).
        view: Zetaris view read, None when no view was read.
        sql_template_id: Fixed SQL template of the snapshot, None when no view was read.
        role: Session role the tool ran for.
        params: Validated tool parameters (scope fields are never parameters).
        rows: Up to MAX_SAMPLE_ROWS rows the result rests on (untrusted data).
        row_count: Number of rows the result rests on (after scope and filters).
        zetaris_query_id: queryId of the snapshot load.
        snapshot_at: When the snapshot was loaded (UTC), so staleness is visible.
        retrieved_at: When the tool read the snapshot (UTC).
        note: Short fixed text, e.g. why something was not assessed.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    evidence_id: str = Field(default_factory=new_evidence_id)
    tool: str
    source: EvidenceSource
    view: str | None
    sql_template_id: str | None
    role: str
    params: dict[str, Any]
    rows: list[dict[str, Any]] = Field(max_length=MAX_SAMPLE_ROWS)
    row_count: int = Field(ge=0)
    zetaris_query_id: str | None
    snapshot_at: datetime | None
    retrieved_at: datetime
    note: str | None = None


class EvidenceLog:
    """Evidence records of one investigation, in the order they were made."""

    def __init__(self) -> None:
        """Start empty."""
        self._records: dict[str, EvidenceRecord] = {}

    def add(self, record: EvidenceRecord) -> str:
        """Store a record and return its id.

        Raises:
            EvidenceLogFull: If the log already holds MAX_RECORDS records.
        """
        if len(self._records) >= MAX_RECORDS:
            raise EvidenceLogFull("evidence log is full")
        self._records[record.evidence_id] = record
        return record.evidence_id

    def get(self, evidence_id: str) -> EvidenceRecord:
        """Return a record by id.

        Raises:
            KeyError: If the id is unknown.
        """
        return self._records[evidence_id]

    def all(self) -> list[EvidenceRecord]:
        """Return every record, oldest first."""
        return list(self._records.values())
