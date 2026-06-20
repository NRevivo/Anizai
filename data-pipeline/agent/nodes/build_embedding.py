"""
agent/nodes/build_embedding.py — Node 2 of the forecast graph (T19.7).

Embeds the user's `raw_question` into a 1536-dimensional vector via OpenAI's
`text-embedding-3-small`. The vector is consumed downstream by `vault_query`
(Node 3) for HNSW similarity search across knowledge_vectors and
social_vectors.

Why a separate node (not inline in vault_query):
    Three retrieval agents (Researcher, Pulse Analyst, Market Bridge) all
    share the same query embedding. Computing it once in a dedicated node
    and stashing in state avoids three duplicate API calls per session.

D3 — in-memory state cache only:
    The embedding lives in `state["query_embedding"]` for the duration of
    the graph run. We do NOT persist embeddings to disk or Firestore in
    Sprint 19. A persistent cache (keyed by question hash) is a Sprint 25+
    optimization; until then, follow-up sessions re-embed.

Strict dimension assertion (OQ-3):
    The HNSW indexes on knowledge_vectors and social_vectors are built for
    1536-dim. If `OPENAI_EMBEDDING_MODEL` is ever swapped to a model with a
    different dimension (e.g., `text-embedding-3-large` returns 3072), the
    similarity search would fail at the database layer with a confusing
    error. The cheap len() check here surfaces the misconfiguration as a
    clear AgentProcessingError at the right layer.

Service isolation (CLAUDE.md §3.3):
    Talks only to the OpenAI SDK. Vault reads happen in Node 3 (vault_query).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (Node 2 in the topology)
    - data-pipeline/docs/agentic_hub_spec.md §8.4.1 (1536-dim embedding model)
"""

from __future__ import annotations

import logging
from typing import Any, Optional

from agent.config import settings
from agent.errors import AgentProcessingError
from agent.utils import llm_cost

logger = logging.getLogger(__name__)


# ==========================================================
# Constants
# ==========================================================
# HNSW index dimension — must match the column type of
# knowledge_vectors.embedding and social_vectors.embedding (vector(1536)).
EXPECTED_DIM: int = 1536

# Hard wall on the embedding API call. Matches Node 1's budget.
TIMEOUT_S: float = 30.0


# ==========================================================
# OpenAI client lifecycle
# ==========================================================
_default_client: Optional[Any] = None


def _get_default_client() -> Any:
    """
    Lazy-construct a module-level OpenAI client.

    Lazy because importing `openai` at module-load time would break test
    discovery in environments without OPENAI_API_KEY set. Tests inject
    their own client via the `client` kwarg and never hit this function.
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
    Execute Node 2 of the forecast graph.

    Args:
        state:  Running ForecastState. Must contain `raw_question: str`.
        client: Optional injected OpenAI client (tests). Defaults to the
                module singleton.

    Returns:
        Partial state dict containing `query_embedding` and an updated
        `total_tokens_used` accumulator.

    Raises:
        AgentProcessingError: when raw_question is empty, the OpenAI call
            fails, or the returned embedding has the wrong dimensionality.
    """
    raw_question = state.get("raw_question") or ""
    if not raw_question.strip():
        raise AgentProcessingError("build_embedding: empty raw_question in state")

    client = client or _get_default_client()

    response = _call_openai(client=client, question=raw_question)
    embedding = _extract_embedding(response)
    # Embedding calls have prompt tokens only; compute_cost gets completion=0
    # via record_usage → extract_usage (embedding responses carry no
    # completion_tokens), so the cost is input-priced (T23.5.10, R8).
    tokens_used, cost_usd = llm_cost.record_usage(
        settings.OPENAI_EMBEDDING_MODEL, response, site="build_embedding",
    )

    return {
        "query_embedding": embedding,
        "total_tokens_used": int(state.get("total_tokens_used") or 0) + tokens_used,
        "total_cost_usd": float(state.get("total_cost_usd") or 0.0) + cost_usd,
    }


# ==========================================================
# OpenAI plumbing
# ==========================================================
def _call_openai(*, client: Any, question: str) -> Any:
    """
    Invoke embeddings.create with the configured model.

    Wraps every SDK-level exception in AgentProcessingError so the caller
    only has to catch one type.
    """
    try:
        return client.embeddings.create(
            model=settings.OPENAI_EMBEDDING_MODEL,
            input=question,
        )
    except AgentProcessingError:
        raise
    except Exception as exc:
        raise AgentProcessingError(
            f"build_embedding: OpenAI call failed — {exc!r}"
        ) from exc


def _extract_embedding(response: Any) -> list[float]:
    """
    Pull the 1536-dim embedding off the response and validate its shape.

    OpenAI returns `response.data[0].embedding` as a list[float]. The
    dimension check (OQ-3) catches model misconfiguration that would
    otherwise fail silently at the HNSW layer downstream.
    """
    try:
        embedding = response.data[0].embedding
    except (AttributeError, IndexError) as exc:
        raise AgentProcessingError(
            f"build_embedding: malformed OpenAI response shape — {exc!r}"
        ) from exc

    if not isinstance(embedding, list):
        raise AgentProcessingError(
            f"build_embedding: expected list[float], got {type(embedding).__name__}"
        )

    if len(embedding) != EXPECTED_DIM:
        raise AgentProcessingError(
            f"build_embedding: expected {EXPECTED_DIM}-dim embedding, "
            f"got {len(embedding)}-dim — check OPENAI_EMBEDDING_MODEL "
            f"(currently {settings.OPENAI_EMBEDDING_MODEL!r})"
        )

    return embedding
