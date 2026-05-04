"""
agent/schemas.py — Shared Pydantic models for the hub graph (Sprint 20+).

This module owns the small set of Pydantic models that flow between nodes
and into Firestore. It is intentionally lean: types only, no I/O, no
helper functions beyond what the schemas themselves need. Nodes import
these models, build instances, and serialize via `.model_dump()` into
the dicts that LangGraph merges into `ForecastState`.

Why colocated here, not per-node:
    `EvidenceItem` is consumed by `rate_evidence`, `synthesize`, and
    `write_to_firestore`. Inlining it would force one of those nodes to
    own the canonical definition and the other two to import sideways
    (agent-design P1: single responsibility). This module IS the
    canonical home.

Why Pydantic v2 (not TypedDict):
    Field validation matters here — `relevance_score` must be in [0, 1],
    `credibility_tier` must be one of three values, `source_type` must
    be one of the eight Patch 10 enum values. TypedDict gives no runtime
    validation; Pydantic v2 raises early at construction so a bug in
    `rate_evidence` doesn't silently produce a malformed Firestore write.

Why model_config = ConfigDict(extra="forbid"):
    Catches programming errors at construction (e.g. typo in field name)
    instead of silently ignoring them. The shape of EvidenceItem is
    pinned by spec §8.5.5; we never want to accidentally accept an
    unspec'd field that downstream code would silently drop.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.5.5 (EvidenceItem schema)
    - data-pipeline/docs/agentic_hub_spec_patch.md Patch 10 (source_type
      enum, origin enum, frontend display-type mapping)
    - .claude/skills/evidence-handling/SKILL.md (rating semantics)
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


# ==========================================================
# Type aliases — closed enums per spec §8.5.5 + Patch 10
# ==========================================================
# Eight values per Patch 10. The "online_*" pair is reactive-search
# territory (Sprint 22+) but lives in the enum from Sprint 20 so the
# unified shape is stable as soon as reactive search lands.
SourceType = Literal[
    "vault_news",
    "vault_telegram",
    "vault_market",
    "vault_arxiv",
    "vault_hackernews",
    "vault_fred",
    "online_news",
    "online_blog",
]

# Two values: knowledge_vault for items pulled from PostgreSQL vaults,
# reactive_search for online-fetched items (Sprint 22+).
Origin = Literal["knowledge_vault", "reactive_search"]

# Three-tier credibility per evidence-handling skill. Vault items derive
# this in rate_evidence from their source_platform; reactive items will
# derive it from agent/config/source_allowlist.json (Sprint 22+).
CredibilityTier = Literal["tier_1", "tier_2", "tier_3"]

# Set during synthesis (Sprint 20 T20.3), not during rating.
ImpactOnForecast = Literal["increases", "decreases", "neutral", "context_only"]


# ==========================================================
# EvidenceItem — the unified evidence shape (Patch 10, §8.5.5)
# ==========================================================
class EvidenceItem(BaseModel):
    """
    One piece of evidence flowing through the graph.

    Constructed by `rate_evidence` from per-vault evidence packages,
    mutated by `synthesize` (sets influence fields), and written to
    Firestore by `write_to_firestore`.

    Fields are grouped to match spec §8.5.5:
        - Identity: evidence_id, source_type, origin
        - Content: title, snippet, url, source_domain, published_at, fetched_at
        - Ratings (rate_evidence): relevance_score, credibility_tier, recency_weight
        - Influence (synthesize): used_in_answer, impact_on_forecast,
          impact_magnitude, is_key_evidence, rank
        - Transparency: justification

    Defaults for influence fields are set so an item is fully-shaped after
    rate_evidence even before synthesis touches it. Synthesis overrides
    `used_in_answer`, `impact_on_forecast`, `impact_magnitude`; the
    deterministic post-synthesis pass sets `is_key_evidence` and `rank`.
    """

    model_config = ConfigDict(extra="forbid")

    # --- Identity ---
    evidence_id: str = Field(
        description="UUID assigned at construction; stable across the session."
    )
    source_type: SourceType
    origin: Origin

    # --- Content ---
    title: str
    snippet: str = Field(
        default="",
        description="First ~200 chars of the article body or summary.",
    )
    url: Optional[str] = None
    source_domain: str = Field(
        description="e.g. 'reuters.com', 'arxiv.org', 'fred.stlouisfed.org'."
    )
    published_at: datetime
    fetched_at: datetime

    # --- Ratings (set by rate_evidence) ---
    relevance_score: float = Field(
        ge=0.0,
        le=1.0,
        description="LLM-rated relevance to the user's question.",
    )
    credibility_tier: CredibilityTier
    recency_weight: float = Field(
        ge=0.0,
        le=1.0,
        description="Exponential-decay weight on age (7-day half-life default).",
    )

    # --- Influence (defaults filled at construction; set by synthesize) ---
    used_in_answer: bool = False
    impact_on_forecast: ImpactOnForecast = "context_only"
    impact_magnitude: float = Field(default=0.0, ge=0.0, le=1.0)
    is_key_evidence: bool = False
    rank: int = 0

    # --- Transparency ---
    justification: str = Field(
        default="",
        description="One user-facing sentence explaining why the item was used.",
    )


# ==========================================================
# Frontend display-type mapping (§8.5.5 + frontend-integration skill)
# ==========================================================
# Used by write_to_firestore (Sprint 20 T20.5) when populating the
# `type` field on each evidence subcollection doc. Keeping the mapping
# here so any node that needs it has one source of truth.
SOURCE_TYPE_TO_FRONTEND_TYPE: dict[str, str] = {
    "vault_news": "news",
    "online_news": "news",
    "vault_telegram": "social",
    "online_blog": "social",
    "vault_arxiv": "expert",
    "vault_market": "market",
    "vault_fred": "market",
    "vault_hackernews": "social",
}
