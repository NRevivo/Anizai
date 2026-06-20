"""
agent/nodes/sufficiency_check.py — Node 4 of the forecast graph (Sprint 23.5, T23.5.1).

The orphaned-node defect (KG-B-15): the 2026-05-23 re-plan repurposed the
sprint that originally carried `sufficiency_check`, and the node was never
re-scheduled — Sprint 26 T26.7 then inherited the assumption it existed.
This module builds it as originally intended, sized for the V1 topology
locked for 23.5.

Responsibility (single — hub-principles G1): evaluate the three raw evidence
packages produced by `vault_query` against the user's `structured_intent`,
and append one verdict to `state.sufficiency_checks`. It decides nothing
about routing itself — `agent/graph.py._route_after_sufficiency` reads the
verdict and routes (agent-design: named routing functions, never hidden
in-node routing).

Deterministic V1 rubric (agent-design D2 / decision record §6 R4 — confirmed
deterministic, NO LLM, zero added cost/latency before the initial test):

    1. Signal floor: `count_raw_signals(state) >= AGENT_EVIDENCE_MIN_COUNT`.
       The count comes from the shared `count_raw_signals` helper so it can
       never drift from what `rate_evidence` will normalize (R2).
    2. Entity coverage: every `structured_intent.entities` term must appear
       in the raw evidence text. Uncovered entities become
       `missing_dimensions`.

    is_sufficient = (signal floor met) AND (no uncovered entities).

`structured_intent` carries `entities` but NOT a `dimensions` field (verified
against query_understand.py — only entities/intent/domain/polymarket_search_terms
exist). So "dimension coverage" is realized as **per-entity** evidence
coverage in V1 (decision record §1 / R1).

`missing_dimensions` is RECORDED but does NOT shape reactive keywords (R1):
    The verdict carries `missing_dimensions` as telemetry and as the seam
    for a future `vault_query_2` / LLM-refine path. It is deliberately NOT
    fed into the reactive trigger's `_build_keywords` — V1 reactive keywords
    are entities-only, bounded by the trigger's existing 7-day recency
    window. Rationale (recorded in the sprint plan §2): the gap reactive
    ingestion actually closes is **recency** — fresh, last-7-days articles
    the vault lacks — not topic. Silver's SHA-256 dedup makes any overlap
    with existing vault articles cheap/harmless, so entities-only + recency
    is correct and sufficient for V1. See trigger_reactive_ingestion._build_keywords.

V1 topology (decision record §6 R5, recorded in plan §6): a SINGLE
sufficiency_check followed by trigger-and-forget — there is NO second vault
query (`vault_query_2`) and NO refine loop in 23.5. The richer multi-attempt
rubric in the agent-design skill (vault_query_2 keyed off missing_dimensions,
2nd sufficiency check, reactive_search) is a later enhancement; V1's
`attempt` field is recorded for forward-compatibility but is always 1 in the
locked topology.

No agentEvents (decision record §6 R6): event emission is deferred to
Sprint 25; this node writes no `agentEvents`.

Service isolation (CLAUDE.md §3.3 / hub-principles): reads only state, calls
no LLM, touches no vault / Kafka / Postgres / Firestore. Pure, deterministic,
O(signals × entities). State-mediated I/O only (agent-design P2).

Spec references:
    - data-pipeline/docs/B_hub/sprint23_5_pre26_remediation.md §2 (Track 1, 23.5.1/2)
    - cabinet-outputs/advisor/problem-reports/sprint23_5_advisor-ron-decisions.md
      §1 (R1 entities-only + recency), §2 (R2 count helper), §6 (R4/R5/R6)
    - .claude/skills/agent-design/SKILL.md (sufficiency-check rubric, routing)
"""

from __future__ import annotations

import logging
from typing import Any

from agent.config import settings
from agent.utils.evidence_counting import count_raw_signals

logger = logging.getLogger(__name__)


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict) -> dict:
    """
    Execute Node 4 of the forecast graph.

    Args:
        state: Running ForecastState. Reads `structured_intent.entities`
               and the three raw evidence packages
               (`researcher_evidence`, `pulse_evidence`, `market_evidence`).
               Any may be absent/empty — read defensively.

    Returns:
        Partial state dict for LangGraph to merge:
            - `sufficiency_checks`: the prior list with one verdict appended.
              The verdict is
              `{is_sufficient: bool, missing_dimensions: list[str],
                reason: str, attempt: int}`.

        TypedDict `sufficiency_checks` has no list-reducer annotation, so the
        node reads the existing list and returns the whole appended list
        (read-append-write) rather than relying on a merge reducer.
    """
    structured_intent: dict = state.get("structured_intent") or {}
    entities = _clean_entities(structured_intent.get("entities"))

    signal_count = count_raw_signals(state)
    min_count = settings.AGENT_EVIDENCE_MIN_COUNT
    has_minimum_signals = signal_count >= min_count

    evidence_text = _build_evidence_text(state)
    missing_dimensions = [
        entity for entity in entities
        if entity.lower() not in evidence_text
    ]
    covers_named_entities = not missing_dimensions

    is_sufficient = has_minimum_signals and covers_named_entities

    reason = _build_reason(
        is_sufficient=is_sufficient,
        signal_count=signal_count,
        min_count=min_count,
        has_minimum_signals=has_minimum_signals,
        covers_named_entities=covers_named_entities,
        missing_dimensions=missing_dimensions,
    )

    prior_checks: list[dict] = list(state.get("sufficiency_checks") or [])
    attempt = len(prior_checks) + 1

    verdict = {
        "is_sufficient": is_sufficient,
        "missing_dimensions": missing_dimensions,
        "reason": reason,
        "attempt": attempt,
    }

    logger.info(
        "sufficiency_check: attempt=%d is_sufficient=%s signals=%d/%d "
        "entities=%d missing=%d session_id=%s",
        attempt, is_sufficient, signal_count, min_count,
        len(entities), len(missing_dimensions), state.get("session_id"),
    )

    return {"sufficiency_checks": prior_checks + [verdict]}


# ==========================================================
# Helpers
# ==========================================================
def _clean_entities(raw: Any) -> list[str]:
    """
    Normalize `structured_intent.entities` into a list of non-empty trimmed
    strings, deduped case-insensitively (first-occurrence case preserved).

    Mirrors the trigger node's keyword hygiene so coverage is measured
    against the same entity set the reactive trigger would key on — keeps
    the two halves of the reactive decision consistent.
    """
    if not isinstance(raw, list):
        return []
    seen: set[str] = set()
    out: list[str] = []
    for term in raw:
        if not isinstance(term, str):
            continue
        cleaned = term.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(cleaned)
    return out


def _build_evidence_text(state: dict) -> str:
    """
    Concatenate the searchable text of every raw signal into one lowercased
    blob, for substring-based entity-coverage matching.

    Pulls the same fields `rate_evidence`'s normalize_* helpers read, so
    coverage is judged against the text that will actually become evidence:
        researcher articles      → title + full_text_snippet + publisher
        pulse market_consensus    → market_id_ref + executive_summary
        pulse community_discussion→ title + top_technical_insights
        market fred_anomalies     → indicator_name + series_id + anomaly_flags
    """
    parts: list[str] = []

    researcher = state.get("researcher_evidence") or {}
    if not researcher.get("empty"):
        for article in researcher.get("articles") or []:
            parts.append(str(article.get("title") or ""))
            parts.append(str(article.get("full_text_snippet") or ""))
            parts.append(str(article.get("publisher") or ""))

    pulse = state.get("pulse_evidence") or {}
    if not pulse.get("empty"):
        for row in pulse.get("market_consensus") or []:
            parts.append(str(row.get("market_id_ref") or ""))
            parts.append(str(row.get("executive_summary") or ""))
        for row in pulse.get("community_discussion") or []:
            parts.append(str(row.get("title") or ""))
            insights = row.get("top_technical_insights")
            if isinstance(insights, list):
                parts.extend(str(i) for i in insights)

    market = state.get("market_evidence") or {}
    if not market.get("empty"):
        for anomaly in market.get("fred_anomalies") or []:
            parts.append(str(anomaly.get("indicator_name") or ""))
            parts.append(str(anomaly.get("series_id") or ""))
            flags = anomaly.get("anomaly_flags")
            if isinstance(flags, list):
                parts.extend(str(f) for f in flags)

    return " ".join(parts).lower()


def _build_reason(
    *,
    is_sufficient: bool,
    signal_count: int,
    min_count: int,
    has_minimum_signals: bool,
    covers_named_entities: bool,
    missing_dimensions: list[str],
) -> str:
    """
    Build a short human-readable justification for the verdict (logged + kept
    in the verdict dict for debugging during the initial test).
    """
    if is_sufficient:
        return (
            f"sufficient: {signal_count} signals (>= floor {min_count}) and "
            f"all named entities covered"
        )
    parts: list[str] = []
    if not has_minimum_signals:
        parts.append(
            f"only {signal_count} signals (< floor {min_count})"
        )
    if not covers_named_entities:
        parts.append(
            "uncovered entities: " + ", ".join(missing_dimensions)
        )
    return "insufficient: " + "; ".join(parts)
