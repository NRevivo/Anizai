"""
Evidence projection — the E5 contract.

Turns a SessionResult document plus its evidence subcollection into the
compact JSONB blob stored on `calibration_forecasts.agent_evidence_summary`.

Counts and metadata only. No raw text, ever. Two reasons, and the second is
the important one:

    1. Firestore stays the source of truth for evidence text; duplicating it
       into Postgres would create two copies that drift.

    2. The projection has to survive agent-version changes. A shape built from
       counts and booleans keeps meaning across a §8.7.2 schema addition; one
       built from text snippets breaks the moment the agent rewords anything,
       and breaks *silently* — the column still holds strings, they just stop
       being comparable across versions. Source-contribution analysis in Phase
       10C compares these projections across months of agent versions, so a
       silent break there would corrupt exactly the metric the projection
       exists to feed.

`projection_version` is stamped on every payload so a future shape change is
detectable and back-projectable rather than a mystery.

References:
    - calibration_plan.md §3 E5 (the projection contract)
    - calibration_plan.md §7 T10B.3
"""

from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

logger = logging.getLogger(__name__)

PROJECTION_VERSION = "1.0"

# The vault types the hub's three retrieval agents draw on, plus the reactive
# path. Fixed list rather than "whatever appeared" so a vault contributing
# nothing is reported as absent rather than omitted — Phase 10C's
# source-contribution metric compares present-vs-absent and needs both sides.
KNOWN_VAULT_TYPES: tuple[str, ...] = (
    "knowledge",
    "social",
    "momentum",
    "mapping",
    "reactive_search",
)

# Evidence `sourceType` values as the agent emits them, mapped onto the vault
# taxonomy above. Anything unrecognised is counted under its raw name in
# `evidence_count_by_source_type` but contributes to no vault flag — visible,
# but not silently folded into a bucket it may not belong in.
#
# The `vault_` prefix is what the live agent actually emits — verified against
# a real forecast on 2026-07-29 (`vault_news`, `vault_hackernews`). The first
# version of this table was written from the values the contract *implied* and
# matched none of them, so every forecast reported 15 pieces of evidence and
# zero contributing vaults simultaneously. That is the exact failure mode this
# system exists to catch: a number that is internally contradictory and still
# looks perfectly well-formed. `_strip_vault_prefix` now handles the prefix
# generally, so a future `vault_arxiv` maps without another edit here.
_SOURCE_TO_VAULT: dict[str, str] = {
    # knowledge vault — news, papers, long-form
    "news": "knowledge",
    "article": "knowledge",
    "knowledge": "knowledge",
    "arxiv": "knowledge",
    "hackernews": "knowledge",
    "hn": "knowledge",
    "expert": "knowledge",
    # social vault
    "social": "social",
    "telegram": "social",
    "reddit": "social",
    "googletrends": "social",
    # momentum vault — prediction-market prices
    "market": "momentum",
    "polymarket": "momentum",
    "momentum": "momentum",
    # mapping dictionary
    "mapping": "mapping",
    # reactive second pass
    "reactive": "reactive_search",
    "reactive_search": "reactive_search",
}

# Prefixes the agent has used on `sourceType`. Stripped before lookup so the
# table above stays a statement about *sources*, not about the naming
# convention of whichever agent version produced the row.
_SOURCE_PREFIXES: tuple[str, ...] = ("vault_", "source_", "evidence_")


def _norm(value: Any) -> str:
    return str(value).strip().lower() if value is not None else ""


def _strip_vault_prefix(source: str) -> str:
    """
    Remove a known naming prefix from a source type.

    Kept separate from `_SOURCE_TO_VAULT` deliberately. The agent's naming
    convention has already changed once without notice; absorbing that here
    means the next change costs one entry in `_SOURCE_PREFIXES` rather than a
    duplicate of every row in the table.
    """
    for prefix in _SOURCE_PREFIXES:
        if source.startswith(prefix):
            return source[len(prefix):]
    return source


def _source_type_of(doc: dict[str, Any]) -> str:
    """
    Extract an evidence document's source type.

    Tries several field names because the contract has shifted across agent
    versions and this projection must not be the thing that breaks when it
    shifts again. An evidence document with no recognisable source field is
    counted as "unknown" rather than dropped: losing it silently would make
    the total disagree with the per-type breakdown, and a total that does not
    equal the sum of its parts is worse than an ugly bucket name.
    """
    for key in ("sourceType", "source_type", "source", "type", "vaultType"):
        value = doc.get(key)
        if value:
            return _norm(value)
    return "unknown"


def project_evidence(
    session_result: Optional[dict[str, Any]],
    evidence_docs: Optional[Iterable[dict[str, Any]]],
) -> dict[str, Any]:
    """
    Build the `agent_evidence_summary` payload.

    Args:
        session_result: the SessionResult document, or None.
        evidence_docs:  the evidence subcollection, or None/empty.

    Returns:
        A JSON-serialisable dict. Never raises on missing or malformed input —
        a harvest must not fail because one evidence document lacked a field.
        A degraded projection with `evidence_count_total: 0` is a usable
        record; a failed harvest loses the forecast entirely.
    """
    result = session_result or {}
    docs = [d for d in (evidence_docs or []) if isinstance(d, dict)]

    by_source: dict[str, int] = {}
    vaults_present: set[str] = set()
    unmapped: set[str] = set()

    for doc in docs:
        source = _source_type_of(doc)
        # Counted under the name the agent actually used, so the breakdown
        # stays traceable back to the raw document.
        by_source[source] = by_source.get(source, 0) + 1

        vault = _SOURCE_TO_VAULT.get(_strip_vault_prefix(source))
        if vault:
            vaults_present.add(vault)
        elif source != "unknown":
            unmapped.add(source)

    reactive_count = sum(
        count
        for source, count in by_source.items()
        if _strip_vault_prefix(source) in ("reactive", "reactive_search")
    )
    if reactive_count:
        vaults_present.add("reactive_search")

    if unmapped:
        # The tripwire for the defect found on 2026-07-29: evidence counted,
        # no vault credited. Logged loudly rather than absorbed, because the
        # resulting payload is self-contradictory but perfectly well-formed —
        # nothing downstream would notice.
        logger.warning(
            "[projection] %d evidence document(s) carry source types this "
            "projection does not map: %s. They are counted in the breakdown "
            "but credited to no vault, so source-contribution will understate "
            "them. Add them to _SOURCE_TO_VAULT.",
            sum(by_source[s] for s in unmapped), sorted(unmapped),
        )

    return {
        "evidence_count_total": len(docs),
        "evidence_count_by_source_type": by_source,
        "reactive_search_used": reactive_count > 0,
        "reactive_search_count": reactive_count,
        "top_3_key_factor_titles": _key_factor_titles(result),
        "vault_types_present": sorted(vaults_present),
        "vault_types_absent": sorted(set(KNOWN_VAULT_TYPES) - vaults_present),
        # Carried in the payload, not just the log. A row that counted
        # evidence against no vault is a data-quality fact about that
        # forecast, and it has to travel with the row rather than live in a
        # log line nobody will read six weeks from now.
        "unmapped_source_types": sorted(unmapped),
        "projection_version": PROJECTION_VERSION,
    }


def _key_factor_titles(result: dict[str, Any], limit: int = 3) -> list[str]:
    """
    Pull up to `limit` key-factor titles from the SessionResult.

    Titles, not bodies: a short label is enough to eyeball on the dashboard
    and to notice when the agent's reasoning shifted, without turning this
    column into a second copy of the evidence text.
    """
    factors = result.get("keyFactors") or result.get("key_factors") or []
    if not isinstance(factors, list):
        return []

    titles: list[str] = []
    for factor in factors[:limit]:
        if isinstance(factor, str):
            titles.append(factor[:200])
        elif isinstance(factor, dict):
            title = (
                factor.get("title")
                or factor.get("label")
                or factor.get("factor")
                or factor.get("name")
            )
            if isinstance(title, str):
                titles.append(title[:200])
    return titles


# ==========================================================
# SessionResult field extraction
# ==========================================================

def extract_probability(result: Optional[dict[str, Any]]) -> Optional[float]:
    """
    Pull the final probability, normalised to the 0-1 contract.

    A value in (1, 100] is treated as a percentage and divided by 100. This is
    a judgement call worth stating: the alternative is to reject it, but a
    forecast row with a NULL probability is excluded from Brier entirely, so a
    unit mix-up upstream would quietly shrink the sample rather than announce
    itself. Converting and logging keeps the forecast and makes the anomaly
    visible. Values above 100 are rejected — those are not units, they are
    corruption.
    """
    if not result:
        return None

    for key in ("finalProbability", "probability", "final_probability"):
        raw = result.get(key)
        if raw is None:
            continue
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue

        if 0.0 <= value <= 1.0:
            return value
        if 1.0 < value <= 100.0:
            logger.warning(
                "[projection] %s=%s looks like a percentage — converting to %s. "
                "Check the agent's probability units.", key, value, value / 100.0,
            )
            return value / 100.0
        logger.error(
            "[projection] %s=%s is outside any plausible range — discarding.", key, value
        )
        return None
    return None


def extract_confidence(result: Optional[dict[str, Any]]) -> Optional[float]:
    """Pull confidence, same 0-1 normalisation as the probability."""
    if not result:
        return None
    for key in ("confidence", "confidenceScore", "confidence_score"):
        if key in result:
            return extract_probability({"probability": result[key]})
    return None


def extract_tier(result: Optional[dict[str, Any]]) -> Optional[str]:
    """
    Pull the tier, normalised to the `tier_1` / `tier_2` values the schema
    CHECK allows. Anything else returns None rather than failing the insert.
    """
    if not result:
        return None
    raw = _norm(result.get("tier") or result.get("tierLevel"))
    if not raw:
        return None
    if raw in {"tier_1", "tier1", "1", "tier 1"}:
        return "tier_1"
    if raw in {"tier_2", "tier2", "2", "tier 2"}:
        return "tier_2"
    logger.warning("[projection] Unrecognised tier %r — storing NULL", raw)
    return None


def extract_agent_version(result: Optional[dict[str, Any]]) -> Optional[str]:
    """
    Pull `agentVersion` — the attribution key for the improvement loop (F3).

    Recorded verbatim. It is never parsed or compared with `>=`: since Sprint
    26 it carries a git sha suffix (`0.5.0-sprint26+55e8093`), and ordering
    those as strings would be meaningless.
    """
    if not result:
        return None
    for key in ("agentVersion", "agent_version", "version"):
        value = result.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None
