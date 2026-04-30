"""
agent/tools/social_tools.py — Pulse Analyst's vault tools (Sprint 19 §8.4.2).

Wraps `persistence/social_vectors.py` and `persistence/social_vault.py`. The
Pulse Analyst (`agent/agents/pulse_analyst.py`, T19.3) calls these and never
imports persistence.

Drift normalization handled here (audit §4):
    D-1: `social_vectors.similarity_search` returns key `similarity_score`;
         `knowledge_vectors.similarity_search` returns `similarity`. The
         wrapper renames `similarity_score` → `similarity` so downstream
         agents/nodes/synthesis see one canonical key across both vaults.
    D-3: parameter shape differs from knowledge_vectors:
            - persistence uses `min_impact: int` with sentinel 1 ("disable");
              the wrapper exposes `min_impact_level: Optional[int]` and
              translates None → 1.
            - persistence uses `min_reliability: float` with sentinel 0.0;
              the wrapper exposes `Optional[float]` and translates None → 0.0.
            - persistence param `platforms` already takes a list; wrapper
              renames to `source_platforms` for symmetry with knowledge_tools.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.2
    - data-pipeline/docs/sprint19_persistence_audit.md §3.2, §4 D-1/D-3, §6
"""

from __future__ import annotations

from typing import Optional

from persistence import social_vault, social_vectors


def similarity_search(
    query_embedding: list[float],
    limit: int,
    min_impact_level: Optional[int] = None,
    min_reliability: Optional[float] = None,
    source_platforms: Optional[list[str]] = None,
) -> list[dict]:
    """
    Run an HNSW similarity search against `social_vectors`.

    Spec call shape (§8.4.2 step 1): limit=10, no impact/reliability filters,
    no platform filter — Pulse Analyst splits Polymarket vs HackerNews on
    `source_platform` after the search, not via pre-filter.

    Args:
        query_embedding:  1536-dim float list.
        limit:            Top-k cap. Caller passes spec value explicitly.
        min_impact_level: Minimum impact_level (1-5). None disables (sent as 1).
        min_reliability:  Minimum reliability_score. None disables (sent as 0.0).
        source_platforms: Restrict to a list of platforms (e.g.
                          ['polymarket', 'hackernews']). None = all.

    Returns:
        List of row dicts ordered by similarity DESC. Each row carries:
        signal_id, canonical_event_id, silver_data_ref, source_platform,
        entry_type, published_at, content_vitals, enrichment_ai,
        social_context, platform_logic, and `similarity` (renamed from
        the persistence-side `similarity_score`).
    """
    impact_arg = min_impact_level if min_impact_level is not None else 1
    reliab_arg = min_reliability if min_reliability is not None else 0.0

    rows = social_vectors.similarity_search(
        query_vector=query_embedding,
        limit=limit,
        min_impact=impact_arg,
        platforms=source_platforms,
        min_reliability=reliab_arg,
    )
    # D-1: rename in place. social_vectors.similarity_search returns dicts
    # built from RealDictRow; mutating the dict is safe.
    for row in rows:
        if "similarity_score" in row:
            row["similarity"] = row.pop("similarity_score")
    return rows


def fetch_raw_comments(social_id: str) -> Optional[dict]:
    """
    Drill-down: retrieve the raw `social_vault` row for a `social_id`.

    Spec §8.4.2 step 5: when the Pulse Analyst spots a "consensus extreme"
    (e.g., a Polymarket market with extreme YES/NO sentiment), it follows
    the `silver_data_ref` (a `social_id` UUID) into the social_vault to
    inspect the original raw_comments / message_text / top_comments
    captured at the Silver layer.

    Args:
        social_id: UUID string from a `social_vectors` row's `silver_data_ref`.

    Returns:
        Full row from `social_vault` (social_id, canonical_event_id,
        raw_data_ref, source_name, platform_data JSONB, ingested_at), or
        None if not found. The platform-specific raw payload lives inside
        `platform_data`; see persistence/social_vault.py module docstring
        for the per-platform shape.
    """
    return social_vault.fetch_by_social_id(social_id)
