"""
agent/nodes/rate_evidence.py — Sprint 20 T20.1, between vault_query and synthesize.

Normalizes the three per-vault evidence packages (researcher, pulse,
market) into the unified `EvidenceItem[]` shape (spec §8.5.5), computes
deterministic recency weights, assigns per-platform credibility tiers,
then calls OpenAI in batches to set `relevance_score` + `justification`
per item. Writes the result to `state.evidence_trail` for `synthesize`
(T20.3) to consume.

Algorithm (spec §8.5.5 + evidence-handling skill):
    1. Normalize each evidence package's items into EvidenceItem-shaped
       dicts (one helper per source: researcher articles, pulse polymarket
       consensus, pulse hackernews discussion, market FRED anomalies).
    2. Compute `recency_weight` from `published_at` using the standard
       7-day-half-life exponential decay.
    3. Set `credibility_tier` from a per-platform mapping (vault items
       don't carry credibility metadata yet — Sprint 22+ will read from
       the source allowlist for non-vault items).
    4. Batch the items 8 at a time, call OpenAI with strict JSON schema
       to rate relevance, merge ratings back onto the items.
    5. Return `state.evidence_trail` as a list of fully-rated dicts that
       conform to the EvidenceItem shape.

Why the LLM call here (not deterministic-only):
    Per evidence-handling skill: "an article that's topically similar
    but doesn't address the question's specific angle should score
    lower." Cosine similarity from the vault is one signal but it's not
    enough — Reuters article that touches the topic without answering
    the question must score below 0.5. Only the LLM has the question
    context to make that judgment.

Why batches of 8:
    Per agent-prompt-engineering cost table (~$0.0005/call gpt-4o-mini),
    8 amortizes the system-prompt overhead while keeping each completion
    small. ~30-50 items / 8 = 4-7 calls × $0.0005 = ~$0.003 per session.
    Well within the Phase 8B success criterion (cost p50 within ±20% of
    $0.03 estimate).

Why fall back to fetched_at when published_at is missing:
    pulse_analyst.py and market_bridge.py do not currently surface
    `published_at` in their packed dicts (researcher.py does). This is
    a Sprint 19-era gap (KG-PHASE8-14 to be raised at Sprint 20 close).
    For Sprint 20, items without `published_at` get `recency_weight=1.0`
    via the fetched_at fallback. The synthesis prompt sees this as
    "fresh evidence" — a small upward bias on those items, acceptable
    until pulse + market_bridge packs are extended to emit published_at.

Why `relevance_score=0.0` + `justification="rating_unavailable"` on
batch failures:
    Per design D-error in the implementation plan: skip a failed batch,
    log a warning, mark items so the synthesis prompt can choose to
    exclude them (relevance_score < 0.5 → used_in_answer=False default).
    Avoids cascading a single OpenAI rate-limit into a session-level
    failure when the rest of the batches succeeded.

Service isolation (CLAUDE.md §3.3):
    Talks to the OpenAI SDK only. Does NOT reach into persistence
    directly — the vault has already been queried by Node 3
    (vault_query). State-mediated communication only (agent-design P2).

Spec references:
    - data-pipeline/docs/agentic_hub_spec.md §8.5.5 (EvidenceItem schema)
    - data-pipeline/docs/agentic_hub_spec_patch.md Patch 10 (source_type
      enum, Patch 7 graph topology — rate_evidence between vault_query
      and synthesize)
    - .claude/skills/evidence-handling/SKILL.md (rating semantics,
      credibility-tier rules, recency_weight standard)
    - .claude/skills/agent-prompt-engineering/SKILL.md (batched LLM
      calls, calibration anchors, justification copy rules)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

from agent.config import settings
from agent.errors import AgentProcessingError
from agent.utils import llm_cost
from agent.prompts.evidence_rating import (
    BATCH_SIZE,
    RESPONSE_SCHEMA,
    SYSTEM_PROMPT,
    build_user_message,
)
from agent.utils.recency import compute_recency_weight

logger = logging.getLogger(__name__)


# ==========================================================
# Constants
# ==========================================================

# Spec §8.5.5 + evidence-handling skill: snippet is "first ~200 chars".
# Researcher articles carry up to 500-char `full_text_snippet`; pulse
# items carry shorter `executive_summary`. Both get truncated here so
# the LLM sees consistent input shape across source types.
SNIPPET_MAX_CHARS: int = 200

# Half-life used for recency_weight. 7 days is the evidence-handling
# skill default; configurable per question type but Sprint 20 uses the
# default uniformly (per design D12).
RECENCY_HALF_LIFE_DAYS: int = 7

# Hard wall on each OpenAI call. Each call is small (1 system prompt + a
# batch of ~8 items + structured-output schema), so 30s is generous —
# this is mostly to avoid a stuck request hanging the worker.
TIMEOUT_S: float = 30.0

# Cap on completion tokens per batch. ~8 items × ~50 tokens of rating
# output each = ~400 tokens. 600 leaves room for verbose justifications.
MAX_TOKENS_PER_BATCH: int = 600

# Per-platform credibility-tier assignment. See module docstring +
# evidence-handling skill. Sprint 22+ will switch newsapi to a per-domain
# lookup once the source_allowlist.json file lands.
PLATFORM_TO_CREDIBILITY_TIER: dict[str, str] = {
    "newsapi": "tier_2",
    "arxiv": "tier_2",
    "telegram": "tier_3",
    "polymarket": "tier_2",
    "hackernews": "tier_3",
    "fred": "tier_1",
}

# Per-platform source_type assignment per spec §8.5.5 enum.
PLATFORM_TO_SOURCE_TYPE: dict[str, str] = {
    "newsapi": "vault_news",
    "arxiv": "vault_arxiv",
    "telegram": "vault_telegram",
    "polymarket": "vault_market",
    "hackernews": "vault_hackernews",
    "fred": "vault_fred",
}

# Source-domain fallbacks for items that don't carry a derivable URL.
PLATFORM_TO_SOURCE_DOMAIN: dict[str, str] = {
    "arxiv": "arxiv.org",
    "polymarket": "polymarket.com",
    "hackernews": "news.ycombinator.com",
    "fred": "fred.stlouisfed.org",
}


# ==========================================================
# OpenAI client lifecycle (matches Sprint 19 query_understand pattern)
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
    Execute the rate_evidence node.

    Args:
        state:  Running ForecastState. Must contain `raw_question: str`.
                Reads `researcher_evidence`, `pulse_evidence`,
                `market_evidence` (any may be None or empty).
        client: Optional injected OpenAI client (tests).

    Returns:
        Partial state dict for LangGraph to merge:
            - `evidence_trail`: list of fully-rated EvidenceItem dicts
            - `llm_calls_count`: incremented by number of batches called
            - `total_tokens_used`: incremented by sum of batch token usage

    Raises:
        AgentProcessingError: when raw_question is missing/empty. LLM
            call failures are caught at the per-batch level and degraded
            to `relevance_score=0.0` + `justification="rating_unavailable"`
            for affected items — they do NOT escape this node.
    """
    raw_question = state.get("raw_question") or ""
    if not raw_question.strip():
        raise AgentProcessingError(
            "rate_evidence: missing raw_question in state — "
            "claim_session should have populated it"
        )

    fetched_at = datetime.now(tz=timezone.utc)

    items: list[dict] = []
    items.extend(_normalize_researcher(state.get("researcher_evidence"), fetched_at))
    items.extend(_normalize_pulse(state.get("pulse_evidence"), fetched_at))
    items.extend(_normalize_market(state.get("market_evidence"), fetched_at))

    # Compute recency_weight on every item. Mutation in-place is fine —
    # these dicts are the rate_evidence node's product, not shared with
    # any upstream node.
    for item in items:
        item["recency_weight"] = compute_recency_weight(
            published_at=item["published_at"],
            half_life_days=RECENCY_HALF_LIFE_DAYS,
            now=fetched_at,
        )

    if not items:
        logger.info("rate_evidence: no evidence to rate (all packages empty)")
        return {
            "evidence_trail": [],
            "llm_calls_count": int(state.get("llm_calls_count") or 0),
            "total_tokens_used": int(state.get("total_tokens_used") or 0),
            "total_cost_usd": float(state.get("total_cost_usd") or 0.0),
        }

    client = client or _get_default_client()

    rated_items, calls_made, tokens_used, cost_usd = _rate_in_batches(
        client=client,
        question=raw_question,
        items=items,
    )

    logger.info(
        "rate_evidence: rated %d items across %d batches (tokens=%d cost_usd=%.6f)",
        len(rated_items), calls_made, tokens_used, cost_usd,
    )

    return {
        "evidence_trail": rated_items,
        "llm_calls_count": int(state.get("llm_calls_count") or 0) + calls_made,
        "total_tokens_used": int(state.get("total_tokens_used") or 0) + tokens_used,
        "total_cost_usd": float(state.get("total_cost_usd") or 0.0) + cost_usd,
    }


# ==========================================================
# Normalization — researcher
# ==========================================================
def _normalize_researcher(
    package: Optional[dict],
    fetched_at: datetime,
) -> list[dict]:
    """
    Convert a ResearcherEvidence package's `articles` into pre-rated
    EvidenceItem-shaped dicts.

    Each article carries `source_platform` ∈ {"newsapi", "arxiv",
    "telegram"}, `published_at` as ISO string, `title`, `full_text_snippet`
    (already capped at 500 chars by researcher.py — re-truncated here to
    SNIPPET_MAX_CHARS), and various enrichment fields not used here.
    """
    if not package or package.get("empty"):
        return []

    out: list[dict] = []
    for article in package.get("articles") or []:
        platform = article.get("source_platform") or ""
        source_type = PLATFORM_TO_SOURCE_TYPE.get(platform)
        if source_type is None:
            # Unknown vault platform — defensively skip rather than
            # producing an EvidenceItem with an out-of-enum source_type.
            logger.warning(
                "rate_evidence: dropping researcher article with unknown "
                "source_platform=%r", platform,
            )
            continue

        publisher = article.get("publisher") or ""
        out.append({
            "evidence_id": str(uuid.uuid4()),
            "source_type": source_type,
            "origin": "knowledge_vault",
            "title": article.get("title") or "",
            "snippet": (article.get("full_text_snippet") or "")[:SNIPPET_MAX_CHARS],
            "url": None,
            "source_domain": _resolve_domain_for_researcher(platform, publisher),
            "published_at": _parse_published_at(article.get("published_at"), fetched_at),
            "fetched_at": fetched_at,
            "credibility_tier": PLATFORM_TO_CREDIBILITY_TIER[platform],
        })
    return out


def _resolve_domain_for_researcher(platform: str, publisher: str) -> str:
    """
    Researcher's `publisher` field is per-platform:
      - newsapi → URL host (e.g. "reuters.com") — already a domain
      - arxiv   → "ArXiv" string — replace with arxiv.org
      - telegram → "@channel" string — replace with channel name as domain

    Falls back to PLATFORM_TO_SOURCE_DOMAIN when publisher is empty.
    """
    if platform == "newsapi":
        return publisher or "unknown.invalid"
    if platform == "arxiv":
        return PLATFORM_TO_SOURCE_DOMAIN["arxiv"]
    if platform == "telegram":
        # Channel handle like "@CoinDesk" → use as the domain string;
        # the frontend renders source_domain verbatim and Telegram has
        # no canonical web domain per channel.
        return publisher or "telegram"
    return PLATFORM_TO_SOURCE_DOMAIN.get(platform, "unknown.invalid")


# ==========================================================
# Normalization — pulse
# ==========================================================
def _normalize_pulse(
    package: Optional[dict],
    fetched_at: datetime,
) -> list[dict]:
    """
    Convert a PulseEvidence package's `market_consensus` (Polymarket) and
    `community_discussion` (HackerNews) lists into EvidenceItem-shaped
    dicts.

    Note: pulse_analyst.py does not currently surface `published_at` in
    its packed dicts (Sprint 19 limitation, see module docstring + the
    KG-PHASE8-14 entry to be raised at Sprint 20 close). All pulse items
    fall back to fetched_at → recency_weight=1.0.
    """
    if not package or package.get("empty"):
        return []

    out: list[dict] = []

    # Polymarket consensus → vault_market
    for item in package.get("market_consensus") or []:
        market_id_ref = item.get("market_id_ref") or ""
        out.append({
            "evidence_id": str(uuid.uuid4()),
            "source_type": "vault_market",
            "origin": "knowledge_vault",
            "title": _polymarket_title(market_id_ref, item.get("executive_summary") or ""),
            "snippet": (item.get("executive_summary") or "")[:SNIPPET_MAX_CHARS],
            "url": None,
            "source_domain": PLATFORM_TO_SOURCE_DOMAIN["polymarket"],
            "published_at": fetched_at,
            "fetched_at": fetched_at,
            "credibility_tier": PLATFORM_TO_CREDIBILITY_TIER["polymarket"],
        })

    # HackerNews community discussion → vault_hackernews
    for item in package.get("community_discussion") or []:
        out.append({
            "evidence_id": str(uuid.uuid4()),
            "source_type": "vault_hackernews",
            "origin": "knowledge_vault",
            "title": item.get("title") or "",
            "snippet": _hackernews_snippet(item),
            "url": None,
            "source_domain": PLATFORM_TO_SOURCE_DOMAIN["hackernews"],
            "published_at": fetched_at,
            "fetched_at": fetched_at,
            "credibility_tier": PLATFORM_TO_CREDIBILITY_TIER["hackernews"],
        })
    return out


def _polymarket_title(market_id_ref: str, executive_summary: str) -> str:
    """
    Build a presentable title for a polymarket consensus row.

    market_id_ref looks like "fed-rate-cut-q2-2026" or similar slugs.
    Title-casing keeps the model's input clean and gives synthesis a
    sensible default to render as the evidence headline.
    """
    if market_id_ref:
        return f"Polymarket consensus: {market_id_ref.replace('-', ' ').title()}"
    if executive_summary:
        return executive_summary[:80]
    return "Polymarket consensus"


def _hackernews_snippet(item: dict) -> str:
    """
    HackerNews items pack `top_technical_insights` (list of strings) but
    no narrative summary. Join up to 2 insights into a readable snippet
    so the rating LLM has substance to evaluate.
    """
    insights = item.get("top_technical_insights") or []
    if isinstance(insights, list) and insights:
        joined = " | ".join(str(i) for i in insights[:2])
        return joined[:SNIPPET_MAX_CHARS]
    return ""


# ==========================================================
# Normalization — market_bridge
# ==========================================================
def _normalize_market(
    package: Optional[dict],
    fetched_at: datetime,
) -> list[dict]:
    """
    Convert a MarketEvidence package's `fred_anomalies` into
    EvidenceItem-shaped dicts.

    For Sprint 20 only FRED anomalies become evidence items —
    `polymarket` is the market anchor itself (rendered separately in
    SessionResult.marketProbability/marketComparison, not as supporting
    evidence), `linked_sources` are typically empty pre-Phase-7,
    `google_trends` doesn't fit the §8.5.5 source_type enum (no
    "vault_trends" value).

    FRED anomalies don't carry `published_at` in the packed dict
    (market_bridge limitation, see KG-PHASE8-14). Falls back to
    fetched_at → recency_weight=1.0.
    """
    if not package or package.get("empty"):
        return []

    out: list[dict] = []
    for anomaly in package.get("fred_anomalies") or []:
        indicator = anomaly.get("indicator_name") or anomaly.get("series_id") or ""
        flags = anomaly.get("anomaly_flags") or []
        flag_text = ", ".join(str(f) for f in flags) if isinstance(flags, list) else ""
        out.append({
            "evidence_id": str(uuid.uuid4()),
            "source_type": "vault_fred",
            "origin": "knowledge_vault",
            "title": f"FRED anomaly: {indicator}" if indicator else "FRED anomaly",
            "snippet": _fred_snippet(anomaly, flag_text)[:SNIPPET_MAX_CHARS],
            "url": None,
            "source_domain": PLATFORM_TO_SOURCE_DOMAIN["fred"],
            "published_at": fetched_at,
            "fetched_at": fetched_at,
            "credibility_tier": PLATFORM_TO_CREDIBILITY_TIER["fred"],
        })
    return out


def _fred_snippet(anomaly: dict, flag_text: str) -> str:
    current = anomaly.get("current_value")
    change_7d = anomaly.get("change_7d")
    parts: list[str] = []
    if current is not None:
        parts.append(f"current={current}")
    if change_7d is not None:
        parts.append(f"7d change={change_7d}")
    if flag_text:
        parts.append(f"flags=[{flag_text}]")
    return "; ".join(parts) if parts else ""


# ==========================================================
# Helpers — published_at parsing
# ==========================================================
def _parse_published_at(
    raw: Any,
    fallback: datetime,
) -> datetime:
    """
    Parse an ISO-8601 string (researcher's pack) or datetime back into a
    timezone-aware datetime. Falls back to `fallback` (typically
    fetched_at) on missing or unparseable values.

    Why timezone-aware required: `compute_recency_weight` does
    `now - published_at`, and naive vs aware datetimes can't subtract
    without raising. fetched_at is always tz-aware (datetime.now(tz=UTC)),
    so the fallback path is safe.
    """
    if isinstance(raw, datetime):
        return raw if raw.tzinfo is not None else raw.replace(tzinfo=timezone.utc)
    if isinstance(raw, str) and raw:
        try:
            parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
            return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=timezone.utc)
        except ValueError:
            pass
    return fallback


# ==========================================================
# Batched LLM rating
# ==========================================================
def _rate_in_batches(
    *,
    client: Any,
    question: str,
    items: list[dict],
) -> tuple[list[dict], int, int, float]:
    """
    Rate every item, BATCH_SIZE at a time. Returns the items with
    `relevance_score` + `justification` filled in, plus the count of
    successful API calls made, the total tokens consumed, and the total USD
    cost (summed across batches via llm_cost — T23.5.10).

    Items in a failed batch get `relevance_score=0.0` and
    `justification="rating_unavailable"` so synthesis can choose to
    exclude them via the relevance threshold (used_in_answer < 0.5
    convention). The batch counts toward `calls_made` only on success
    so observability metrics reflect actual API usage.
    """
    calls_made = 0
    tokens_used = 0
    cost_usd = 0.0

    for batch_start in range(0, len(items), BATCH_SIZE):
        batch = items[batch_start: batch_start + BATCH_SIZE]
        try:
            response = _call_openai(client=client, question=question, batch=batch)
            ratings = _extract_ratings(response)
            batch_tokens, batch_cost = llm_cost.record_usage(
                settings.OPENAI_MODEL_EVIDENCE_RATING, response,
                site="rate_evidence",
            )
            tokens_used += batch_tokens
            cost_usd += batch_cost
            calls_made += 1
            _apply_ratings(batch, ratings)
        except AgentProcessingError as exc:
            # Per-batch graceful degradation — log and mark items so
            # synthesis can route around them.
            logger.warning(
                "rate_evidence: batch %d-%d failed (%s); marking items "
                "with relevance_score=0.0",
                batch_start, batch_start + len(batch), exc,
            )
            _mark_unavailable(batch)

    return items, calls_made, tokens_used, cost_usd


def _call_openai(*, client: Any, question: str, batch: list[dict]) -> Any:
    """
    Invoke chat.completions with strict structured output for one batch.

    Wraps every SDK-level exception in AgentProcessingError so the
    caller only has to catch one type. `strict: true` on the JSON
    schema means we never need to handle malformed JSON — the API
    errors out before returning a non-conforming response.
    """
    user_message = build_user_message(question=question, items=batch)
    try:
        return client.chat.completions.create(
            model=settings.OPENAI_MODEL_EVIDENCE_RATING,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            response_format={"type": "json_schema", "json_schema": RESPONSE_SCHEMA},
            max_tokens=MAX_TOKENS_PER_BATCH,
            temperature=0.0,
        )
    except AgentProcessingError:
        raise
    except Exception as exc:
        raise AgentProcessingError(
            f"rate_evidence: OpenAI call failed — {exc!r}"
        ) from exc


def _extract_ratings(response: Any) -> list[dict]:
    """
    Parse the strict-mode JSON response into the list of ratings.

    The API guarantees the shape (because `strict: true`), but we still
    validate basic invariants to surface AgentProcessingError early
    instead of crashing downstream nodes.
    """
    try:
        content = response.choices[0].message.content
    except (AttributeError, IndexError) as exc:
        raise AgentProcessingError(
            f"rate_evidence: malformed OpenAI response shape — {exc!r}"
        ) from exc

    try:
        parsed = json.loads(content)
    except (TypeError, json.JSONDecodeError) as exc:
        raise AgentProcessingError(
            f"rate_evidence: response not JSON — {exc!r}"
        ) from exc

    ratings = parsed.get("ratings") or []
    if not isinstance(ratings, list):
        raise AgentProcessingError(
            f"rate_evidence: 'ratings' is not a list — got {type(ratings).__name__}"
        )
    return ratings


def _apply_ratings(batch: list[dict], ratings: list[dict]) -> None:
    """
    Merge ratings back onto the batch items by `evidence_id`.

    The model is instructed to preserve order and use the exact
    evidence_id, but we still match by id (not index) so an
    out-of-order or partial response degrades gracefully — items not
    covered by the response get `rating_unavailable` (same path as
    a fully-failed batch).
    """
    by_id = {r.get("evidence_id"): r for r in ratings if r.get("evidence_id")}
    for item in batch:
        rating = by_id.get(item["evidence_id"])
        if rating is None:
            item["relevance_score"] = 0.0
            item["justification"] = "rating_unavailable"
            logger.warning(
                "rate_evidence: item %s missing from batch response",
                item["evidence_id"],
            )
            continue
        item["relevance_score"] = float(rating.get("relevance_score") or 0.0)
        item["justification"] = str(rating.get("justification") or "")


def _mark_unavailable(batch: list[dict]) -> None:
    """
    Tag every item in a failed batch so synthesis can route around them.

    Setting relevance_score=0.0 means the item lands below the
    used_in_answer threshold (0.5) automatically; setting the
    justification gives synthesis observable context for any reasoning
    chain it wants to write.
    """
    for item in batch:
        item["relevance_score"] = 0.0
        item["justification"] = "rating_unavailable"


# ==========================================================
# Helper — derive a domain from a URL (kept for future symmetry)
# ==========================================================
def _domain_from_url(url: str) -> str:
    """
    Parse a domain out of a URL. Currently unused by the four
    normalize_* helpers (they all use platform-derived domains), but
    kept here so reactive_search (Sprint 22+) can reuse the same logic
    for online_news / online_blog items.
    """
    if not url:
        return ""
    host = urlparse(url).netloc
    if host.startswith("www."):
        host = host[4:]
    return host
