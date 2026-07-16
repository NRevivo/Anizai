"""
agent/nodes/vault_query.py — Node 3 of the forecast graph (T19.8).

Dispatches the three retrieval agents (Researcher, Pulse Analyst, Market
Bridge) in parallel using a thread pool. Gathers their evidence packages
into state, increments `vault_query_attempts`, and propagates exceptions
fail-fast (no per-agent isolation in Sprint 19 — see OQ-3).

Why a thread pool:
    The retrieval agents do blocking psycopg2 I/O. Threads release the GIL
    during DB calls, so three workers achieve real parallelism. asyncio
    would require rewriting the agents' DB layer (asyncpg) — out of scope
    for Sprint 19. Sequential dispatch would 3× the retrieval latency.

Why fail-fast on agent error:
    Sprint 19 deliberately keeps the error model simple — a single
    `AGENT_PROCESSING_ERROR` code (T19.5). Per-agent partial failure with
    silent evidence drop is the worst kind of bug for a forecasting agent:
    the user sees a confident answer with a third of the evidence missing.
    Sprint 26 T26.1 introduces the full taxonomy (timeout vs. rate-limit
    vs. vault-down) at which point per-agent isolation becomes meaningful.

Why Market Bridge runs with `polymarket_slug=None` always (Sprint 19 only):
    No polymarket-market vector index exists in `persistence/`. The only
    path to a slug is `mapping_dict.lookup_by_canonical(canonical_event_id,
    platform="polymarket")`, which requires `canonical_event_id` populated
    by Phase 7 — sparse pre-Phase 7 (Sprint 19 D4). Tracked formally as
    KG-PHASE8-12; the resolver lands in Sprint 21+ T21.6.

    `structured_intent["polymarket_search_terms"]` (T19.5 OQ-3 output) is
    wired through state but unused by this node. Market Bridge runs in
    Tier 2 mode (`polymarket: None`); its FRED + Trends + entities slices
    populate normally.

Service isolation (CLAUDE.md §3.3):
    Talks only to the three retrieval agents (which talk to `agent/tools/`
    wrappers, which talk to `persistence/`). This module never touches
    persistence directly.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 3 in topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.4.1, §8.4.2, §8.4.3
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, TimeoutError as FuturesTimeoutError
from datetime import datetime
from typing import Optional

from agent import events
from agent.agents import market_bridge, pulse_analyst, researcher
from agent.errors import AgentProcessingError

logger = logging.getLogger(__name__)


# ==========================================================
# Constants (OQ-1, OQ-4)
# ==========================================================
# Three workers — one per retrieval agent. Sized to match the static
# fan-out, not tuned per-load.
MAX_WORKERS: int = 3

# Per-agent timeout. Each retrieval agent does ~10 DB queries (similarity
# search + drill-downs). 15s gives plenty of headroom on healthy Postgres
# while staying well under the hub-level 60s NFR (spec §8.7).
PER_AGENT_TIMEOUT_S: float = 15.0


# ==========================================================
# Public node function
# ==========================================================
@events.emits("vault_query", "Gathering evidence…")
def run(state: dict, *, now: Optional[datetime] = None) -> dict:
    """
    Execute Node 3 of the forecast graph.

    Args:
        state: Running ForecastState. Must contain:
                - `query_embedding: list[float]` (Researcher + Pulse Analyst)
                - `structured_intent: dict` (entities → Market Bridge)
        now:   Reference "now" forwarded to all three agents for
               deterministic recency scoring. Defaults to UTC now inside
               each agent.

    Returns:
        Partial state dict containing `researcher_evidence`,
        `pulse_evidence`, `market_evidence`, and an incremented
        `vault_query_attempts`.

    Raises:
        AgentProcessingError: when any agent throws or times out, when
            required state fields are missing.
    """
    query_embedding = state.get("query_embedding")
    if not query_embedding:
        raise AgentProcessingError("vault_query: missing query_embedding in state")

    structured_intent = state.get("structured_intent") or {}
    entities = structured_intent.get("entities") or []

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        future_researcher = pool.submit(researcher.run, query_embedding, now=now)
        future_pulse = pool.submit(pulse_analyst.run, query_embedding, now=now)
        future_market = pool.submit(
            market_bridge.run,
            polymarket_slug=None,        # forward-compat: QU auto-pick (Future Enhancement 4)
            canonical_event_id=None,     # ditto — populated by Phase 7
            entities=entities,
            now=now,
            # Sprint 22 T22.2: pg_trgm fuzzy match against the user's
            # raw question replaces the deferred vector resolver. Gated
            # on QU's has_market_question_intent so Tier 2 questions
            # don't pay for the DB round-trip.
            raw_question=state.get("raw_question") or "",
            has_market_question_intent=bool(
                structured_intent.get("has_market_question_intent")
            ),
        )

        researcher_evidence = _await(future_researcher, agent_name="researcher")
        pulse_evidence = _await(future_pulse, agent_name="pulse_analyst")
        market_evidence = _await(future_market, agent_name="market_bridge")

    return {
        "researcher_evidence": researcher_evidence,
        "pulse_evidence": pulse_evidence,
        "market_evidence": market_evidence,
        "vault_query_attempts": int(state.get("vault_query_attempts") or 0) + 1,
    }


# ==========================================================
# Private helpers
# ==========================================================
def _await(future, *, agent_name: str) -> dict:
    """
    Block on a future with the per-agent timeout, wrapping any exception
    in AgentProcessingError so the caller has one type to catch.

    Both `TimeoutError` (the agent ran but exceeded budget) and any other
    exception (DB error, validation failure, etc.) collapse to the same
    user-visible code in Sprint 19 — the taxonomy split lands in T26.1.
    """
    try:
        return future.result(timeout=PER_AGENT_TIMEOUT_S)
    except FuturesTimeoutError as exc:
        raise AgentProcessingError(
            f"vault_query: {agent_name} timed out after {PER_AGENT_TIMEOUT_S}s"
        ) from exc
    except AgentProcessingError:
        raise
    except Exception as exc:
        raise AgentProcessingError(
            f"vault_query: {agent_name} failed — {exc!r}"
        ) from exc
