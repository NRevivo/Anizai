"""
agent/nodes/query_understand.py — Node 1 of the forecast graph (T19.5).

Parses the user's `raw_question` into structured intent via an OpenAI
structured-output call. Decides whether to auto-pick the rank-1
interpretation or surface candidates 1-3 to the user for clarification.

Algorithm (spec §8.3.2 + §8.3.3):
    1. Render system prompt (`agent/prompts/query_understanding.SYSTEM_PROMPT`)
       and the strict JSON schema.
    2. Call OpenAI with `response_format={"type": "json_schema", ...,
       "strict": true}`. Structured output mode guarantees a parsable
       response or an API-level error (no malformed-JSON branch needed).
    3. If candidate 1 has `rejected == True` → raise AgentProcessingError
       (spec §8.3.3 line 294: "writing a `failed` status with explanation").
    4. If candidate 1 has `too_broad == True` → set `awaiting_clarification`
       and surface candidates (spec §8.3.3 line 293).
    5. If `rank_1.confidence >= AUTO_PICK_THRESHOLD` AND
       `rank_1.confidence - rank_2.confidence >= MARGIN_THRESHOLD` (or only
       one candidate exists) → auto-pick rank 1.
    6. Otherwise → set `awaiting_clarification = True`, write all candidates
       to `clarification_candidates`.

Returns a partial state dict that LangGraph merges into ForecastState.

Why injectable client (OQ-9):
    The OpenAI client is a constructor parameter so tests can pass a Mock
    without monkey-patching the SDK. Production callers omit it and
    `_get_default_client()` returns a singleton.

Service isolation (CLAUDE.md §3.3):
    This module talks to the OpenAI SDK directly — that's the node's job.
    It does NOT reach into `persistence/` or call other nodes. Vault reads
    happen in `vault_query` (Node 3); this node is text-in / structured-out.

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 1 output contract)
    - data-pipeline/docs/agentic_hub_spec.md §8.3.3 (ambiguous/rejected routing)
    - data-pipeline/docs/agentic_hub_spec.md §8.5.4 (clarification flow)
"""

from __future__ import annotations

import json
import logging
import uuid as _uuid_module
from typing import Any, Optional

from agent.config import settings
from agent.errors import AgentProcessingError
from agent.prompts.query_understanding import (
    MAX_CANDIDATES,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_message,
)

logger = logging.getLogger(__name__)


# ==========================================================
# Constants (OQ-2)
# ==========================================================
# rank-1 confidence floor for auto-pick. Below this, we always clarify even
# when rank 2 is far behind — we don't want to commit to a low-confidence
# parse just because it's the best of a weak field.
AUTO_PICK_THRESHOLD: float = 0.75

# rank-1 must beat rank-2 by this margin to auto-pick. Tight margins indicate
# the LLM couldn't separate two plausible interpretations — surface both.
# Spec §8.2.3: "top match's confidence within 0.10 of second-place → clarify".
# Sprint 19 used 0.15 (conservative); Sprint 21 aligns to spec per OQ-1.
MARGIN_THRESHOLD: float = 0.10

# Hard wall on the OpenAI call. Hub-level NFR (spec §8.7) gives 60s end-to-end;
# Node 1 is the cheapest LLM call so we cap it well below the budget.
TIMEOUT_S: float = 30.0

# Cap on completion tokens. The schema is small — 3 candidates × ~10 fields
# fits comfortably in 400 tokens with room for retries.
MAX_TOKENS: int = 400


# ==========================================================
# OpenAI client lifecycle
# ==========================================================
_default_client: Optional[Any] = None


def _get_default_client() -> Any:
    """
    Lazy-construct a module-level OpenAI client.

    Lazy because importing `openai` and reading the API key at module-load
    time would break test discovery in environments without OPENAI_API_KEY
    set. Tests inject their own client via the `client` kwarg and never
    hit this function.
    """
    global _default_client
    if _default_client is None:
        # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
        from utils.openai_client import get_openai_client

        _default_client = get_openai_client(
            api_key=settings.OPENAI_API_KEY,
            timeout=TIMEOUT_S,
        )
    return _default_client


# ==========================================================
# Public node function
# ==========================================================
def run(state: dict, *, client: Optional[Any] = None) -> dict:
    """
    Execute Node 1 of the forecast graph.

    Args:
        state:  Running ForecastState. Must contain `raw_question: str`.
        client: Optional injected OpenAI client (tests). Defaults to the
                module singleton.

    Returns:
        Partial state dict for LangGraph to merge. Always contains
        `structured_intent`, `awaiting_clarification`, and metadata
        counters; `clarification_candidates` and `clarification_needed`
        are present only on the ambiguous path.

    Raises:
        AgentProcessingError: when the rank-1 candidate has `rejected=True`
            (nonsensical / out-of-domain question — spec §8.3.3) or when
            the OpenAI call itself fails. process_query.py (T19.11) catches
            and writes `failed` to Firestore.
    """
    # Sprint 21 T21.5 — resume-on-clarify fast path.
    # When process_query pre-populates skip_matching_step=True and
    # structured_intent (T21.4), skip the OpenAI call entirely.
    # structured_intent is already in state; just signal "not ambiguous"
    # so the graph takes the build_embedding → forecast path.
    if state.get("skip_matching_step"):
        logger.info(
            "query_understand: skip_matching_step=True — resume-on-clarify path, "
            "using pre-populated structured_intent, no LLM call"
        )
        return {
            "awaiting_clarification": False,
            "clarification_needed": None,
            "clarification_candidates": None,
        }

    raw_question = state.get("raw_question") or ""
    if not raw_question.strip():
        raise AgentProcessingError("query_understand: empty raw_question in state")

    client = client or _get_default_client()

    response = _call_openai(client=client, question=raw_question)
    candidates = _extract_candidates(response)
    tokens_used = _extract_token_usage(response)

    rank_1 = candidates[0]

    # Hard rejection short-circuit (spec §8.3.3 line 294).
    if rank_1.get("rejected"):
        raise AgentProcessingError(
            f"query_understand: rejected question — {rank_1.get('intent', 'unknown')}"
        )

    auto_picked = _should_auto_pick(candidates)

    metadata_patch = {
        "llm_calls_count": int(state.get("llm_calls_count") or 0) + 1,
        "total_tokens_used": int(state.get("total_tokens_used") or 0) + tokens_used,
    }

    if auto_picked:
        return {
            "structured_intent": _strip_meta(rank_1),
            "awaiting_clarification": False,
            "clarification_needed": None,
            "clarification_candidates": None,
            **metadata_patch,
        }

    # Ambiguous path: shape candidates into ClarificationCandidate format
    # (spec §8.2.4, anizai_handoff_consolidated.md §6.3) and surface to user.
    # UUIDs assigned here are stable within the session — process_query uses
    # them to match chosenCandidateId on resume-on-clarify (T21.4).
    shaped_candidates = _build_clarification_candidates(candidates)

    # Guard: clarification only makes UX sense when ≥2 distinct choices exist.
    # A single candidate (e.g. low-confidence result from a broad question)
    # must fall through to auto-pick rather than surfacing a 1-item picker
    # that offers the user no real decision. Surfaced during T21.12 first run.
    if len(shaped_candidates) < 2:
        return {
            "structured_intent": _strip_meta(rank_1),
            "awaiting_clarification": False,
            "clarification_needed": None,
            "clarification_candidates": None,
            **metadata_patch,
        }

    return {
        "structured_intent": _strip_meta(rank_1),
        "awaiting_clarification": True,
        "clarification_needed": {"candidates": shaped_candidates},
        "clarification_candidates": shaped_candidates,
        **metadata_patch,
    }


# ==========================================================
# OpenAI plumbing
# ==========================================================
def _call_openai(*, client: Any, question: str) -> Any:
    """
    Invoke chat.completions with strict structured output.

    Wraps every SDK-level exception in AgentProcessingError so the caller
    only has to catch one type. `strict: true` on the JSON schema means
    we never need to handle malformed JSON — the API errors out before
    returning a non-conforming response.
    """
    try:
        return client.chat.completions.create(
            model=settings.OPENAI_MODEL_QUERY_UNDERSTANDING,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": build_user_message(question)},
            ],
            response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
            max_tokens=MAX_TOKENS,
            temperature=0.0,
        )
    except AgentProcessingError:
        raise
    except Exception as exc:
        raise AgentProcessingError(f"query_understand: OpenAI call failed — {exc!r}") from exc


def _extract_candidates(response: Any) -> list[dict]:
    """
    Parse the strict-mode JSON response into the list of candidates.

    The API guarantees the shape (because `strict: true`), but we still
    validate basic invariants — empty list, missing root key — to surface
    AgentProcessingError early instead of crashing downstream nodes with
    an IndexError.
    """
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise AgentProcessingError(
            f"query_understand: malformed OpenAI response shape — {exc!r}"
        ) from exc

    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentProcessingError(
            f"query_understand: response not JSON — {exc!r}"
        ) from exc

    candidates = parsed.get("candidates") or []
    if not candidates:
        raise AgentProcessingError("query_understand: no candidates returned")
    return candidates[:MAX_CANDIDATES]


def _extract_token_usage(response: Any) -> int:
    """
    Pull total_tokens off the response, defaulting to 0 if usage is absent.

    Cost tracking is best-effort here — the goal is to surface a non-zero
    counter for observability, not to be the source of truth. Sprint 25
    will add proper budget enforcement.
    """
    usage = getattr(response, "usage", None)
    if usage is None:
        return 0
    return int(getattr(usage, "total_tokens", 0) or 0)


# ==========================================================
# Routing logic
# ==========================================================
def _should_auto_pick(candidates: list[dict]) -> bool:
    """
    Return True when rank 1 is confidently best (auto-pick); False otherwise
    (route to clarification).

    Auto-pick requires BOTH:
    - rank_1.confidence >= AUTO_PICK_THRESHOLD
    - margin over rank 2 >= MARGIN_THRESHOLD

    A single-candidate response auto-picks when its confidence clears the
    threshold (no rank 2 to compare against).

    `too_broad=True` on rank 1 forces clarification regardless of confidence
    — the LLM is signaling that the question lacks a resolvable criterion.
    """
    rank_1 = candidates[0]

    if rank_1.get("too_broad"):
        return False

    rank_1_conf = float(rank_1.get("confidence") or 0.0)
    if rank_1_conf < AUTO_PICK_THRESHOLD:
        return False

    if len(candidates) == 1:
        return True

    rank_2_conf = float(candidates[1].get("confidence") or 0.0)
    return (rank_1_conf - rank_2_conf) >= MARGIN_THRESHOLD


def _strip_meta(candidate: dict) -> dict:
    """
    Return the candidate dict as-is (currently a no-op).

    Reserved for future use: if we add ranking-only fields to the candidate
    schema (e.g., `rank`, `reasoning`) that should NOT propagate into
    `structured_intent`, this is where they get filtered out.
    """
    return dict(candidate)


def _build_clarification_candidates(candidates: list[dict]) -> list[dict]:
    """
    Shape raw LLM candidate dicts into ClarificationCandidate format
    (spec §8.2.4, anizai_handoff_consolidated.md §6.3) for Firestore
    storage and frontend display.

    Each candidate gets a stable UUID4 `id` so process_query can match
    the user's `chosenCandidateId` back to the original candidate on
    resume-on-clarify (T21.4). The ID is stable within the session
    (assigned once here); reruns produce different UUIDs, which is
    acceptable — each clarification flow is its own session.

    Frontend-facing fields (ClarificationCandidate schema):
        id               UUID4, returned as chosenCandidateId by Express
        label            Human-readable from top 3 entities
        source           "polymarket" — only platform pre-Phase-7
        description      Derived from intent + search terms / entities
        matchConfidence  LLM self-rated confidence (proxy for vault
                         similarity; true vault resolution deferred per
                         KG-PHASE8-12).

    Hub-recovery fields (stored in Firestore, ignored by frontend,
    consumed by process_query on resume to rebuild structured_intent):
        intent, domain, entities, polymarket_search_terms

    Why no vault lookup here:
        A canonical Polymarket market ID requires the polymarket vector
        index, which doesn't exist pre-Phase-7 (KG-PHASE8-12). The LLM
        confidence is the available signal. Sprint 26 calibration can
        revisit if empirical issues surface.
    """
    result = []
    for candidate in candidates:
        entities: list[str] = list(candidate.get("entities") or [])
        search_terms: list[str] | None = candidate.get("polymarket_search_terms")
        intent: str = str(candidate.get("intent") or "forecast")
        domain: str = str(candidate.get("domain") or "general")
        confidence: float = float(candidate.get("confidence") or 0.0)

        label = ", ".join(entities[:3]) if entities else "Unknown market"

        terms_display = (
            ", ".join(search_terms[:3]) if search_terms else label
        )
        description = f"{intent.title()} question about {terms_display}"

        result.append({
            # Frontend-facing ClarificationCandidate fields (§8.2.4)
            "id": str(_uuid_module.uuid4()),
            "label": label,
            "source": "polymarket",
            "description": description,
            "matchConfidence": confidence,
            # Hub-recovery fields for T21.4 resume-on-clarify
            "intent": intent,
            "domain": domain,
            "entities": entities,
            "polymarket_search_terms": search_terms,
        })
    return result
