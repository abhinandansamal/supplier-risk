"""Pydantic models of the agent graph: plan, gaps, claims, report, proposals, trace, result.

Models that parse model output (PlanOutput, EvaluatorOutput, WriterOutput) ignore unknown keys
but are strict about types, enums and sizes; nothing the model writes can set a verified flag,
a status or a role.
"""

from datetime import UTC, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, computed_field

from app.evidence.models import EvidenceRecord
from app.score.model import RiskScore

Dimension = Literal["delivery", "concentration", "financial", "country", "weather"]
DIMENSIONS: tuple[Dimension, ...] = ("delivery", "concentration", "financial", "country", "weather")
Status = Literal["completed", "incomplete", "escalated", "rejected"]
GapIssue = Literal[
    "missing_evidence",
    "no_data",
    "needs_detail",
    "tool_failed",
    "no_tool_call",
    "source_unavailable",
    "not_assessed",
    "hidden_from_role",
    "contradiction",
]
ActionKind = Literal["second_source", "safety_stock", "supplier_review", "escalate"]
Impact = Literal["low", "medium", "high"]
EvidenceIdText = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_:.-]{1,64}$")]
SupplierIdText = Annotated[str, StringConstraints(pattern=r"^S[0-9]{4}$")]
MAX_CLAIMS = 12
MAX_CLAIM_CHARS = 600
MAX_MISSING_ITEMS = 10


class PlanOutput(BaseModel):
    """The planner's answer.

    Attributes:
        dimensions: Risk dimensions to check (unique, at least one).
        rationale: Short reason (kept in the trace length only, never on the board).
    """

    model_config = ConfigDict(extra="ignore")

    dimensions: Annotated[list[Dimension], Field(min_length=1, max_length=5)]
    rationale: Annotated[str, StringConstraints(max_length=500)] = ""


class Gap(BaseModel):
    """Something the investigation could not establish.

    Attributes:
        dimension: Dimension the gap is in.
        issue: What kind of gap it is.
        retryable: True when another research round may close it.
        evidence_ids: Evidence that shows the gap, if any.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    dimension: Dimension
    issue: GapIssue
    retryable: bool
    evidence_ids: list[str] = Field(default_factory=list)


class ModelGap(BaseModel):
    """A gap named by the evaluator model."""

    model_config = ConfigDict(extra="ignore")

    dimension: Dimension
    issue: Literal["missing_evidence", "needs_detail", "tool_failed"] = "needs_detail"


class ModelContradiction(BaseModel):
    """Two or more evidence records the evaluator model found in conflict."""

    model_config = ConfigDict(extra="ignore")

    dimension: Dimension
    evidence_ids: Annotated[list[EvidenceIdText], Field(min_length=1, max_length=10)]


class EvaluatorOutput(BaseModel):
    """The evaluator's answer."""

    model_config = ConfigDict(extra="ignore")

    gaps: Annotated[list[ModelGap], Field(max_length=10)] = Field(default_factory=list)
    contradictions: Annotated[list[ModelContradiction], Field(max_length=10)] = Field(
        default_factory=list
    )


class Verdict(BaseModel):
    """The evaluator's verdict on one round.

    Attributes:
        sufficient: True when no retryable gap and no contradiction is left.
        covered: Per planned dimension, whether evidence was found.
        gaps: Retryable and permanent gaps.
        contradictions: Dimensions with conflicting evidence.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    sufficient: bool
    covered: dict[Dimension, bool]
    gaps: list[Gap]
    contradictions: list[Dimension] = Field(default_factory=list)


class WriterClaim(BaseModel):
    """One claim as written by the writer model."""

    model_config = ConfigDict(extra="ignore")

    text: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=1, max_length=MAX_CLAIM_CHARS)
    ]
    evidence_ids: Annotated[list[EvidenceIdText], Field(min_length=1, max_length=20)]
    kind: Literal["fact", "hypothesis"] = "fact"


class WriterOutput(BaseModel):
    """The writer's answer."""

    model_config = ConfigDict(extra="ignore")

    claims: Annotated[list[WriterClaim], Field(min_length=1, max_length=MAX_CLAIMS)]
    missing: Annotated[
        list[Annotated[str, StringConstraints(max_length=300)]],
        Field(max_length=MAX_MISSING_ITEMS),
    ] = Field(default_factory=list)


class Claim(BaseModel):
    """A numbered claim of the report.

    Attributes:
        text: The claim.
        evidence_ids: Evidence it cites (never empty).
        kind: fact (in the data) or hypothesis (a cause the data does not show).
        verified: False when the verifier could not confirm it after the rewrite.
        problems: Verifier findings, e.g. "unsupported_number: 41".
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    text: Annotated[str, StringConstraints(min_length=1, max_length=MAX_CLAIM_CHARS)]
    evidence_ids: Annotated[list[str], Field(min_length=1, max_length=20)]
    kind: Literal["fact", "hypothesis"] = "fact"
    verified: bool = True
    problems: list[str] = Field(default_factory=list)


class Report(BaseModel):
    """The answer shown to the user.

    Attributes:
        headline: Python-built line per supplier with the computed rating.
        claims: Numbered claims with evidence ids.
        missing: What could not be checked.
        notes: Python-built notes (injection ignored, memory unavailable, fallbacks).
        source: writer (the model wrote the claims) or fallback (built from the score).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    headline: str
    claims: list[Claim]
    missing: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    source: Literal["writer", "fallback"] = "writer"


class ActionProposal(BaseModel):
    """A proposed action; it waits for an authorised human and is never executed here.

    Attributes:
        action_id: Id of the proposal.
        kind: What is proposed.
        supplier_id: Supplier it is about.
        impact: Impact level (decides who may approve, Feature 7b).
        evidence_ids: Evidence it rests on.
        rationale: Short reason written by the recommender (untrusted model text).
        status: Always pending here.
        idempotency_key: One key per (investigation, kind, supplier).
        proposed_by: User whose investigation proposed it.
        role: That user's role.
        created_at: When it was proposed (UTC).
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str
    kind: ActionKind
    supplier_id: str
    impact: Impact
    evidence_ids: list[str]
    rationale: str
    status: Literal["pending"] = "pending"
    idempotency_key: str
    proposed_by: str
    role: str
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TraceEvent(BaseModel):
    """One line of the agent activity trace ("How we got here").

    Attributes:
        seq: Position in the trace.
        at: When it happened (UTC).
        agent: Agent or step (guard, planner, research, ..., score).
        step: Sub-step, e.g. the research dimension.
        action: What happened (model_call, tool_call, step_done, model_fallback, ...).
        model_route: default, deep or fast.
        model_requested: Exact model id sent.
        routed_model: Model that served the call (receipt).
        receipt_id: Nightshift receipt id.
        cache_status: Nightshift cache status.
        from_cache: True when Nightshift answered from its cache.
        tokens: Total tokens.
        latency_ms: Duration.
        retries: Retries inside the client.
        tools: Tools called or offered.
        evidence_ids: Evidence created or cited.
        verdict: Short verdict (e.g. sufficient, insufficient, passed, failed).
        budget_left: Model calls left after this event.
        prompt_version: Version of the prompt used.
        note: Short fixed code (never model output or data text).
        error_type: Error class name on failures.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    seq: int
    at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    agent: str
    step: str | None = None
    action: str
    model_route: str | None = None
    model_requested: str | None = None
    routed_model: str | None = None
    receipt_id: str | None = None
    cache_status: str | None = None
    from_cache: bool | None = None
    tokens: int | None = None
    latency_ms: int | None = None
    retries: int | None = None
    tools: list[str] = Field(default_factory=list)
    evidence_ids: list[str] = Field(default_factory=list)
    verdict: str | None = None
    budget_left: int | None = None
    prompt_version: str | None = None
    note: str | None = None
    error_type: str | None = None


class InvestigationResult(BaseModel):
    """Everything an investigation produced.

    Attributes:
        investigation_id: Id (also the case board id).
        status: completed, incomplete, escalated or rejected.
        reason: Short code for incomplete, escalated and rejected results.
        message: Fixed text for the user (rejections, incomplete results).
        role: Role the investigation ran for.
        suppliers: Suppliers the question is about (within the user's scope).
        plan: Dimensions planned.
        checked: Dimensions with evidence.
        gaps: What is missing.
        report: The report (None when rejected).
        scores: Deterministic risk score per supplier.
        evidence: Every evidence record.
        proposals: Pending action proposals.
        trace: Agent activity trace.
        model_calls: Model calls used.
        memory: available or unavailable (World Model case board).
        duration_ms: Wall time.
    """

    model_config = ConfigDict(frozen=True, extra="forbid")

    investigation_id: str
    status: Status
    reason: str | None = None
    message: str | None = None
    role: str
    suppliers: list[str] = Field(default_factory=list)
    plan: list[Dimension] = Field(default_factory=list)
    checked: list[Dimension] = Field(default_factory=list)
    gaps: list[Gap] = Field(default_factory=list)
    report: Report | None = None
    scores: list[RiskScore] = Field(default_factory=list)
    evidence: list[EvidenceRecord] = Field(default_factory=list)
    proposals: list[ActionProposal] = Field(default_factory=list)
    trace: list[TraceEvent] = Field(default_factory=list)
    model_calls: int = 0
    memory: Literal["available", "unavailable"] = "available"
    duration_ms: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def claims(self) -> list[Claim]:
        """Return the report's claims (empty without a report)."""
        return list(self.report.claims) if self.report else []

    @computed_field  # type: ignore[prop-decorator]
    @property
    def score(self) -> RiskScore | None:
        """Return the score of the first supplier (most questions are about one)."""
        return self.scores[0] if self.scores else None
