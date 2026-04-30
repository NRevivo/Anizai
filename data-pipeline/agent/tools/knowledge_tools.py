"""
agent/tools/knowledge_tools.py — Researcher's vault tools (Sprint 19 §8.4.1).

Wraps `persistence/knowledge_vectors.py` (HNSW similarity search) and
`persistence/knowledge_vault.py` (full-text drill-down). The Researcher agent
(`agent/agents/researcher.py`, T19.2) calls these and never imports persistence.

Drift normalization handled here (audit §4):
    D-2: spec §8.4.1 step 1 calls similarity_search with limit=15,
         min_impact_level=2, min_reliability=0.3. The persistence default for
         `limit` is 10 and the filters are unset by default — wrappers forward
         spec values explicitly so Sprint 19 callers pass them as args, not
         relying on changeable persistence defaults.
    D-4: spec uses `source_platforms` (plural list, consistent with social).
         knowledge_vectors only supports a single platform pre-filter; the
         wrapper accepts a list, unpacks length-1, and rejects length-N (>1)
         with NotImplementedError. Sprint 19 callers pass None.

Return contract: rows always carry the key `similarity` (knowledge_vectors
already does this — kept aligned with social_tools after its rename).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.1
    - data-pipeline/docs/sprint19_persistence_audit.md §3.1, §6
"""

from __future__ import annotations

from typing import Optional

from persistence import knowledge_vault, knowledge_vectors


def similarity_search(
    query_embedding: list[float],
    limit: int,
    min_impact_level: Optional[int] = None,
    min_reliability: Optional[float] = None,
    source_platforms: Optional[list[str]] = None,
) -> list[dict]:
    """
    Run an HNSW similarity search against `knowledge_vectors`.

    Args:
        query_embedding:  1536-dim float list. Embedding of the user's query.
        limit:            Top-k cap. Caller passes spec value explicitly (no
                          wrapper default — see D-2 in audit §4).
        min_impact_level: Minimum impact_level (1-5). None disables.
        min_reliability:  Minimum reliability_score (0.0-1.0). None disables.
        source_platforms: Restrict to one platform via a length-1 list.
                          None means no platform filter (Sprint 19 default).
                          Length>1 raises NotImplementedError because
                          knowledge_vectors HNSW pre-filter is single-platform.

    Returns:
        List of row dicts ordered by similarity DESC. Each row carries:
        signal_id, canonical_event_id, source_platform, entry_type,
        published_at, content_vitals, enrichment_ai, domain_context,
        and `similarity` (1.0 = identical, 0.0 = orthogonal).
    """
    source_platform: Optional[str] = None
    if source_platforms is not None:
        if len(source_platforms) > 1:
            raise NotImplementedError(
                "knowledge_tools.similarity_search supports at most one "
                "source_platform per query (knowledge_vectors HNSW pre-filter "
                f"is single-platform). Got: {source_platforms!r}"
            )
        if len(source_platforms) == 1:
            source_platform = source_platforms[0]

    return knowledge_vectors.similarity_search(
        query_vector=query_embedding,
        limit=limit,
        source_platform=source_platform,
        min_impact_level=min_impact_level,
        min_reliability=min_reliability,
    )


def fetch_full_text(doc_id: str) -> Optional[dict]:
    """
    Drill-down: retrieve the full Silver document for a doc_id.

    Spec §8.4.1 step 3: after similarity_search returns the top-k Gold
    signals, the Researcher takes the top-5 `silver_data_ref` UUIDs (which
    are doc_ids) and calls this wrapper to load the original article body.

    Args:
        doc_id: UUID string from a `knowledge_vectors` row's `silver_data_ref`.

    Returns:
        Full row from `knowledge_vault` (doc_id, document_hash,
        canonical_event_id, source_name, author, original_url, publish_date,
        full_text_raw, inverted_pyramid_lead, detected_entities,
        relevance_score, sniper_keywords, ingested_at), or None if not found.
    """
    return knowledge_vault.fetch_by_doc_id(doc_id)
