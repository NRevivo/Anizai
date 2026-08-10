"""
Calibration Models — the typed boundary between SQL rows and Python code.

Pydantic v2 models mirroring the five tables in `sql/init.sql`. Every
repository returns these; no calibration code outside `repos/` handles a raw
row dict. That keeps a schema change to two places (the DDL and this file)
instead of scattered across services.

Enum choices are `Literal[...]` rather than `enum.Enum` so they serialize to
plain strings for JSON and psycopg2 without a converter, and so a mismatch
against the SQL CHECK constraint is a type error visible in the diff.

Why the models validate what the database already CHECKs: the database is the
source of truth, but a ValidationError at construction names the offending
field and value, whereas a CheckViolation from psycopg2 names a constraint.
The former is what a developer actually needs at 2am.

References:
    - calibration_plan.md §4 (Postgres schema)
    - calibration_plan.md §6 T10A.4
"""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from typing import Any, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

# ==========================================================
# Shared literal types (mirrors of the SQL CHECK constraints)
# ==========================================================
Category = Literal["geopolitical", "financial", "ai", "other"]
Cohort = Literal["7d", "14d", "30-45d"]
CohortOrAll = Literal["7d", "14d", "30-45d", "all"]
QuestionStatus = Literal["open", "resolved", "archived"]
AddedBy = Literal["auto", "manual"]

ForecastStatus = Literal[
    "dispatched",
    "completed",
    "failed",
    "timed_out",
    # Terminal, and NOT a failure: the agent asked for clarification and V1
    # does not answer. Counted and displayed separately (plan §7).
    "needs_clarification",
]

Outcome = Literal["YES", "NO", "AMBIGUOUS"]
Tier = Literal["tier_1", "tier_2"]
RunType = Literal["initial_seed", "weekly_reforecast", "manual", "single_question"]
MetricType = Literal[
    "aggregate_brier",
    "calibration_curve",
    "cohort_brier",
    "improvement_curve",
    "source_contribution",
]

# The three cohorts, in the order they should be displayed everywhere.
COHORTS: tuple[Cohort, ...] = ("7d", "14d", "30-45d")

# Forecast statuses that mean "stop polling this row".
TERMINAL_FORECAST_STATUSES: frozenset[str] = frozenset(
    {"completed", "failed", "timed_out", "needs_clarification"}
)


class _Base(BaseModel):
    """
    Shared config.

    `from_attributes` lets a model be built straight from a RealDictCursor row.
    `str_strip_whitespace` prevents a slug with a trailing newline — pasted
    from a browser into the manual-add form — from becoming a distinct value
    that silently fails to match anything upstream.
    """

    model_config = ConfigDict(from_attributes=True, str_strip_whitespace=True)


# ==========================================================
# Table 1: calibration_questions
# ==========================================================

class Question(_Base):
    """One Polymarket-anchored question under measurement."""

    id: Optional[str] = None
    question_text: str = Field(min_length=1)
    polymarket_slug: str = Field(min_length=1)
    polymarket_condition_id: str = Field(min_length=1)
    category: Category
    cohort: Cohort
    expected_resolution_date: date
    liquidity_usd_at_pickup: Optional[Decimal] = None
    status: QuestionStatus = "open"
    added_by: AddedBy
    added_by_operator: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None

    @model_validator(mode="after")
    def _operator_only_for_manual(self) -> "Question":
        """
        `added_by_operator` is meaningful only for manual adds.

        Enforced here rather than in SQL because the rule is about provenance
        semantics, not data integrity: an auto-discovered question with an
        operator email attached means something has confused the two paths,
        and downstream provenance reporting would be wrong rather than broken.
        """
        if self.added_by == "auto" and self.added_by_operator:
            raise ValueError(
                "added_by_operator must be None for auto-discovered questions; "
                f"got {self.added_by_operator!r}"
            )
        return self

    @property
    def polymarket_url(self) -> str:
        """Operator-facing link to the market."""
        return f"https://polymarket.com/event/{self.polymarket_slug}"


# ==========================================================
# Table 2: calibration_forecasts
# ==========================================================

class Forecast(_Base):
    """
    One forecast the agent produced, or failed to produce.

    Phase 10A defines this model but does not populate it outside tests —
    dispatch and harvest land in Phase 10B.
    """

    id: Optional[str] = None
    question_id: str
    run_id: str
    forecast_run_index: int = Field(ge=0)
    session_id: str = Field(min_length=1)
    query_doc_id: str = Field(min_length=1)
    idempotency_key: str = Field(min_length=1)
    agent_version: Optional[str] = None
    final_probability: Optional[Decimal] = None
    confidence: Optional[Decimal] = None
    tier: Optional[Tier] = None
    status: ForecastStatus = "dispatched"
    error_message: Optional[str] = None
    agent_evidence_summary: Optional[dict[str, Any]] = None
    brier_score: Optional[Decimal] = None
    forecast_dispatched_at: Optional[datetime] = None
    forecast_completed_at: Optional[datetime] = None

    @field_validator("final_probability", "confidence")
    @classmethod
    def _unit_interval(cls, v: Optional[Decimal]) -> Optional[Decimal]:
        """Probabilities are 0-1 floats everywhere in this system (plan B4)."""
        if v is not None and not (Decimal(0) <= v <= Decimal(1)):
            raise ValueError(f"must be within [0, 1], got {v}")
        return v

    @property
    def is_terminal(self) -> bool:
        """True when the harvester should stop polling this row."""
        return self.status in TERMINAL_FORECAST_STATUSES

    @property
    def is_scorable(self) -> bool:
        """
        True when this forecast can contribute to a Brier score.

        Only half the inclusion rule (plan §8): the other half — that the
        question resolved non-ambiguously — is not knowable from the forecast
        row alone. Phase 10C combines the two.
        """
        return self.status == "completed" and self.final_probability is not None


# ==========================================================
# Table 3: calibration_resolutions
# ==========================================================

class Resolution(_Base):
    """Ground truth for one question, as reported by Polymarket."""

    id: Optional[str] = None
    question_id: str
    resolved_at: datetime
    detected_at: Optional[datetime] = None
    outcome: Outcome
    outcome_numeric: Optional[Decimal] = None
    resolution_source: str = "polymarket_clob"
    raw_resolution_data: dict[str, Any]

    @model_validator(mode="after")
    def _numeric_matches_outcome(self) -> "Resolution":
        """
        YES=1.0, NO=0.0, AMBIGUOUS=NULL — mirrors the SQL CHECK.

        The AMBIGUOUS case is the one that matters: an ambiguous market has no
        ground truth, and a 0.0 there would score every forecast as if the
        answer had been NO. That is a silent, plausible, entirely wrong number
        — exactly the kind this system exists to catch, so it must not
        generate one itself.
        """
        expected: dict[str, Optional[Decimal]] = {
            "YES": Decimal("1.0"),
            "NO": Decimal("0.0"),
            "AMBIGUOUS": None,
        }
        want = expected[self.outcome]
        if want is None:
            if self.outcome_numeric is not None:
                raise ValueError(
                    "outcome_numeric must be None for AMBIGUOUS, got "
                    f"{self.outcome_numeric}"
                )
        elif self.outcome_numeric is None or Decimal(self.outcome_numeric) != want:
            raise ValueError(
                f"outcome_numeric must be {want} for outcome {self.outcome}, "
                f"got {self.outcome_numeric}"
            )
        return self

    @property
    def is_scorable(self) -> bool:
        """AMBIGUOUS resolutions are recorded for audit but never scored."""
        return self.outcome != "AMBIGUOUS"


# ==========================================================
# Table 4: calibration_runs
# ==========================================================

class Run(_Base):
    """One execution of a calibration cycle."""

    id: Optional[str] = None
    run_type: RunType
    triggered_at: Optional[datetime] = None
    triggered_by: str = Field(min_length=1)
    questions_dispatched: Optional[int] = None
    forecasts_completed: Optional[int] = None
    forecasts_failed: Optional[int] = None
    finished_at: Optional[datetime] = None
    run_metadata: Optional[dict[str, Any]] = None

    @property
    def is_finished(self) -> bool:
        return self.finished_at is not None


# ==========================================================
# Table 5: calibration_metrics_snapshots
# ==========================================================

class MetricsSnapshot(_Base):
    """A point-in-time metric payload. Written by Phase 10C."""

    id: Optional[str] = None
    snapshot_at: Optional[datetime] = None
    metric_type: MetricType
    cohort: Optional[CohortOrAll] = None
    payload: dict[str, Any]


# ==========================================================
# Discovery candidate (not a table)
# ==========================================================

class MarketCandidate(_Base):
    """
    A Polymarket market that passed the discovery filters but has not been
    persisted yet.

    Distinct from `Question` on purpose. A candidate carries the raw signals
    the filter used (volume, days to resolution) which are not stored as such,
    and it lacks the identity fields a stored row has. Collapsing the two
    would mean `Question` grew nullable columns that exist only during
    discovery.
    """

    question_text: str
    polymarket_slug: str
    polymarket_condition_id: str
    category: Category
    cohort: Cohort
    expected_resolution_date: date
    volume_usd: Decimal
    days_to_resolution: int

    def to_question(self) -> Question:
        """Project into a persistable auto-discovered `Question`."""
        return Question(
            question_text=self.question_text,
            polymarket_slug=self.polymarket_slug,
            polymarket_condition_id=self.polymarket_condition_id,
            category=self.category,
            cohort=self.cohort,
            expected_resolution_date=self.expected_resolution_date,
            liquidity_usd_at_pickup=self.volume_usd,
            status="open",
            added_by="auto",
            added_by_operator=None,
        )
