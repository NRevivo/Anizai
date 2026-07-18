"""
agent/tools/mapping_tools.py — Market Bridge's cross-platform linkage tool
(Sprint 19 §8.4.3 step 2).

Wraps `persistence/mapping_dict.py`. The Market Bridge agent
(`agent/agents/market_bridge.py`, T19.4) calls this and never imports
persistence.

Sprint 19 uses `lookup_by_canonical` only. `find_similar_and_link` is the
Phase-7 Gold-job integration path (audit §4 D-5) — out of scope until the
mapping_dict population strategy lands.

Empty `linked_sources` is the expected default for most pre-Phase-7
canonical_event_ids; the Market Bridge tests both populated and empty cases.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.4.3 step 2
    - data-pipeline/docs/sprint19_persistence_audit.md §3.3, §4 D-5
"""

from __future__ import annotations

from typing import Optional

from agent.tools._retry import vault_read_retry
from persistence import mapping_dict


def lookup_by_canonical(
    canonical_event_id: str,
    platform: Optional[str] = None,
    limit: int = 100,
) -> list[dict]:
    """
    All platform IDs linked to a canonical_event_id.

    Args:
        canonical_event_id: The shared event key.
        platform:           Optional platform filter (e.g. 'polymarket').
        limit:              Maximum rows returned (default 100).

    Returns:
        List of row dicts (mapping_id, canonical_event_id, platform,
        platform_specific_id, similarity_score, created_at, notes), ordered
        by created_at DESC. Empty list if no linkages exist for this
        canonical_event_id (the common pre-Phase-7 case).
    """
    return vault_read_retry(
        mapping_dict.lookup_by_canonical,
        canonical_event_id=canonical_event_id,
        platform=platform,
        limit=limit,
        op_name="mapping.lookup_by_canonical",
    )
