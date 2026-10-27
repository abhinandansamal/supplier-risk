"""Evidence records: what a tool read, from which snapshot, with which parameters.

Every tool result creates one record per view it used. Rows are untrusted data copied from
Zetaris (at most MAX_SAMPLE_ROWS per record); claims cite records by evidence_id.

SEC-066: 20 sample rows cannot prove an aggregate over 57 deliveries, so each record also
carries the numbers the tool computed (`facts`) and a SHA-256 digest of every row they rest on.
The claim verifier checks numbers against the facts; the digest lets an auditor recompute them
from the snapshot.
"""

import hashlib
import json
import math
import re
import uuid
from collections.abc import Mapping, Sequence
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# computed: numbers derived in Python from other records (the risk score), no view read.
EvidenceSource = Literal["erp", "edgar", "eurostat", "noaa", "computed"]
FactValue = str | int | float | bool | None
MAX_SAMPLE_ROWS = 20
MAX_FACTS = 64
MAX_FACT_TEXT_CHARS = 64
_FACT_KEY = re.compile(r"[a-z][a-z0-9_]{0,63}")
# Bounds one investigation's log (tools x views x suppliers stays far below this).
MAX_RECORDS = 2000


class EvidenceLogFull(Exception):  # noqa: N818
    """The investigation's evidence log reached MAX_RECORDS."""


def rows_digest(rows: Sequence[Mapping[str, Any]]) -> str:
    """Return the SHA-256 hex digest of rows in order (canonical JSON, sorted keys).

    Args:
        rows: The rows a result rests on, in the order the tool used them.

    Returns:
        64 hex characters.
    """
    canonical = json.dumps(
        [dict(r) for r in rows], sort_keys=True, separators=(",", ":"), default=str
    )
    return hashlib.sha256(canonical.encode()).hexdigest()


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
        facts: Numbers the tool computed from all rows (flat, at most MAX_FACTS keys).
        rows_digest: SHA-256 of every row the result rests on, not only the samples.
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
    facts: dict[str, FactValue] = Field(default_factory=dict)
    rows_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")

    @field_validator("facts")
    @classmethod
    def _flat_finite_facts(cls, value: dict[str, FactValue]) -> dict[str, FactValue]:
        if len(value) > MAX_FACTS:
            raise ValueError(f"at most {MAX_FACTS} facts")
        for key, item in value.items():
            if not _FACT_KEY.fullmatch(key):
                raise ValueError("fact keys must be snake_case")
            if isinstance(item, float) and not math.isfinite(item):
                raise ValueError("facts must be finite numbers")
            if isinstance(item, str) and len(item) > MAX_FACT_TEXT_CHARS:
                raise ValueError("fact text too long")
        return value


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
