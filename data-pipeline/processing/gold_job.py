"""
Gold Job — Flink Semantic & Structural Enrichment (Section 4.2).

Three enrichment paths are implemented:

A. Social Pulse Path (Section 4.2A — Consensus Bundling):
   Consumes process.silver.social_pulse
   → GPT-4o Consensus Bundling (4-hour temporal groups)
   → Cognitive Metadata Extraction (impact_level, urgency_level, etc.)
   → text-embedding-3-small vector (1536-dim)
   → Gold Social Pulse record (Section C.6)
   → social_vectors table (via persistence/social_vectors.py)
   → serve.gold.social_pulse (Kafka)

B. Structured Metrics Path (Section 4.2B — Momentum Block):
   Consumes process.silver.structured_metrics
   → Flink keyed state → compute change_24h/7d/30d
   → Whale alert escalation (impact_level: 5 when whale_alert=True, Section B.8)
   → Gold Structured Metric (Section C.2 with populated momentum_block)
   → serve.gold.structured_metrics (Kafka)
   (momentum_vault persistence wired in Task 1.5)

C. Global News Path (Section 4.2A — Cognitive Metadata Extraction):
   Consumes process.silver.global_news (NewsAPI + ArXiv + Telegram)
   → is_high_signal guard (Section 4.1A — skips low-signal records, zero token spend)
   → GPT-4o Cognitive Metadata Extraction (shared COGNITIVE_METADATA_SYSTEM_PROMPT)
   → Impact Boost applied for NewsAPI only (Section B.4 — not for ArXiv B.1 or Telegram A.1)
   → text-embedding-3-small on executive_summary
   → Gold Global Signal record (Section C.5)
     NewsAPI  → domain_context: Tactical extension (sniper_keywords, is_breaking, share_count, geospatial_focus)
     ArXiv    → domain_context: Academic extension (authors, is_peer_reviewed, citation_count, domain_tags)
     Telegram → domain_context: Direct Message extension (channel_username, is_forwarded, forwarded_from, views, extracted_links)
   → knowledge_vectors table (via persistence/knowledge_vectors.py)
   → serve.gold.global_news (Kafka)

Architecture note — same pattern as silver_job.py:
    All transform functions are pure Python and have no PyFlink dependency.
    Flink wiring is isolated in class definitions and build_pipeline(),
    guarded by PYFLINK_AVAILABLE. Gate 2 tests call transform functions
    directly with mock OpenAI responses from tests/mocks/ (Section 9.3).

OpenAI models used:
    Consensus Bundling:  gpt-4o  (OPENAI_MODEL_NAME from settings, Section 4.2A)
    Vector Embedding:    text-embedding-3-small  (1536-dim, Section 5.2)

References:
    - Section 4.2:  Gold Job specification
    - Section 4.2A: Semantic Enrichment — Consensus Bundling
    - Section 4.2B: Structural Enrichment — Momentum Block
    - Section B.8:  Polymarket parameters (consensus, whale_alert)
    - Section C.4:  Silver-to-Gold transition
    - Section C.6:  Gold Social Pulse Schema
    - Section 5.2:  Vector Intelligence — pgvector with HNSW
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
"""

from __future__ import annotations

import copy
import json
import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from config.kafka_topics import (
    DEAD_LETTER_QUEUE,
    GOLD_GLOBAL_NEWS,
    GOLD_SOCIAL_PULSE,
    GOLD_STRUCTURED_METRICS,
    SILVER_GLOBAL_NEWS,
    SILVER_SOCIAL_PULSE,
    SILVER_STRUCTURED_METRICS,
)
from prompts.cognitive_metadata import COGNITIVE_METADATA_SYSTEM_PROMPT
from prompts.consensus_summary import CONSENSUS_SYSTEM_PROMPT
from utils.validators import (
    validate_gold_signal,
    validate_gold_social_pulse,
    validate_silver_document,
    validate_silver_social,
)

from utils.logging_config import setup_logging, set_trace_id
from utils.llm_cost import record_usage

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# LLM cost instrumentation helper (Phase 7B.5-I, T4)
# ==========================================================

def _cost_trace_id(record: dict) -> str:
    """
    Resolve the cost-event trace_id for a Silver record (plan §2.5).

    canonical_event_id is the cross-layer key; social records set it from
    bronze_ref at Gold-build time, so before that point bronze_ref IS the
    canonical id — same fallback chain the logging layer uses (set_trace_id
    call sites). Joins llm_cost_events rows to vault rows / filter_rejects
    for per-article unit economics.
    """
    return record.get("canonical_event_id") or record.get("bronze_ref", "")


# ==========================================================
# Deterministic signal_id helper (Gap 2 fix, Sprint 11)
# ==========================================================

def _deterministic_signal_id(content_hash: str) -> str:
    """
    Derive a stable UUID5 from a Silver content_hash (Section 4.1C).

    Why UUID5 instead of uuid4():
    social_vectors.insert() already has ON CONFLICT (signal_id) DO NOTHING,
    but the guard only fires when signal_id matches an existing row.
    uuid4() generates a fresh UUID on every call, so E2E reruns and Flink
    re-deliveries always produce new signal_ids and accumulate duplicate rows.

    UUID5 is deterministic: same content_hash → same signal_id → ON CONFLICT
    fires on re-delivery → exactly-one row per unique Silver batch in the
    Gold vector table.

    Falls back to uuid4() when content_hash is absent (should not happen for
    Polymarket/HackerNews Silver records, which always set content_hash via
    hash_social_batch()).

    Args:
        content_hash: 64-char SHA-256 hex string from hash_social_batch()
                      stored on the Silver social record.

    Returns:
        UUID string — deterministic when content_hash is non-empty,
        random otherwise.
    """
    if content_hash:
        return str(uuid.uuid5(uuid.NAMESPACE_X500, content_hash))
    return str(uuid.uuid4())


# ==========================================================
# PyFlink — optional import (Docker container only, Section 8.2)
# ==========================================================
# See silver_job.py for rationale on this guard pattern.

try:
    from pyflink.common import SimpleStringSchema, Types, WatermarkStrategy
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.connectors.kafka import (
        KafkaOffsetsInitializer,
        KafkaRecordSerializationSchema,
        KafkaSink,
        KafkaSource,
    )
    from pyflink.datastream.functions import KeyedProcessFunction, ProcessFunction
    PYFLINK_AVAILABLE = True
except ImportError:
    PYFLINK_AVAILABLE = False


# ==========================================================
# Constants
# ==========================================================

# Embedding model for Gold vector generation (Section 5.2)
EMBEDDING_MODEL = "text-embedding-3-small"
EMBEDDING_DIM   = 1536

# Whale alert impact override threshold (Section B.8)
WHALE_ALERT_IMPACT_LEVEL = 5

# Max price-history entries retained per asset in Flink keyed state.
# 105,120 = 365 days × 288 five-minute samples; capped at 10,000
# most-recent to bound per-key state size.
_MAX_HISTORY = 10_000

# OutputTag constant — side-routes failed records to DLQ inside Flink
DLQ_TAG = "dlq"

# System prompts for GPT-4o enrichment (Section 4.2A).
# Prompts live in prompts/ — imported above. See prompts/README.md for the full catalogue.
# CONSENSUS_SYSTEM_PROMPT     → prompts/consensus_summary.py   (Polymarket, HackerNews)
# COGNITIVE_METADATA_SYSTEM_PROMPT → prompts/cognitive_metadata.py (NewsAPI, ArXiv, Telegram)


# ==========================================================
# Consensus Bundling Helpers (Section 4.2A)
# ==========================================================

def build_consensus_prompt(silver_social: dict) -> str:
    """
    Build the GPT-4o user prompt for Polymarket Consensus Bundling.

    Formats the market question and comment texts into a numbered list.
    Numbered presentation helps GPT-4o weigh individual comment
    contributions without being distracted by JSON structure noise.

    Args:
        silver_social: Silver social record (Section C.3, Polymarket pattern).
                       Expected keys: question, raw_comments (list of dicts
                       with a 'text' key).

    Returns:
        Formatted user prompt string.
    """
    question = silver_social.get("question", "Unknown market question")
    comments = silver_social.get("raw_comments", [])

    comment_lines = "\n".join(
        f"{i + 1}. {c.get('text', '')}"
        for i, c in enumerate(comments)
    )
    return (
        f"Market question: {question}\n\n"
        f"Community comments ({len(comments)} total):\n"
        f"{comment_lines}"
    )


def parse_openai_consensus_response(content: str) -> dict:
    """
    Parse the JSON string returned by GPT-4o into an AI metadata dict.

    GPT-4o with response_format=json_object guarantees valid JSON, but
    some versions wrap the output in markdown code fences (```json...```).
    This function strips those before parsing.

    Args:
        content: Raw string from choices[0].message.content.

    Returns:
        Dict with AI metadata keys (executive_summary, impact_level, etc.).

    Raises:
        ValueError: If content cannot be parsed as valid JSON.
    """
    stripped = content.strip()
    if stripped.startswith("```"):
        # Strip opening fence and optional language tag
        stripped = stripped.split("```", 2)[1]
        if stripped.startswith("json"):
            stripped = stripped[4:]
        # Strip closing fence
        stripped = stripped.rsplit("```", 1)[0].strip()

    try:
        return json.loads(stripped)
    except json.JSONDecodeError as exc:
        raise ValueError(
            f"[gold_job] GPT-4o response is not valid JSON: {exc}\n"
            f"Content (first 200 chars): {content[:200]}"
        ) from exc


def call_openai_consensus(silver_social: dict, client: Any) -> dict:
    """
    Call GPT-4o to produce a Consensus Bundle summary (Section 4.2A).

    Uses response_format=json_object to guarantee parseable output.
    Temperature 0.3: balances determinism with natural language quality.
    max_tokens 1024: sufficient for the 11-field JSON schema; avoids
    over-generation that could corrupt the JSON structure.

    Args:
        silver_social: Silver social record (market context + comments).
        client:        openai.OpenAI instance (injected for testability).

    Returns:
        AI metadata dict parsed from GPT-4o JSON response.

    Raises:
        ValueError: If the OpenAI response cannot be parsed as JSON.
    """
    from config.settings import OPENAI_MODEL_NAME

    prompt = build_consensus_prompt(silver_social)
    response = client.chat.completions.create(
        model=OPENAI_MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CONSENSUS_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    # Cost capture BEFORE parsing (7B.5-I T4): a paid call whose JSON fails
    # to parse (→ DLQ) must still land in llm_cost_events.
    record_usage(
        OPENAI_MODEL_NAME, response,
        site="gold_consensus",
        source_name=silver_social.get("source_name", "polymarket"),
        trace_id=_cost_trace_id(silver_social),
    )
    return parse_openai_consensus_response(response.choices[0].message.content)


def call_openai_embedding(
    text: str,
    client: Any,
    *,
    source_name: Optional[str] = None,
    trace_id: Optional[str] = None,
) -> list[float]:
    """
    Call text-embedding-3-small to produce a 1536-dim vector (Section 5.2).

    The input text is the GPT-4o executive_summary from Consensus Bundling:
    embedding the summary rather than raw comments means the HNSW index
    encodes the synthesised consensus position, not conversational noise.

    Cost instrumentation (7B.5-I T4): recorded HERE, once, because this
    helper is shared by all 5 embedding paths (polymarket / hackernews /
    newsapi / arxiv / telegram) — instrumenting the callers would be 5
    copies of the same line (Section 3.2 DRY). source_name/trace_id are
    keyword-only with None defaults so pre-existing callers and Gate 2
    tests stay valid (approved P2); callers pass them so the cost row
    knows "who is expensive" (§2.5).

    Args:
        text:        The text to embed (typically the consensus executive_summary).
        client:      openai.OpenAI instance (injected for testability).
        source_name: Producing source, for the llm_cost_events row.
        trace_id:    canonical_event_id of the processed object.

    Returns:
        list[float] of length EMBEDDING_DIM (1536).
    """
    response = client.embeddings.create(
        model=EMBEDDING_MODEL,
        input=text,
    )
    record_usage(
        EMBEDDING_MODEL, response,
        site="gold_embed",
        source_name=source_name,
        trace_id=trace_id,
    )
    return response.data[0].embedding


# ==========================================================
# Gold Social Pulse Builder (Section C.6)
# ==========================================================

def build_gold_social_pulse(
    silver_social: dict,
    ai_meta:       dict,
    embedding:     list[float],
) -> dict:
    """
    Assemble the Gold Social Pulse record (Section C.6) for Polymarket.

    Bridges Silver → Gold:
        canonical_event_id  ← bronze_ref  (traces back to Bronze envelope)
        silver_data_ref     ← social_vault.social_id UUID set by caller after sv_archive()
        embedding           ← GPT-4o executive_summary vectorised

    Why embed the summary, not individual comments: Consensus Bundling
    (Section 4.2A) compresses N comments into one representative vector.
    Per-comment embeddings would cause Vector DB bloat and recall dilution.

    Args:
        silver_social: Silver social record from process.silver.social_pulse.
        ai_meta:       Structured AI metadata from call_openai_consensus().
        embedding:     1536-dim float list from call_openai_embedding().

    Returns:
        Gold Social Pulse dict conforming to Section C.6.
    """
    market_id    = silver_social.get("market_id", "")
    question     = silver_social.get("question", "")
    raw_comments = silver_social.get("raw_comments", [])
    ingested_at  = silver_social.get("ingested_at", datetime.now(timezone.utc).isoformat())
    bronze_ref   = silver_social.get("bronze_ref", "")

    return {
        "metadata": {
            # Deterministic signal_id from content_hash ensures ON CONFLICT
            # (signal_id) DO NOTHING fires on Flink re-deliveries and E2E reruns,
            # preventing duplicate Gold vectors (Gap 2 fix, Sprint 11, Section 4.1C).
            "signal_id":          _deterministic_signal_id(silver_social.get("content_hash", "")),
            "canonical_event_id": bronze_ref,
            "source_platform":    "polymarket",
            "published_at":       ingested_at,
            # silver_data_ref starts as None and is patched by the caller with the
            # actual social_vault.social_id after sv_archive() returns (T1, Sprint 11).
            # None is the correct sentinel — a UUID pointing to the wrong table
            # (bronze_ref) would be a misleading reference (see Section C.6).
            "silver_data_ref":    None,
            "raw_data_ref":       bronze_ref,
        },
        "content_vitals": {
            "title":               question,
            "url":                 f"https://polymarket.com/event/{market_id}",
            "description_snippet": ai_meta.get("executive_summary", "")[:300],
        },
        "enrichment_ai": {
            "executive_summary":    ai_meta.get("executive_summary", ""),
            "key_findings":         ai_meta.get("key_findings", []),
            # Explicit int/float casts: the validator requires exact types.
            # GPT-4o may return integers for score fields depending on the
            # random seed — cast defensively rather than relying on model
            # type consistency.
            "impact_level":         int(ai_meta.get("impact_level", 3)),
            "urgency_level":        int(ai_meta.get("urgency_level", 2)),
            "reliability_score":    float(ai_meta.get("reliability_score", 0.5)),
            "sentiment_score":      float(ai_meta.get("sentiment_score", 0.0)),
            "extracted_entities":   ai_meta.get("extracted_entities", []),
            "topic_classification": ai_meta.get("topic_classification", ""),
            "fact_check_flag":      bool(ai_meta.get("fact_check_flag", False)),
        },
        "social_context": {
            "community_name":  "Polymarket",
            "author_handle":   None,
            # Platform-level default: no per-author reputation available for
            # consensus bundles (multiple authors collapsed into one record).
            "author_reputation":        0.8,
            # Engagement = comment volume fed into this consensus bundle
            "primary_engagement_score": len(raw_comments),
        },
        "platform_logic": {
            "entry_type":               "market_consensus",
            "aggregation_window_hours": 4,
            "comment_volume_analyzed":  len(raw_comments),
            "consensus_rating":         float(ai_meta.get("consensus_rating", 0.5)),
            "market_id_ref":            market_id,
            # Section B.8: drill-down flag — raw comments retrievable via Social Vault
            "has_raw_source":           True,
            "uncertainty_index":        float(ai_meta.get("uncertainty_index", 0.0)),
            # Whale alert not applicable to comment bundles; it is tracked in
            # the price_update (structured_metrics) path instead.
            "whale_alert":              False,
        },
        "embedding": embedding,
    }


# ==========================================================
# Momentum Block (Section 4.2B)
# ==========================================================

def compute_momentum_block(
    current_value: float,
    price_history: list[tuple[str, float]],
    _now: Optional[datetime] = None,
) -> dict:
    """
    Compute deterministic momentum deltas (change_24h, change_7d, change_30d).

    Why computed at Gold, not Silver: Silver records stub all deltas to 0.0
    because computing them requires the asset's historical observations.
    The Flink KeyedProcessFunction maintains per-asset history in ValueState
    so deltas can be computed without a DB round-trip on every update.

    Delta formula: (current - reference) / reference
    Reference selection: the most recent observation recorded AT OR BEFORE
    the target lookback window (e.g., current_time - 24h). If no observation
    falls in that window, the delta is 0.0.

    Args:
        current_value: The current asset price / metric value.
        price_history: Chronologically sorted list of (timestamp_utc_iso, value)
                       tuples for this asset. The current observation is NOT
                       included — it will be appended by the Flink operator
                       after this function returns.
        _now:          Override the "current" time. Injected by Gate 2 tests
                       to produce deterministic delta assertions without
                       relying on wall-clock time (Section 9.3).

    Returns:
        momentum_block dict: change_24h, change_7d, change_30d (fractional),
        is_new_market (True when no prior history exists).
    """
    now = _now or datetime.now(timezone.utc)

    def find_closest_before(hours: int) -> Optional[float]:
        """Return the most recent value recorded at or before now - hours."""
        target = now - timedelta(hours=hours)
        # Walk chronologically: last qualifying entry is the closest to target
        candidates: list[float] = []
        for ts_str, val in price_history:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
            except ValueError:
                continue
            if ts <= target:
                candidates.append(val)
        return candidates[-1] if candidates else None

    def pct_change(reference: Optional[float]) -> float:
        if reference is None or reference == 0.0:
            return 0.0
        return round((current_value - reference) / reference, 6)

    return {
        "change_24h":    pct_change(find_closest_before(24)),
        "change_7d":     pct_change(find_closest_before(24 * 7)),
        "change_30d":    pct_change(find_closest_before(24 * 30)),
        "is_new_market": len(price_history) == 0,
    }


def build_gold_structured_metric(
    silver_metric: dict,
    momentum:      dict,
) -> dict:
    """
    Produce a Gold Structured Metric by merging Silver with computed momentum.

    Replaces the stubbed momentum_block (all 0.0) from Silver with the
    values computed by compute_momentum_block() from Flink keyed state.

    Whale alert handling (Section B.8): when metadata_extension.whale_alert
    is True, the top-level whale_alert key is set on the Gold record so
    downstream consumers can filter on it without inspecting JSONB.

    Args:
        silver_metric: Silver Structured Metric (Section C.2) with stubs.
        momentum:      Computed momentum_block from compute_momentum_block().

    Returns:
        Gold Structured Metric — deep copy with layer=Gold, populated
        momentum_block, and top-level whale_alert flag.
    """
    result = copy.deepcopy(silver_metric)
    result["layer"]          = "Gold"
    result["momentum_block"] = momentum

    whale = result.get("metadata_extension", {}).get("whale_alert", False)
    result["whale_alert"] = bool(whale)

    return result


# ==========================================================
# Dispatch Functions — full Gold enrichment path per message type
# ==========================================================

def process_social_pulse_message(
    silver_social: dict,
    openai_client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Full Gold enrichment path for a Polymarket Silver social pulse record.

    Steps (Section 4.2A):
        1. Guard: empty comment batch → (None, None)
        2. Validate Silver social schema → DLQ on failure
        3. Lazy-create OpenAI client if not injected
        4. GPT-4o consensus bundling → DLQ on API error
        5. text-embedding-3-small embedding → DLQ on API error
        6. Build Gold Social Pulse (Section C.6)
        7. Validate Gold Social Pulse schema → DLQ on failure
        8. Return (GOLD_SOCIAL_PULSE, gold_record)

    Note on DB persistence: the Flink PolymarketGoldSocialFunction calls
    social_vectors.insert(gold_record) AFTER this function returns. Keeping
    persistence out of this function ensures it is testable at Gate 2
    without a live database (Section 9.3).

    Args:
        silver_social:  Silver social record from process.silver.social_pulse.
        openai_client:  openai.OpenAI instance. If None, created from
                        OPENAI_API_KEY in config/settings.py.

    Returns:
        (GOLD_SOCIAL_PULSE, gold_record) on success.
        (DEAD_LETTER_QUEUE, dlq_record) on any validation or API failure.
        (None, None) if comment batch is empty (intentional skip, not an error).
    """
    set_trace_id(silver_social.get("canonical_event_id", silver_social.get("bronze_ref", "")))
    if not silver_social.get("raw_comments"):
        logger.debug(
            "[gold/polymarket] Empty comment batch for market_id=%s — skipping",
            silver_social.get("market_id", "unknown"),
        )
        return None, None

    silver_result = validate_silver_social(silver_social)
    if not silver_result.is_valid:
        logger.warning(
            "[gold/polymarket] Silver social validation failed: %s",
            silver_result.errors,
        )
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, silver_result.errors, "silver_social_validation"
        )

    if openai_client is None:
        try:
            # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
            from utils.openai_client import get_openai_client
            from config.settings import OPENAI_API_KEY
            openai_client = get_openai_client(api_key=OPENAI_API_KEY)
        except ImportError:
            logger.error("[gold/polymarket] openai package not installed")
            return DEAD_LETTER_QUEUE, _dlq_record(
                silver_social, ["openai package not available"], "openai_init"
            )

    try:
        ai_meta = call_openai_consensus(silver_social, openai_client)
    except Exception as exc:
        logger.error("[gold/polymarket] OpenAI consensus call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, [f"OpenAI consensus error: {exc}"], "openai_consensus"
        )

    try:
        embedding = call_openai_embedding(
            ai_meta.get("executive_summary") or silver_social.get("question", ""),
            openai_client,
            source_name=silver_social.get("source_name", "polymarket"),
            trace_id=_cost_trace_id(silver_social),
        )
    except Exception as exc:
        logger.error("[gold/polymarket] OpenAI embedding call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, [f"OpenAI embedding error: {exc}"], "openai_embedding"
        )

    gold_record = build_gold_social_pulse(silver_social, ai_meta, embedding)

    gold_result = validate_gold_social_pulse(gold_record)
    if not gold_result.is_valid:
        logger.error(
            "[gold/polymarket] Gold social pulse validation failed: %s",
            gold_result.errors,
        )
        return DEAD_LETTER_QUEUE, _dlq_record(
            silver_social, gold_result.errors, "gold_social_pulse_validation"
        )

    return GOLD_SOCIAL_PULSE, gold_record


def process_structured_metrics_message(
    silver_metric: dict,
    price_history: list[tuple[str, float]],
) -> tuple[str, dict]:
    """
    Full Gold enrichment path for a Polymarket Silver structured metric record.

    Computes momentum deltas from Flink keyed state and builds the Gold
    Structured Metric. Whale alert escalation is structural (non-AI),
    applied here as part of Section 4.2B enrichment.

    Note on persistence: the Flink PolymarketGoldMetricsFunction is
    responsible for (a) calling this function, (b) appending the current
    observation to ValueState, (c) persisting to momentum_vault (Task 1.5),
    and (d) emitting to serve.gold.structured_metrics.

    Args:
        silver_metric: Silver Structured Metric (Section C.2).
        price_history: Per-asset observation history from Flink ValueState.
                       Pass [] on first observation for an asset.

    Returns:
        (GOLD_STRUCTURED_METRICS, gold_metric).
    """
    set_trace_id(silver_metric.get("canonical_event_id", silver_metric.get("bronze_ref", "")))
    current_value = float(
        silver_metric.get("data_point", {}).get("current_value", 0.0)
    )
    momentum    = compute_momentum_block(current_value, price_history)
    gold_record = build_gold_structured_metric(silver_metric, momentum)
    return GOLD_STRUCTURED_METRICS, gold_record


# ==========================================================
# FRED Gold Branch — Automation Triggers & Dispatch (Section 4.2B / B.3)
# ==========================================================

# Series IDs whose 24h price variance triggers an immediate grounding flag.
# Commodity prices with fast-moving markets (Section B.3).
_FRED_PRICE_SERIES = frozenset({"DCOILWTICO", "GASREGW", "DHHNGSP"})

# Flink automation trigger thresholds (Section B.3)
_T10Y2Y_INVERSION_THRESHOLD = 0.0    # T10Y2Y < 0  → yield curve inversion
_VIXCLS_FEAR_THRESHOLD      = 30.0   # VIXCLS > 30 → extreme uncertainty
_PRICE_VARIANCE_THRESHOLD   = 0.05   # 5% in 24h   → price variance alert


def apply_fred_automation_triggers(
    gold_record: dict,
    momentum:    dict,
) -> dict:
    """
    Apply FRED-specific automation triggers to an already-built Gold record.

    Evaluates the three trigger rules from Section B.3 and enriches
    metadata_extension with the results. This is deterministic, non-AI
    logic — no OpenAI calls, no external I/O (Section 4.2B).

    Trigger rules (Section B.3):
        T10Y2Y < 0           → anomaly_flag "Market_Anomaly",      impact_level 5
                               (Yield Curve Inversion — historically precedes recessions)
        VIXCLS > 30          → anomaly_flag "Extreme_Uncertainty",  impact_level 5
                               (Fear Index spike — extreme market uncertainty)
        DCOILWTICO / GASREGW / DHHNGSP
          |change_24h| > 5%  → anomaly_flag "Price_Variance_Alert"
                               (Commodity price swing — potential supply/geopolitical shock)

    Why enrich metadata_extension rather than a top-level field: the Gold
    Structured Metric schema (Section C.2) uses metadata_extension as the
    JSONB column for source-specific fields. Adding trigger results here keeps
    the schema stable — the RAG agent queries metadata_extension.impact_level
    for fast severity filtering without altering the top-level schema contract.

    Why impact_level defaults to release_priority: release_priority (1–5) is
    the static editorial assessment of a series' importance. impact_level is
    the dynamic runtime assessment of this specific observation. When no trigger
    fires, the two coincide — keeping consistent semantics for the RAG agent.

    Args:
        gold_record: Gold Structured Metric produced by build_gold_structured_metric().
                     Modified copy is returned — original is never mutated.
        momentum:    The computed momentum_block dict from compute_momentum_block().
                     Needed to read change_24h for price variance checks.

    Returns:
        Deep-copy of gold_record with metadata_extension enriched with:
            anomaly_flags  (list[str])  — empty if no triggers fired
            impact_level   (int 1–5)    — elevated to 5 on critical triggers
            trigger_reason (str | None) — human-readable explanation, or None
    """
    series_id = gold_record.get("core_identity", {}).get("external_reference_id", "")
    value     = float(gold_record.get("data_point", {}).get("current_value", 0.0))

    # Default: inherit static release_priority, no alerts
    base_priority  = gold_record.get("metadata_extension", {}).get("release_priority", 3)
    anomaly_flags: list[str] = []
    impact_level   = base_priority
    trigger_reason: Optional[str] = None

    # --- Trigger 1: T10Y2Y yield curve inversion (Section B.3) ---
    if series_id == "T10Y2Y" and value < _T10Y2Y_INVERSION_THRESHOLD:
        anomaly_flags.append("Market_Anomaly")
        impact_level   = 5
        trigger_reason = (
            f"Yield curve inverted: T10Y2Y={value:.4f} < 0. "
            "Historical recession leading indicator."
        )

    # --- Trigger 2: VIXCLS extreme fear (Section B.3) ---
    elif series_id == "VIXCLS" and value > _VIXCLS_FEAR_THRESHOLD:
        anomaly_flags.append("Extreme_Uncertainty")
        impact_level   = 5
        trigger_reason = (
            f"VIX fear index elevated: VIXCLS={value:.2f} > {_VIXCLS_FEAR_THRESHOLD}. "
            "Extreme market uncertainty detected."
        )

    # --- Trigger 3: Commodity price variance > 5% in 24h (Section B.3) ---
    elif series_id in _FRED_PRICE_SERIES:
        change_24h_abs = abs(momentum.get("change_24h", 0.0))
        if change_24h_abs > _PRICE_VARIANCE_THRESHOLD:
            anomaly_flags.append("Price_Variance_Alert")
            trigger_reason = (
                f"{series_id} 24h variance={change_24h_abs * 100:.1f}% "
                f"exceeds {_PRICE_VARIANCE_THRESHOLD * 100:.0f}% threshold. "
                "Flag for immediate grounding."
            )

    result = copy.deepcopy(gold_record)
    result["metadata_extension"]["anomaly_flags"]  = anomaly_flags
    result["metadata_extension"]["impact_level"]   = impact_level
    result["metadata_extension"]["trigger_reason"] = trigger_reason

    return result


def process_fred_gold_message(
    silver_metric: dict,
    price_history: list[tuple[str, float]],
    _now: Optional[datetime] = None,
) -> tuple[str, dict]:
    """
    Full Gold enrichment path for a FRED Silver structured metric.

    Why FRED needs no OpenAI: FRED data is entirely numerical. The Gold
    enrichment is deterministic math (momentum deltas) plus rule-based
    trigger evaluation (Section B.3). Zero token cost, zero latency
    overhead — this path is always faster than the Social Pulse path.

    Steps (Section 4.2B + B.3):
        1. Compute momentum block from price_history via compute_momentum_block()
        2. Build Gold record via build_gold_structured_metric() — promotes layer to Gold,
           replaces stub momentum_block with computed values
        3. Apply FRED automation triggers via apply_fred_automation_triggers() —
           adds anomaly_flags, impact_level, trigger_reason to metadata_extension

    Note on persistence: the Flink FREDGoldMetricsFunction (in the PYFLINK_AVAILABLE
    block) calls momentum_vault.insert() AFTER this function returns. Keeping
    persistence out of this function ensures it is testable at Gate 2 without
    a live database (Section 9.3).

    Args:
        silver_metric: Silver Structured Metric from process.silver.structured_metrics,
                       produced by silver_job.map_fred_observation_to_silver().
        price_history: Per-series observation history from Flink ValueState.
                       List of (timestamp_utc_iso, value) tuples in chronological order.
                       Pass [] on first observation for a series.
        _now:          Override current time for deterministic test assertions
                       (injected by Gate 2 tests — same pattern as compute_momentum_block).

    Returns:
        (GOLD_STRUCTURED_METRICS, gold_metric) — always succeeds for valid Silver input.
        FRED Gold enrichment has no failure paths; a structurally valid Silver record
        always produces a valid Gold record (all operations are deterministic math).
    """
    set_trace_id(silver_metric.get("canonical_event_id", silver_metric.get("bronze_ref", "")))
    current_value = float(
        silver_metric.get("data_point", {}).get("current_value", 0.0)
    )
    momentum    = compute_momentum_block(current_value, price_history, _now=_now)
    gold_record = build_gold_structured_metric(silver_metric, momentum)
    gold_record = apply_fred_automation_triggers(gold_record, momentum)
    return GOLD_STRUCTURED_METRICS, gold_record


# ==========================================================
# NewsAPI Gold Branch — Cognitive Metadata Extraction (Section 4.2A / C.5)
# ==========================================================

def build_cognitive_metadata_prompt(silver_doc: dict) -> str:
    """
    Build the GPT-4o user prompt for Cognitive Metadata Extraction (Section 4.2A).

    Formats the article headline, lede, and body into a structured block.
    Labelled sections (Headline / Lede / Body) help GPT-4o apply the same
    inverted-pyramid weighting as the Keyword Sniper — title signal matters most.

    Args:
        silver_doc: Silver Full-Text Document Store record (Section C.3).
                    Expected keys: title, inverted_pyramid_lead, full_text_raw,
                    source_display_name (or source_name), publish_date.

    Returns:
        Formatted user prompt string.
    """
    title  = silver_doc.get("title", "")
    lead   = silver_doc.get("inverted_pyramid_lead", "")
    body   = silver_doc.get("full_text_raw", "")
    source = silver_doc.get("source_display_name") or silver_doc.get("source_name", "")
    date   = silver_doc.get("publish_date", "")

    return (
        f"Source: {source}\n"
        f"Published: {date}\n\n"
        f"Headline: {title}\n\n"
        f"Lede: {lead}\n\n"
        f"Body: {body}"
    )


def call_openai_cognitive_metadata(silver_doc: dict, client: Any) -> dict:
    """
    Call GPT-4o to extract Cognitive Metadata from a NewsAPI article (Section 4.2A).

    Uses response_format=json_object to guarantee parseable output.
    Temperature 0.2: lower than Consensus Bundling (0.3) because article analysis
    is more deterministic than synthesising divergent community opinion.

    Reuses parse_openai_consensus_response() for JSON parsing — same fence-stripping
    and error-raising logic applies to both response types (Section 3.2 DRY).

    Args:
        silver_doc: Silver Full-Text Document Store record (Section C.3).
        client:     openai.OpenAI instance (injected for testability).

    Returns:
        AI metadata dict parsed from GPT-4o JSON response.

    Raises:
        ValueError: If the OpenAI response cannot be parsed as JSON.
    """
    from config.settings import OPENAI_MODEL_NAME

    prompt = build_cognitive_metadata_prompt(silver_doc)
    response = client.chat.completions.create(
        model=OPENAI_MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": COGNITIVE_METADATA_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.2,
        max_tokens=1024,
    )
    # Cost capture BEFORE parsing (7B.5-I T4): a paid call whose JSON fails
    # to parse (→ DLQ) must still land in llm_cost_events. Shared by the
    # newsapi / arxiv / telegram paths — source_name comes off the doc.
    record_usage(
        OPENAI_MODEL_NAME, response,
        site="gold_enrich",
        source_name=silver_doc.get("source_name", ""),
        trace_id=_cost_trace_id(silver_doc),
    )
    return parse_openai_consensus_response(response.choices[0].message.content)


def apply_impact_boost(ai_meta: dict, silver_doc: dict) -> dict:
    """
    Apply the Impact Boost rule to AI metadata (Section B.4).

    If the Bronze producer flagged impact_boost=True (Israeli, Middle East,
    or energy keywords in title), the Gold Job increments impact_level by 1,
    capped at 5. This structural rule is applied after OpenAI scoring so that
    high-impact geopolitical/energy articles are never rated below their
    editorial significance floor set by the producer.

    Why cap at 5: impact_level is validated as an integer in [1, 5].
    A GPT-4o score of 5 (maximum assessed significance) must not be pushed
    to 6, which would fail validate_gold_signal() (Section C.5).

    Args:
        ai_meta:    AI metadata dict from call_openai_cognitive_metadata().
                    Modified in-place — caller must pass a copy if the original
                    must be preserved.
        silver_doc: Silver document — checked for impact_boost flag.

    Returns:
        ai_meta with impact_level boosted by 1 (if eligible).
    """
    if silver_doc.get("impact_boost"):
        current = int(ai_meta.get("impact_level", 3))
        ai_meta["impact_level"] = min(5, current + 1)
    return ai_meta


def build_gold_global_signal(
    silver_doc: dict,
    ai_meta:    dict,
    embedding:  list[float],
) -> dict:
    """
    Assemble the Gold Global Signal record (Section C.5) for a NewsAPI article.

    Silver → Gold field mapping:
        metadata.canonical_event_id  ← silver_doc.canonical_event_id
        metadata.silver_data_ref     ← silver_doc.doc_id  (knowledge_vault PK)
        metadata.raw_data_ref        ← silver_doc.bronze_ref
        domain_context.sniper_keywords ← silver_doc.sniper_keywords
        domain_context.is_breaking   ← silver_doc.impact_boost

    Why embed the executive_summary, not the raw article body:
        The AI-synthesised summary is the compressed intelligence signal.
        Embedding raw body text (often truncated by the NewsAPI free tier)
        produces noisier HNSW matches than a semantically coherent summary
        (Section 5.2 — embedding quality over raw text volume).

    Args:
        silver_doc: Silver Full-Text Document Store record (Section C.3).
        ai_meta:    Cognitive Metadata from call_openai_cognitive_metadata(),
                    with Impact Boost already applied by apply_impact_boost().
        embedding:  1536-dim float list from call_openai_embedding() on
                    ai_meta["executive_summary"].

    Returns:
        Gold Global Signal dict conforming to Section C.5.
    """
    return {
        "metadata": {
            "signal_id":          str(uuid.uuid4()),
            "canonical_event_id": silver_doc.get("canonical_event_id", ""),
            "source_platform":    "newsapi",
            "published_at":       silver_doc.get("publish_date", ""),
            # silver_data_ref → knowledge_vault doc_id (Silver PK, not Bronze UUID)
            "silver_data_ref":    silver_doc.get("doc_id", ""),
            "raw_data_ref":       silver_doc.get("bronze_ref", ""),
        },
        "content_vitals": {
            "title":               silver_doc.get("title", ""),
            "url":                 silver_doc.get("original_url", ""),
            "description_snippet": silver_doc.get("inverted_pyramid_lead", "")[:300],
        },
        "enrichment_ai": {
            "executive_summary":    ai_meta.get("executive_summary", ""),
            "key_findings":         ai_meta.get("key_findings", []),
            # Explicit int/float casts: GPT-4o may return integer for score fields
            # depending on the random seed — same defensive pattern as social pulse.
            "impact_level":         int(ai_meta.get("impact_level", 3)),
            "urgency_level":        int(ai_meta.get("urgency_level", 2)),
            "reliability_score":    float(ai_meta.get("reliability_score", 0.5)),
            "sentiment_score":      float(ai_meta.get("sentiment_score", 0.0)),
            "extracted_entities":   ai_meta.get("extracted_entities", []),
            "topic_classification": ai_meta.get("topic_classification", ""),
            "fact_check_flag":      bool(ai_meta.get("fact_check_flag", False)),
        },
        # domain_context — NewsAPI-specific fields (Section C.5)
        "domain_context": {
            "sniper_keywords": silver_doc.get("sniper_keywords", []),
            # is_breaking mirrors impact_boost — producer already evaluated this
            "is_breaking":     bool(silver_doc.get("impact_boost", False)),
            # share_count: NewsAPI free tier has no share data; placeholder for
            # paid-tier enrichment in later sprints.
            "share_count":     0,
            "geospatial_focus": ai_meta.get("geospatial_focus", ""),
        },
        "embedding": embedding,
    }


def process_newsapi_gold_message(
    silver_doc:    dict,
    openai_client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Full Gold enrichment path for a NewsAPI Silver Global News record.

    Steps (Section 4.2A):
        1. Guard: is_high_signal=False → (None, None) — skip OpenAI (Section 4.1A)
        2. Validate Silver document schema → DLQ on failure
        3. Lazy-create OpenAI client if not injected
        4. GPT-4o Cognitive Metadata Extraction → DLQ on API error
        5. Apply Impact Boost rule if silver_doc.impact_boost=True (Section B.4)
        6. text-embedding-3-small on executive_summary → DLQ on API error
        7. Build Gold Global Signal (Section C.5)
        8. Validate Gold Global Signal schema → DLQ on failure
        9. Return (GOLD_GLOBAL_NEWS, gold_record)

    Note on low-signal articles: articles with is_high_signal=False are stored
    in the Knowledge Vault by knowledge_vault.py (via a Flink branch reading
    SILVER_GLOBAL_NEWS directly). They skip Gold entirely — zero OpenAI token
    spend (Section 4.1A: 'reducing downstream token costs').

    Note on DB persistence: the Flink NewsAPIGoldFunction calls
    knowledge_vault.archive() and knowledge_vectors.insert() AFTER this function
    returns. Keeping persistence out of this function ensures Gate 2 tests can
    run without a live database (Section 9.3).

    Args:
        silver_doc:    Silver Full-Text Document Store record from
                       process.silver.global_news (Section C.3).
        openai_client: openai.OpenAI instance. If None, created from
                       OPENAI_API_KEY in config/settings.py.

    Returns:
        (GOLD_GLOBAL_NEWS, gold_record) on success.
        (DEAD_LETTER_QUEUE, dlq_record) on any validation or API failure.
        (None, None) if is_high_signal=False (intentional skip, not an error).
    """
    set_trace_id(silver_doc.get("canonical_event_id", silver_doc.get("bronze_ref", "")))
    # --- Guard: only high-signal articles receive OpenAI enrichment (Section 4.1A) ---
    if not silver_doc.get("is_high_signal", False):
        logger.debug(
            "[gold/newsapi] Low-signal article skipped — url=%s  score=%.3f",
            silver_doc.get("original_url", "unknown")[:80],
            silver_doc.get("relevance_score", 0.0),
        )
        return None, None

    # --- Gate: Silver document schema ---
    silver_result = validate_silver_document(silver_doc)
    if not silver_result.is_valid:
        logger.warning(
            "[gold/newsapi] Silver document validation failed: %s",
            silver_result.errors,
        )
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(
            silver_doc, silver_result.errors, "silver_document_validation"
        )

    if openai_client is None:
        try:
            # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
            from utils.openai_client import get_openai_client
            from config.settings import OPENAI_API_KEY
            openai_client = get_openai_client(api_key=OPENAI_API_KEY)
        except ImportError:
            logger.error("[gold/newsapi] openai package not installed")
            return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(
                silver_doc, ["openai package not available"], "openai_init"
            )

    try:
        ai_meta = call_openai_cognitive_metadata(silver_doc, openai_client)
    except Exception as exc:
        logger.error("[gold/newsapi] OpenAI cognitive metadata call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(
            silver_doc, [f"OpenAI cognitive metadata error: {exc}"], "openai_cognitive_metadata"
        )

    # Apply Impact Boost before building the record (Section B.4)
    ai_meta = apply_impact_boost(ai_meta, silver_doc)

    try:
        embedding = call_openai_embedding(
            ai_meta.get("executive_summary") or silver_doc.get("title", ""),
            openai_client,
            source_name=silver_doc.get("source_name", "newsapi"),
            trace_id=_cost_trace_id(silver_doc),
        )
    except Exception as exc:
        logger.error("[gold/newsapi] OpenAI embedding call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(
            silver_doc, [f"OpenAI embedding error: {exc}"], "openai_embedding"
        )

    gold_record = build_gold_global_signal(silver_doc, ai_meta, embedding)

    gold_result = validate_gold_signal(gold_record)
    if not gold_result.is_valid:
        logger.error(
            "[gold/newsapi] Gold global signal validation failed: %s",
            gold_result.errors,
        )
        return DEAD_LETTER_QUEUE, _newsapi_gold_dlq_record(
            silver_doc, gold_result.errors, "gold_global_signal_validation"
        )

    return GOLD_GLOBAL_NEWS, gold_record


def _newsapi_gold_dlq_record(
    record: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for Gold Job NewsAPI failures (Section 3.5).

    Tags source_topic=SILVER_GLOBAL_NEWS so DLQ operators can route the
    replay to the correct Silver topic consumer — distinct from the Polymarket
    social pulse DLQ path which uses SILVER_SOCIAL_PULSE.
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Gold",
        "failed_stage":      failed_stage,
        "source_topic":      SILVER_GLOBAL_NEWS,
        "validation_errors": errors,
        "original_message":  record,
    }


# ==========================================================
# ArXiv Gold Branch — Cognitive Metadata Extraction (Section 4.2A / C.5)
# ==========================================================

def build_arxiv_gold_global_signal(
    silver_doc: dict,
    ai_meta:    dict,
    embedding:  list[float],
) -> dict:
    """
    Assemble the Gold Global Signal record (Section C.5) for an ArXiv paper.

    Shares the same metadata, content_vitals, enrichment_ai, and embedding
    structure as build_gold_global_signal() for NewsAPI, but the domain_context
    block uses the Academic extension (Section C.5 Domain Extensions — ArXiv row)
    instead of the Tactical extension used for NewsAPI.

    Why a separate builder instead of branching inside build_gold_global_signal():
        Section C.5 defines two distinct domain_context shapes:
          - Tactical (NewsAPI): sniper_keywords, is_breaking, share_count, geospatial_focus
          - Academic (ArXiv):   authors, is_peer_reviewed, citation_count, domain_tags
        A single builder with an if/else branch would conflate two unrelated schemas.
        Separate named builders make the domain_context contract explicit, keep each
        function independently testable, and follow the same pattern as the Silver Job
        (map_newsapi_article_to_silver vs map_arxiv_paper_to_silver). Section 3.2 DRY
        applies to shared logic — not to domain-specific schema differences.

    Why no apply_impact_boost() call:
        Section B.1 explicitly states "No Impact Boost rule (academic papers are not
        breaking-news sources)." apply_impact_boost() is a NewsAPI-only rule (Section B.4).
        The Gold record for ArXiv receives its impact_level solely from GPT-4o's
        Cognitive Metadata Extraction on the abstract content.

    Academic domain_context fields (Section C.5):
        authors:         List of author name strings from the Silver record.
                         Preserved as a list (not joined to string) so the RAG agent
                         can filter by author without string splitting.
        is_peer_reviewed: False for all arXiv papers — the arXiv repository is a
                         pre-print server; papers are not peer-reviewed before
                         appearing on arXiv. This is a structural fact about the
                         source, not an OpenAI-assessed quality score.
        citation_count:  0 — citation data is not available in the arXiv Atom feed.
                         A future sprint may enrich this from Semantic Scholar or
                         CrossRef. Zero correctly signals "unknown" to RAG agents.
        domain_tags:     arXiv category list from the Silver record (e.g., ['cs.AI',
                         'econ.GN']). These ARE the academic domain classification
                         — more precise than a free-text topic_classification for
                         research paper retrieval.

    Args:
        silver_doc: Silver Full-Text Document Store record for an ArXiv paper,
                    as produced by map_arxiv_paper_to_silver() (Section C.3).
        ai_meta:    Cognitive Metadata from call_openai_cognitive_metadata().
                    No Impact Boost applied — caller must NOT call apply_impact_boost()
                    before passing ai_meta to this builder.
        embedding:  1536-dim float list from call_openai_embedding() on
                    ai_meta["executive_summary"].

    Returns:
        Gold Global Signal dict conforming to Section C.5 with Academic domain_context.
    """
    return {
        "metadata": {
            "signal_id":          str(uuid.uuid4()),
            "canonical_event_id": silver_doc.get("canonical_event_id", ""),
            "source_platform":    "arxiv",
            "publisher":          "arxiv",   # all papers originate from arxiv.org
            "published_at":       silver_doc.get("publish_date", ""),
            # silver_data_ref → knowledge_vault doc_id (Silver PK)
            "silver_data_ref":    silver_doc.get("doc_id", ""),
            "raw_data_ref":       silver_doc.get("bronze_ref", ""),
        },
        "content_vitals": {
            "title":               silver_doc.get("title", ""),
            "url":                 silver_doc.get("original_url", ""),
            # abstract is the lede for papers (see map_arxiv_paper_to_silver why)
            "description_snippet": silver_doc.get("inverted_pyramid_lead", "")[:300],
        },
        "enrichment_ai": {
            "executive_summary":    ai_meta.get("executive_summary", ""),
            "key_findings":         ai_meta.get("key_findings", []),
            # Explicit int/float casts: GPT-4o may return integer for score fields.
            "impact_level":         int(ai_meta.get("impact_level", 3)),
            "urgency_level":        int(ai_meta.get("urgency_level", 2)),
            "reliability_score":    float(ai_meta.get("reliability_score", 0.5)),
            "sentiment_score":      float(ai_meta.get("sentiment_score", 0.0)),
            "extracted_entities":   ai_meta.get("extracted_entities", []),
            "topic_classification": ai_meta.get("topic_classification", ""),
            "fact_check_flag":      bool(ai_meta.get("fact_check_flag", False)),
        },
        # domain_context — ArXiv Academic extension (Section C.5)
        "domain_context": {
            "authors":         silver_doc.get("authors", []),
            "is_peer_reviewed": False,   # arXiv is a pre-print server (structural fact)
            "citation_count":  0,        # not in Atom feed; placeholder for future enrichment
            "domain_tags":     silver_doc.get("categories", []),
        },
        "embedding": embedding,
    }


def process_arxiv_gold_message(
    silver_doc:    dict,
    openai_client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Full Gold enrichment path for an ArXiv Silver Global News record.

    Steps (Section 4.2A):
        1. Guard: is_high_signal=False → (None, None) — skip OpenAI (Section 4.1A)
        2. Validate Silver document schema → DLQ on failure
        3. Lazy-create OpenAI client if not injected
        4. GPT-4o Cognitive Metadata Extraction (shared COGNITIVE_METADATA_SYSTEM_PROMPT)
           → DLQ on API error
        5. [No Impact Boost — Section B.1: academic papers are not breaking-news sources]
        6. text-embedding-3-small on executive_summary → DLQ on API error
        7. Build Gold Global Signal via build_arxiv_gold_global_signal() (Academic domain_context)
        8. Validate Gold Global Signal schema → DLQ on failure
        9. Return (GOLD_GLOBAL_NEWS, gold_record)

    Why no apply_impact_boost():
        Impact Boost is a NewsAPI-specific rule for Israeli/Middle East security and
        energy articles from live feeds (Section B.4). Section B.1 explicitly excludes
        arXiv from this rule. Calling apply_impact_boost() here would incorrectly
        inflate impact_level for energy papers like "Crude Oil Supply Chain Analysis".

    Why the same COGNITIVE_METADATA_SYSTEM_PROMPT as NewsAPI:
        The 10-field Cognitive Metadata schema (executive_summary, key_findings,
        impact_level, urgency_level, reliability_score, sentiment_score,
        extracted_entities, topic_classification, fact_check_flag, geospatial_focus)
        is domain-agnostic. It applies equally to news articles and academic papers.
        The user prompt built by build_cognitive_metadata_prompt() already labels the
        source as "arxiv" and the body as the paper abstract, giving GPT-4o sufficient
        context to score the paper's significance to prediction markets (Section 4.2A).

    Note on DB persistence (same as process_newsapi_gold_message):
        The Flink ArXivGoldFunction calls knowledge_vault.archive() and
        knowledge_vectors.insert() AFTER this function returns. Keeping persistence
        out of this function ensures Gate 2 tests can run without a live DB (Section 9.3).

    Args:
        silver_doc:    Silver Full-Text Document Store record from
                       process.silver.global_news (Section C.3), as produced by
                       map_arxiv_paper_to_silver().
        openai_client: openai.OpenAI instance. If None, created from
                       OPENAI_API_KEY in config/settings.py.

    Returns:
        (GOLD_GLOBAL_NEWS, gold_record) on success.
        (DEAD_LETTER_QUEUE, dlq_record) on any validation or API failure.
        (None, None) if is_high_signal=False (intentional skip, not an error).
    """
    set_trace_id(silver_doc.get("canonical_event_id", silver_doc.get("bronze_ref", "")))
    # --- Guard: only high-signal papers receive OpenAI enrichment (Section 4.1A) ---
    if not silver_doc.get("is_high_signal", False):
        logger.debug(
            "[gold/arxiv] Low-signal paper skipped — arxiv_id=%s  score=%.3f",
            silver_doc.get("arxiv_id", "unknown")[:25],
            silver_doc.get("relevance_score", 0.0),
        )
        return None, None

    # --- Gate: Silver document schema ---
    silver_result = validate_silver_document(silver_doc)
    if not silver_result.is_valid:
        logger.warning(
            "[gold/arxiv] Silver document validation failed: %s",
            silver_result.errors,
        )
        return DEAD_LETTER_QUEUE, _arxiv_gold_dlq_record(
            silver_doc, silver_result.errors, "silver_document_validation"
        )

    if openai_client is None:
        try:
            # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
            from utils.openai_client import get_openai_client
            from config.settings import OPENAI_API_KEY
            openai_client = get_openai_client(api_key=OPENAI_API_KEY)
        except ImportError:
            logger.error("[gold/arxiv] openai package not installed")
            return DEAD_LETTER_QUEUE, _arxiv_gold_dlq_record(
                silver_doc, ["openai package not available"], "openai_init"
            )

    try:
        ai_meta = call_openai_cognitive_metadata(silver_doc, openai_client)
    except Exception as exc:
        logger.error("[gold/arxiv] OpenAI cognitive metadata call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _arxiv_gold_dlq_record(
            silver_doc, [f"OpenAI cognitive metadata error: {exc}"], "openai_cognitive_metadata"
        )

    # Note: apply_impact_boost() is intentionally NOT called here.
    # Section B.1: "No Impact Boost rule for academic papers."

    try:
        embedding = call_openai_embedding(
            ai_meta.get("executive_summary") or silver_doc.get("title", ""),
            openai_client,
            source_name=silver_doc.get("source_name", "arxiv"),
            trace_id=_cost_trace_id(silver_doc),
        )
    except Exception as exc:
        logger.error("[gold/arxiv] OpenAI embedding call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _arxiv_gold_dlq_record(
            silver_doc, [f"OpenAI embedding error: {exc}"], "openai_embedding"
        )

    gold_record = build_arxiv_gold_global_signal(silver_doc, ai_meta, embedding)

    gold_result = validate_gold_signal(gold_record)
    if not gold_result.is_valid:
        logger.error(
            "[gold/arxiv] Gold global signal validation failed: %s",
            gold_result.errors,
        )
        return DEAD_LETTER_QUEUE, _arxiv_gold_dlq_record(
            silver_doc, gold_result.errors, "gold_global_signal_validation"
        )

    return GOLD_GLOBAL_NEWS, gold_record


def _arxiv_gold_dlq_record(
    record: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for Gold Job ArXiv failures (Section 3.5).

    Tags source_topic=SILVER_GLOBAL_NEWS — ArXiv papers arrive from
    process.silver.global_news, the same topic as NewsAPI articles. DLQ
    operators filtering by source_topic will see both; they can distinguish
    by inspecting original_message.source_name == "arxiv".
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Gold",
        "failed_stage":      failed_stage,
        "source_topic":      SILVER_GLOBAL_NEWS,
        "validation_errors": errors,
        "original_message":  record,
    }


# ==========================================================
# Telegram Gold Branch — Cognitive Metadata Extraction (Section 4.2A / C.5 / A.1)
# ==========================================================

def build_telegram_gold_global_signal(
    silver_doc: dict,
    ai_meta:    dict,
    embedding:  list[float],
) -> dict:
    """
    Assemble the Gold Global Signal record (Section C.5) for a Telegram message.

    Shares the same metadata, content_vitals, enrichment_ai, and embedding
    structure as the NewsAPI and ArXiv builders, but the domain_context block
    uses the Direct Message extension (Section C.5 — Telegram row).

    Why a separate builder instead of branching inside build_gold_global_signal():
        Section C.5 defines three distinct domain_context shapes for the Global
        News path. The Direct Message extension carries Telegram-specific provenance
        fields (channel_username, is_forwarded, forwarded_from, views, extracted_links)
        that have no equivalent in the Tactical (NewsAPI) or Academic (ArXiv) shapes.
        A single builder with an if/elif/else chain would conflate three unrelated
        schemas. Section 3.2 DRY applies to shared logic — not domain-specific schema
        differences. Same rationale as build_arxiv_gold_global_signal().

    Why no apply_impact_boost():
        Section A.1 states no Impact Boost for Telegram channels. Impact Boost is
        a NewsAPI-only rule for authority-whitelisted breaking-news sources (Section B.4).
        Telegram channels are vetted at the source-registry level (7 pre-approved channels)
        — their credibility is structural, not article-by-article.

    Direct Message domain_context fields (Section C.5):
        channel_username: Short-form channel handle (e.g. "clashreport") — enables
                          RAG-agent filtering by source channel without parsing original_url.
        is_forwarded:     Whether the message was forwarded from another channel.
                          Forwarded messages carry an additional provenance signal: the
                          original authoring channel (forwarded_from). RAG agents can
                          use this to weight forwarded vs. original signal differently.
        forwarded_from:   Name of the originating channel if forwarded, else null.
        views:            Telegram view count at ingestion time. Proxy for signal
                          reach — high-view messages have broader audience impact.
        extracted_links:  URLs extracted from the message body or entity list.
                          Preserved as a list so downstream consumers can follow
                          sources without re-parsing the raw message text.

    Args:
        silver_doc: Silver Full-Text Document Store record for a Telegram message,
                    as produced by map_telegram_message_to_silver() (Section C.3).
        ai_meta:    Cognitive Metadata from call_openai_cognitive_metadata().
                    No Impact Boost applied — caller must NOT call apply_impact_boost()
                    before passing ai_meta to this builder (Section A.1).
        embedding:  1536-dim float list from call_openai_embedding() on
                    ai_meta["executive_summary"].

    Returns:
        Gold Global Signal dict conforming to Section C.5 with Direct Message domain_context.
    """
    return {
        "metadata": {
            "signal_id":          str(uuid.uuid4()),
            "canonical_event_id": silver_doc.get("canonical_event_id", ""),
            "source_platform":    "telegram",
            "published_at":       silver_doc.get("publish_date", ""),
            # silver_data_ref → knowledge_vault doc_id (Silver PK)
            "silver_data_ref":    silver_doc.get("doc_id", ""),
            "raw_data_ref":       silver_doc.get("bronze_ref", ""),
        },
        "content_vitals": {
            "title":               silver_doc.get("title", ""),
            "url":                 silver_doc.get("original_url", ""),
            # first 300 chars of message text (stored in inverted_pyramid_lead for Telegram)
            "description_snippet": silver_doc.get("inverted_pyramid_lead", "")[:300],
        },
        "enrichment_ai": {
            "executive_summary":    ai_meta.get("executive_summary", ""),
            "key_findings":         ai_meta.get("key_findings", []),
            # Explicit int/float casts: GPT-4o may return integer for score fields.
            "impact_level":         int(ai_meta.get("impact_level", 3)),
            "urgency_level":        int(ai_meta.get("urgency_level", 2)),
            "reliability_score":    float(ai_meta.get("reliability_score", 0.5)),
            "sentiment_score":      float(ai_meta.get("sentiment_score", 0.0)),
            "extracted_entities":   ai_meta.get("extracted_entities", []),
            "topic_classification": ai_meta.get("topic_classification", ""),
            "fact_check_flag":      bool(ai_meta.get("fact_check_flag", False)),
        },
        # domain_context — Telegram Direct Message extension (Section C.5)
        "domain_context": {
            "channel_username": silver_doc.get("channel_username", ""),
            "is_forwarded":     bool(silver_doc.get("is_forwarded", False)),
            "forwarded_from":   silver_doc.get("forwarded_from"),
            "views":            silver_doc.get("views"),
            "extracted_links":  silver_doc.get("extracted_links", []),
        },
        "embedding": embedding,
    }


def process_telegram_gold_message(
    silver_doc:    dict,
    openai_client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Full Gold enrichment path for a Telegram Silver Global News record.

    Steps (Section 4.2A):
        1. Guard: is_high_signal=False → (None, None) — skip OpenAI (Section 4.1A)
        2. Validate Silver document schema → DLQ on failure
        3. Lazy-create OpenAI client if not injected
        4. GPT-4o Cognitive Metadata Extraction (shared COGNITIVE_METADATA_SYSTEM_PROMPT)
           → DLQ on API error
        5. [No Impact Boost — Section A.1: Impact Boost does not apply to Telegram channels]
        6. text-embedding-3-small on executive_summary → DLQ on API error
        7. Build Gold Global Signal via build_telegram_gold_global_signal() (Direct Message domain_context)
        8. Validate Gold Global Signal schema → DLQ on failure
        9. Return (GOLD_GLOBAL_NEWS, gold_record)

    Why no apply_impact_boost():
        Impact Boost is a NewsAPI-only rule for breaking-news articles from
        authority-whitelisted domains (Section B.4). Section A.1 explicitly
        excludes Telegram from this rule — channel authority is structural
        (7 pre-approved channels from the Source Registry), not per-message.

    Why the same COGNITIVE_METADATA_SYSTEM_PROMPT as NewsAPI/ArXiv:
        The 10-field Cognitive Metadata schema applies to any text signal
        regardless of format. For Telegram messages the 'body' slot receives
        the full message_text (stored in full_text_raw), giving GPT-4o the
        same signal quality as a news article body. The prompt label "Source: telegram"
        and the absence of a byline or publication date allows GPT-4o to score
        based on content significance rather than source prestige (Section 4.2A).

    Note on DB persistence (same as process_newsapi_gold_message and process_arxiv_gold_message):
        The Flink TelegramGoldFunction calls knowledge_vault.archive() and
        knowledge_vectors.insert() AFTER this function returns. Keeping persistence
        out of this function ensures Gate 2 tests can run without a live DB (Section 9.3).

    Args:
        silver_doc:    Silver Full-Text Document Store record from
                       process.silver.global_news (Section C.3), as produced by
                       map_telegram_message_to_silver().
        openai_client: openai.OpenAI instance. If None, created from
                       OPENAI_API_KEY in config/settings.py.

    Returns:
        (GOLD_GLOBAL_NEWS, gold_record) on success.
        (DEAD_LETTER_QUEUE, dlq_record) on any validation or API failure.
        (None, None) if is_high_signal=False (intentional skip, not an error).
    """
    set_trace_id(silver_doc.get("canonical_event_id", silver_doc.get("bronze_ref", "")))
    # --- Guard: only high-signal messages receive OpenAI enrichment (Section 4.1A) ---
    if not silver_doc.get("is_high_signal", False):
        logger.debug(
            "[gold/telegram] Low-signal message skipped — url=%s  score=%.3f",
            silver_doc.get("original_url", "unknown")[:80],
            silver_doc.get("relevance_score", 0.0),
        )
        return None, None

    # --- Gate: Silver document schema ---
    silver_result = validate_silver_document(silver_doc)
    if not silver_result.is_valid:
        logger.warning(
            "[gold/telegram] Silver document validation failed: %s",
            silver_result.errors,
        )
        return DEAD_LETTER_QUEUE, _telegram_gold_dlq_record(
            silver_doc, silver_result.errors, "silver_document_validation"
        )

    if openai_client is None:
        try:
            # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
            from utils.openai_client import get_openai_client
            from config.settings import OPENAI_API_KEY
            openai_client = get_openai_client(api_key=OPENAI_API_KEY)
        except ImportError:
            logger.error("[gold/telegram] openai package not installed")
            return DEAD_LETTER_QUEUE, _telegram_gold_dlq_record(
                silver_doc, ["openai package not available"], "openai_init"
            )

    try:
        ai_meta = call_openai_cognitive_metadata(silver_doc, openai_client)
    except Exception as exc:
        logger.error("[gold/telegram] OpenAI cognitive metadata call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _telegram_gold_dlq_record(
            silver_doc, [f"OpenAI cognitive metadata error: {exc}"], "openai_cognitive_metadata"
        )

    # Note: apply_impact_boost() is intentionally NOT called here.
    # Section A.1: "No Impact Boost" — Impact Boost is a NewsAPI-only rule (Section B.4).

    try:
        embedding = call_openai_embedding(
            ai_meta.get("executive_summary") or silver_doc.get("title", ""),
            openai_client,
            source_name=silver_doc.get("source_name", "telegram"),
            trace_id=_cost_trace_id(silver_doc),
        )
    except Exception as exc:
        logger.error("[gold/telegram] OpenAI embedding call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _telegram_gold_dlq_record(
            silver_doc, [f"OpenAI embedding error: {exc}"], "openai_embedding"
        )

    gold_record = build_telegram_gold_global_signal(silver_doc, ai_meta, embedding)

    gold_result = validate_gold_signal(gold_record)
    if not gold_result.is_valid:
        logger.error(
            "[gold/telegram] Gold global signal validation failed: %s",
            gold_result.errors,
        )
        return DEAD_LETTER_QUEUE, _telegram_gold_dlq_record(
            silver_doc, gold_result.errors, "gold_global_signal_validation"
        )

    return GOLD_GLOBAL_NEWS, gold_record


def _telegram_gold_dlq_record(
    record: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for Gold Job Telegram failures (Section 3.5).

    Tags source_topic=SILVER_GLOBAL_NEWS — Telegram messages arrive from
    process.silver.global_news, the same topic as NewsAPI and ArXiv records.
    DLQ operators can distinguish by inspecting original_message.source_name == "telegram".
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Gold",
        "failed_stage":      failed_stage,
        "source_topic":      SILVER_GLOBAL_NEWS,
        "validation_errors": errors,
        "original_message":  record,
    }


# ==========================================================
# HackerNews Gold Branch — Consensus Bundling (Section 4.2A / C.6 / B.2)
# ==========================================================

def build_hackernews_consensus_prompt(silver_social: dict) -> str:
    """
    Build the GPT-4o user prompt for HackerNews Consensus Bundling.

    Formats the story title and top community comments into a numbered list.
    Uses the same CONSENSUS_SYSTEM_PROMPT as Polymarket — the schema is
    format-agnostic; the user prompt label ("HackerNews story") gives GPT-4o
    sufficient context to adjust its consensus framing from prediction market
    outcomes to technical community discourse (Section 4.2A).

    Why title (not story_text) as the anchor:
        For standard link stories story_text is empty — the signal is entirely
        in the title and community reaction. For Ask HN / Show HN, story_text
        is included in the Silver record but the top_comments are the primary
        consensus signal. The title is always populated and unambiguous.

    Args:
        silver_social: Silver social record (Section C.3, HackerNews pattern).
                       Expected keys: title (str), top_comments (list of dicts
                       with a 'text' key).

    Returns:
        Formatted user prompt string for GPT-4o.
    """
    title        = silver_social.get("title", "Unknown story")
    top_comments = silver_social.get("top_comments") or []

    comment_lines = "\n".join(
        f"{i + 1}. {c.get('text', '')}"
        for i, c in enumerate(top_comments)
    )
    return (
        f"HackerNews story: {title}\n\n"
        f"Top community comments ({len(top_comments)} total):\n"
        f"{comment_lines}"
    )


def call_hackernews_consensus(silver_social: dict, client: Any) -> dict:
    """
    Call GPT-4o to produce a Consensus Bundle summary for a HackerNews story.

    Mirrors call_openai_consensus() but uses build_hackernews_consensus_prompt()
    which reads title + top_comments instead of question + raw_comments.
    Same CONSENSUS_SYSTEM_PROMPT, temperature, and max_tokens — the 11-field
    consensus schema is format-agnostic (Section 4.2A).

    Args:
        silver_social: Silver social record (HackerNews Story-Centric, C.3).
        client:        openai.OpenAI instance (injected for testability).

    Returns:
        AI metadata dict parsed from GPT-4o JSON response.

    Raises:
        ValueError: If the OpenAI response cannot be parsed as JSON.
    """
    from config.settings import OPENAI_MODEL_NAME

    prompt   = build_hackernews_consensus_prompt(silver_social)
    response = client.chat.completions.create(
        model=OPENAI_MODEL_NAME,
        response_format={"type": "json_object"},
        messages=[
            {"role": "system", "content": CONSENSUS_SYSTEM_PROMPT},
            {"role": "user",   "content": prompt},
        ],
        temperature=0.3,
        max_tokens=1024,
    )
    # Cost capture BEFORE parsing (7B.5-I T4): a paid call whose JSON fails
    # to parse (→ DLQ) must still land in llm_cost_events.
    record_usage(
        OPENAI_MODEL_NAME, response,
        site="gold_consensus",
        source_name=silver_social.get("source_name", "hackernews"),
        trace_id=_cost_trace_id(silver_social),
    )
    return parse_openai_consensus_response(response.choices[0].message.content)


def build_hackernews_gold_social_pulse(
    silver_social: dict,
    ai_meta:       dict,
    embedding:     list[float],
) -> dict:
    """
    Assemble the Gold Social Pulse record (Section C.6) for a HackerNews story.

    Shares the same metadata, content_vitals, enrichment_ai, social_context,
    and embedding structure as build_gold_social_pulse() for Polymarket, but
    uses the HackerNews Story-Centric platform extension (Section C.6).

    Why a separate builder instead of branching inside build_gold_social_pulse():
        Section C.6 defines platform-specific extensions for platform_logic.
        The HackerNews extension carries story_type, points, top_technical_insights,
        external_link_ref, and community_sentiment — none of which have equivalents
        in the Polymarket market_consensus shape. Section 3.2 DRY applies to shared
        logic, not domain-specific schema differences. Same rationale as
        build_arxiv_gold_global_signal().

    Platform extension fields (Section C.6 — HackerNews row):
        entry_type:             "hackernews_story_summary" — required by
                                social_vectors._default_entry_type() and the
                                pgvector HNSW index (Section C.6).
        story_type:             "story" | "ask_hn" | "show_hn" — derived at
                                Bronze time by _classify_story_type(). Allows
                                RAG agents to filter by content type.
        points:                 Algolia points at ingestion time. Proxy for
                                community endorsement and signal weight.
        top_technical_insights: ai_meta.key_findings list from GPT-4o Consensus
                                Bundling. Represents distilled technical
                                observations from the comment thread. Named
                                "top_technical_insights" in platform_logic to
                                follow Section C.6 platform extension naming.
        external_link_ref:      The story's external URL (empty for Ask HN).
                                Preserved at Gold level so RAG consumers can link
                                to the source article without parsing Silver.
        community_sentiment:    ai_meta.sentiment_score as a float. HackerNews
                                commentary is often technically critical — preserving
                                sentiment in platform_logic allows RAG agents to
                                filter by community reception independently.

    Args:
        silver_social: Silver social record from process_hackernews_message()
                       (Section C.3, HackerNews Story-Centric pattern).
        ai_meta:       Structured AI metadata from call_hackernews_consensus().
        embedding:     1536-dim float list from call_openai_embedding() on
                       ai_meta["executive_summary"].

    Returns:
        Gold Social Pulse dict conforming to Section C.6 with HackerNews
        Story-Centric platform_logic extension.
    """
    story_id     = str(silver_social.get("story_id", ""))
    title        = silver_social.get("title", "")
    url          = silver_social.get("url", "")
    author       = silver_social.get("author", "")
    points       = int(silver_social.get("points") or 0)
    story_type   = silver_social.get("story_type", "story")
    top_comments = silver_social.get("top_comments") or []
    ingested_at  = silver_social.get("ingested_at", datetime.now(timezone.utc).isoformat())
    bronze_ref   = silver_social.get("bronze_ref", "")

    return {
        "metadata": {
            # Deterministic signal_id from content_hash — same rationale as
            # build_gold_social_pulse() (Gap 2 fix, Sprint 11, Section 4.1C).
            "signal_id":          _deterministic_signal_id(silver_social.get("content_hash", "")),
            "canonical_event_id": bronze_ref,
            "source_platform":    "hackernews",
            "published_at":       ingested_at,
            # silver_data_ref starts as None and is patched by the caller with the
            # actual social_vault.social_id after sv_archive() returns (T1, Sprint 11).
            # None is the correct sentinel — a UUID pointing to the wrong table
            # (bronze_ref) would be a misleading reference (see Section C.6).
            "silver_data_ref":    None,
            "raw_data_ref":       bronze_ref,
        },
        "content_vitals": {
            "title":               title,
            "url":                 url,
            "description_snippet": ai_meta.get("executive_summary", "")[:300],
        },
        "enrichment_ai": {
            "executive_summary":    ai_meta.get("executive_summary", ""),
            "key_findings":         ai_meta.get("key_findings", []),
            # Explicit int/float casts: GPT-4o may return integers for score fields.
            "impact_level":         int(ai_meta.get("impact_level", 3)),
            "urgency_level":        int(ai_meta.get("urgency_level", 2)),
            "reliability_score":    float(ai_meta.get("reliability_score", 0.5)),
            "sentiment_score":      float(ai_meta.get("sentiment_score", 0.0)),
            "extracted_entities":   ai_meta.get("extracted_entities", []),
            "topic_classification": ai_meta.get("topic_classification", ""),
            "fact_check_flag":      bool(ai_meta.get("fact_check_flag", False)),
        },
        "social_context": {
            "community_name":           "HackerNews",
            # author_handle = story submitter (not a comment author — Consensus
            # Bundling collapses N commenters into one record, same as Polymarket)
            "author_handle":            author,
            "author_reputation":        0.8,
            # Engagement = number of top comments fed into the consensus bundle
            "primary_engagement_score": len(top_comments),
        },
        "platform_logic": {
            "entry_type":              "hackernews_story_summary",
            "story_type":              story_type,
            "points":                  points,
            "top_technical_insights":  ai_meta.get("key_findings", []),
            "external_link_ref":       url,
            "community_sentiment":     float(ai_meta.get("sentiment_score", 0.0)),
            # has_raw_source: top_comments are stored in social_vault for drill-down
            "has_raw_source":          True,
        },
        "embedding": embedding,
    }


def process_hackernews_gold_message(
    silver_social: dict,
    openai_client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Full Gold enrichment path for a HackerNews Silver Social Pulse record.

    Steps (Section 4.2A):
        1. Guard: is_high_signal=False → (None, None) — skip OpenAI token spend
           (Section 4.1A: low-signal stories stored in Social Vault, not enriched)
        2. Validate Silver social schema → DLQ on failure
        3. Lazy-create OpenAI client if not injected
        4. GPT-4o Consensus Bundling via call_hackernews_consensus()
           (build_hackernews_consensus_prompt + shared CONSENSUS_SYSTEM_PROMPT)
           → DLQ on API error
        5. text-embedding-3-small on executive_summary → DLQ on API error
        6. Build Gold Social Pulse via build_hackernews_gold_social_pulse()
           (HackerNews Story-Centric extension, Section C.6)
        7. Validate Gold Social Pulse schema → DLQ on failure
        8. Return (GOLD_SOCIAL_PULSE, gold_record)

    Why is_high_signal guard (not empty top_comments guard as in Polymarket):
        Polymarket guards on empty comments because an empty batch means there
        is nothing to summarise. HackerNews stories can have few comments
        (points > 50 but newly submitted). The is_high_signal flag from the
        Silver Keyword Sniper determines whether the story's topic is relevant
        to the prediction market domain — low-relevance stories skip OpenAI
        token spend (Section 4.1A), same policy as Global News path.

    Why the same CONSENSUS_SYSTEM_PROMPT as Polymarket:
        The 11-field Consensus Bundle schema (executive_summary, key_findings,
        consensus_rating, uncertainty_index, etc.) is domain-agnostic. The user
        prompt built by build_hackernews_consensus_prompt() labels the source as
        "HackerNews story", giving GPT-4o sufficient context to re-interpret
        "consensus" as community agreement on a technical story rather than a
        prediction market YES/NO outcome (Section 4.2A).

    Note on DB persistence:
        The Flink HackerNewsGoldSocialFunction (Phase 5) calls
        social_vectors.insert() AFTER this function returns. Keeping persistence
        out of this function ensures Gate 2 tests run without a live DB
        (Section 9.3).

    Args:
        silver_social:  Silver social record from process_hackernews_message()
                        (Section C.3, HackerNews Story-Centric pattern).
        openai_client:  openai.OpenAI instance. If None, created from
                        OPENAI_API_KEY in config/settings.py.

    Returns:
        (GOLD_SOCIAL_PULSE, gold_record) on success.
        (DEAD_LETTER_QUEUE, dlq_record) on any validation or API failure.
        (None, None) if is_high_signal=False (intentional skip, not an error).
    """
    set_trace_id(silver_social.get("canonical_event_id", silver_social.get("bronze_ref", "")))
    # --- Guard: only high-signal stories receive OpenAI enrichment (Section 4.1A) ---
    if not silver_social.get("is_high_signal", False):
        logger.debug(
            "[gold/hackernews] Low-signal story skipped — story_id=%s  score=%.3f",
            str(silver_social.get("story_id", "unknown"))[:20],
            silver_social.get("relevance_score", 0.0),
        )
        return None, None

    # --- Gate: Silver social schema ---
    silver_result = validate_silver_social(silver_social)
    if not silver_result.is_valid:
        logger.warning(
            "[gold/hackernews] Silver social validation failed: %s",
            silver_result.errors,
        )
        return DEAD_LETTER_QUEUE, _hackernews_gold_dlq_record(
            silver_social, silver_result.errors, "silver_social_validation"
        )

    if openai_client is None:
        try:
            # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
            from utils.openai_client import get_openai_client
            from config.settings import OPENAI_API_KEY
            openai_client = get_openai_client(api_key=OPENAI_API_KEY)
        except ImportError:
            logger.error("[gold/hackernews] openai package not installed")
            return DEAD_LETTER_QUEUE, _hackernews_gold_dlq_record(
                silver_social, ["openai package not available"], "openai_init"
            )

    try:
        ai_meta = call_hackernews_consensus(silver_social, openai_client)
    except Exception as exc:
        logger.error("[gold/hackernews] OpenAI consensus call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _hackernews_gold_dlq_record(
            silver_social, [f"OpenAI consensus error: {exc}"], "openai_consensus"
        )

    try:
        embedding = call_openai_embedding(
            ai_meta.get("executive_summary") or silver_social.get("title", ""),
            openai_client,
            source_name=silver_social.get("source_name", "hackernews"),
            trace_id=_cost_trace_id(silver_social),
        )
    except Exception as exc:
        logger.error("[gold/hackernews] OpenAI embedding call failed: %s", exc)
        return DEAD_LETTER_QUEUE, _hackernews_gold_dlq_record(
            silver_social, [f"OpenAI embedding error: {exc}"], "openai_embedding"
        )

    gold_record = build_hackernews_gold_social_pulse(silver_social, ai_meta, embedding)

    gold_result = validate_gold_social_pulse(gold_record)
    if not gold_result.is_valid:
        logger.error(
            "[gold/hackernews] Gold social pulse validation failed: %s",
            gold_result.errors,
        )
        return DEAD_LETTER_QUEUE, _hackernews_gold_dlq_record(
            silver_social, gold_result.errors, "gold_social_pulse_validation"
        )

    return GOLD_SOCIAL_PULSE, gold_record


def _hackernews_gold_dlq_record(
    record: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for Gold Job HackerNews failures (Section 3.5).

    Tags source_topic=SILVER_SOCIAL_PULSE — HackerNews stories arrive from
    process.silver.social_pulse alongside Polymarket records.
    DLQ operators can distinguish by inspecting original_message.source_name == "hackernews".
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Gold",
        "failed_stage":      failed_stage,
        "source_topic":      SILVER_SOCIAL_PULSE,
        "validation_errors": errors,
        "original_message":  record,
    }


def _dlq_record(record: dict, errors: list[str], failed_stage: str) -> dict:
    """
    Build a dead-letter-queue record for Gold Job failures (Section 3.5).

    Consistent DLQ envelope regardless of which stage fails, matching
    the format produced by silver_job._dlq_record() so DLQ consumers
    have a uniform structure to parse.
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Gold",
        "failed_stage":      failed_stage,
        "source_topic":      SILVER_SOCIAL_PULSE,
        "validation_errors": errors,
        "original_message":  record,
    }


# ==========================================================
# Google Trends Gold Branch — Automation Triggers & Dispatch (Section 4.2B / B.5)
# ==========================================================

# Flink automation trigger threshold (Section B.5).
# Public_Hype_Alert fires when a keyword's interest score increases by
# more than this many ABSOLUTE POINTS on the 0-100 scale within 24 hours.
# Example: score rises from 20 → 80 (+60 pts) → alert fires.
# Example: score rises from 40 → 85 (+45 pts) → no alert.
_GOOGLETRENDS_HYPE_THRESHOLD: float = 50.0


def apply_googletrends_automation_triggers(
    gold_record:   dict,
    momentum:      dict,
    current_value: float,
) -> dict:
    """
    Apply Google Trends automation triggers to an already-built Gold record.

    Trigger rule (Section B.5):
        Public_Hype_Alert if interest_score increases > 50 absolute points
        in 24h. Cross-reference spikes with Social Pulse to validate event
        scale.

    Why absolute points rather than fractional change_24h:
        compute_momentum_block() returns fractional change_24h =
        (current - ref) / ref. The spec defines the trigger in absolute
        score points on the 0-100 Google Trends scale — a jump from 20
        to 80 is 60 points, which should alert regardless of the fractional
        magnitude (300%). A fractional threshold would misfires on low-base
        keywords (a jump from 1 to 3 is +200% but only +2 pts — not a hype
        event).

    Recovering absolute change from fractional:
        change_24h_frac = (current - ref) / ref
        ⟹ current - ref = current × change_24h_frac / (1 + change_24h_frac)
        Edge case: change_24h_frac ≈ -1.0 (score fell to near-zero).
        Denominator guard: if |1 + change_24h_frac| < ε, use current_value
        as the absolute drop (score lost its full prior value).

    Args:
        gold_record:   Gold Structured Metric from build_gold_structured_metric().
                       Returned as a deep copy — original is never mutated.
        momentum:      Computed momentum_block from compute_momentum_block().
        current_value: Current interest score (0.0–100.0) for this keyword.
                       Required to convert fractional change_24h to absolute pts.

    Returns:
        Deep copy of gold_record with metadata_extension enriched with:
            public_hype_alert  (bool)         — True if trigger fired
            abs_change_24h_pts (float, 4 dp)  — absolute 24h score change in pts
            impact_level       (int 1–5)      — 4 when alert fires, else 1
            trigger_reason     (str | None)   — explanation string, or None
    """
    change_24h_frac = momentum.get("change_24h", 0.0)

    # Recover absolute 24h change in score points from fractional delta.
    # Formula derivation: see module docstring for this function.
    denominator = 1.0 + change_24h_frac
    if abs(denominator) > 1e-9:
        abs_change_24h_pts = abs(current_value * change_24h_frac / denominator)
    else:
        # Edge case: score dropped to near-zero (denominator ≈ 0).
        # The full prior value was lost — absolute drop equals current_value.
        abs_change_24h_pts = float(abs(current_value))

    public_hype_alert = abs_change_24h_pts > _GOOGLETRENDS_HYPE_THRESHOLD

    keyword = gold_record.get("core_identity", {}).get("external_reference_id", "")
    geo     = gold_record.get("metadata_extension", {}).get("geo", "")

    if public_hype_alert:
        impact_level   = 4
        trigger_reason = (
            f"Public_Hype_Alert: '{keyword}' ({geo}) interest score spiked "
            f"{abs_change_24h_pts:.1f} pts in 24h "
            f"(threshold={_GOOGLETRENDS_HYPE_THRESHOLD:.0f} pts). "
            "Cross-reference with Social Pulse to validate event scale (Section B.5)."
        )
    else:
        impact_level   = 1
        trigger_reason = None

    result = copy.deepcopy(gold_record)
    result["metadata_extension"]["public_hype_alert"]  = public_hype_alert
    result["metadata_extension"]["abs_change_24h_pts"] = round(abs_change_24h_pts, 4)
    result["metadata_extension"]["impact_level"]       = impact_level
    result["metadata_extension"]["trigger_reason"]     = trigger_reason

    return result


def process_googletrends_gold_message(
    silver_metric: dict,
    price_history: list[tuple[str, float]],
    _now: Optional[datetime] = None,
) -> tuple[str, dict]:
    """
    Full Gold enrichment path for a Google Trends Silver structured metric.

    Why no OpenAI: Google Trends data is entirely numerical (interest score
    0-100). The Gold enrichment is deterministic math (momentum deltas) plus
    one rule-based trigger (Public_Hype_Alert). Zero token cost, same
    pattern as FRED (Section 4.2B).

    Steps (Section 4.2B + B.5):
        1. Compute momentum block from price_history via compute_momentum_block()
        2. Build Gold record via build_gold_structured_metric() — promotes layer
           to "Gold", replaces stub momentum_block with computed values
        3. Apply Google Trends automation trigger via
           apply_googletrends_automation_triggers() — adds public_hype_alert,
           abs_change_24h_pts, impact_level, trigger_reason to metadata_extension

    Note on persistence: the Flink GoogleTrendsGoldMetricsFunction (in the
    PYFLINK_AVAILABLE block, Phase 5) calls momentum_vault.insert() AFTER
    this function returns. Keeping persistence out of this function ensures
    it is testable at Gate 2 without a live database (Section 9.3).

    Args:
        silver_metric: Silver Structured Metric from process.silver.structured_metrics,
                       produced by silver_job.map_googletrends_to_silver().
        price_history: Per-keyword observation history from Flink ValueState.
                       List of (timestamp_utc_iso, value) tuples in chronological
                       order (oldest first). Pass [] on first observation for a
                       keyword — compute_momentum_block() handles the empty case
                       by returning change_* = 0.0 and is_new_market = True.
        _now:          Override current time for deterministic test assertions
                       (injected by Gate 2 tests — same _now pattern as FRED).

    Returns:
        (GOLD_STRUCTURED_METRICS, gold_metric) — always succeeds for valid Silver
        input. Google Trends Gold enrichment has no failure paths; all operations
        are deterministic math with no external I/O.
    """
    set_trace_id(silver_metric.get("canonical_event_id", silver_metric.get("bronze_ref", "")))
    current_value = float(
        silver_metric.get("data_point", {}).get("current_value", 0.0)
    )
    momentum    = compute_momentum_block(current_value, price_history, _now=_now)
    gold_record = build_gold_structured_metric(silver_metric, momentum)
    gold_record = apply_googletrends_automation_triggers(
        gold_record, momentum, current_value
    )
    return GOLD_STRUCTURED_METRICS, gold_record


# ==========================================================
# OpenWeather Gold Branch — Automation Triggers & Dispatch (Section 4.2B / B.6)
# ==========================================================

# Strategic tag sets for trigger routing (Section B.6).
# Defined as private frozensets in the Gold Job — the Gold Job must not import
# from the producer (Service Isolation §3.3). These values mirror the
# strategic_tag values written by openweather_producer.HOTSPOTS.
_OPENWEATHER_TECH_SUPPLY_TAGS = frozenset({"taiwan_semiconductor", "japan_tech"})
_OPENWEATHER_MARITIME_TAGS    = frozenset({"strait_of_hormuz", "suez_canal", "port_of_houston"})

# Trigger thresholds (Section B.6).
# Mirroring producer constants as private Gold Job constants — avoids coupling
# Gold Job to the producer module while keeping the canonical values here explicit.
_OW_DISASTER_SEVERITY_THRESHOLD: int = 4   # condition_severity ≥ 4 → Natural_Disaster_Alert
_OW_WIND_SHIP_THRESHOLD: int        = 50   # wind_speed_knots > 50  → Potential_Shipping_Delay


def apply_openweather_automation_triggers(
    gold_record: dict,
    momentum:    dict,
) -> dict:
    """
    Apply OpenWeather automation triggers to an already-built Gold record.

    Three trigger rules (Section B.6):

        Natural_Disaster_Alert (impact_level: 5):
            condition_severity ≥ 4 at any hotspot. Covers thunderstorm,
            heavy rain, extreme rain, freezing rain, heavy snow, volcanic
            ash, squall, tornado — all conditions rated severity 4–5 in
            CONDITION_ID_TO_SEVERITY (Section T2 design decision).

        Tech_Supply_Risk (impact_level: 4):
            condition_severity ≥ 4 AND strategic_tag ∈ {"taiwan_semiconductor",
            "japan_tech"}. "Extreme weather" threshold (condition_severity ≥ 4)
            — severity 3 (rain/snow) is not extreme enough to warrant a
            semiconductor supply chain alert (task_plan.md T8 decision).

        Potential_Shipping_Delay (impact_level: 4):
            wind_speed_knots > 50 AND strategic_tag ∈ {"strait_of_hormuz",
            "suez_canal", "port_of_houston"}. Wind threshold is the maritime
            Beaufort force 10 boundary — sustained winds that prevent safe
            vessel transit through strategic chokepoints.

    Cold-start guard (is_new_market):
        When price_history was empty (no prior hotspot observation),
        compute_momentum_block() sets is_new_market=True. On the first
        observation we have no baseline to contextualise the reading —
        triggers are suppressed. This prevents spurious alerts on pipeline
        start-up when every hotspot produces its first Bronze message.

    Multiple triggers can fire simultaneously (independent checks — not
    elif). Final impact_level = max across all fired triggers. All fired
    trigger names are accumulated in anomaly_flags so the RAG agent can
    surface the full picture.

    Why enrich metadata_extension rather than a top-level field:
        Gold Structured Metric schema (Section C.2) uses metadata_extension
        as the JSONB column for source-specific fields. Adding trigger results
        here keeps the schema stable — consistent with the FRED and GoogleTrends
        Gold branches.

    Args:
        gold_record: Gold Structured Metric produced by build_gold_structured_metric().
                     Modified deep-copy is returned — original is never mutated.
        momentum:    Computed momentum_block from compute_momentum_block().
                     Used only for the cold-start (is_new_market) guard.

    Returns:
        Deep-copy of gold_record with metadata_extension enriched with:
            anomaly_flags   (list[str])  — empty if no triggers fired
            impact_level    (int 1–5)    — max of all fired trigger levels; 1 if none
            trigger_reason  (str | None) — human-readable explanation, or None
    """
    meta         = gold_record.get("metadata_extension", {})
    strategic_tag      = meta.get("strategic_tag", "")
    condition_severity = int(meta.get("condition_severity", 0))
    wind_speed_knots   = float(meta.get("wind_speed_knots", 0.0))
    hotspot_name       = gold_record.get("core_identity", {}).get("external_reference_id", "")

    anomaly_flags:   list[str] = []
    trigger_reasons: list[str] = []
    impact_level = 1   # default: no trigger fired

    # Cold-start guard — suppress all triggers on the first observation.
    # is_new_market=True means price_history was empty; no baseline exists
    # to contextualise whether this reading is anomalous (plan T8 design).
    if momentum.get("is_new_market", False):
        result = copy.deepcopy(gold_record)
        result["metadata_extension"]["anomaly_flags"]  = []
        result["metadata_extension"]["impact_level"]   = 1
        result["metadata_extension"]["trigger_reason"] = None
        return result

    # --- Trigger 1: Natural_Disaster_Alert (Section B.6) ---
    if condition_severity >= _OW_DISASTER_SEVERITY_THRESHOLD:
        anomaly_flags.append("Natural_Disaster_Alert")
        impact_level = max(impact_level, 5)
        trigger_reasons.append(
            f"Natural_Disaster_Alert: '{hotspot_name}' condition_severity="
            f"{condition_severity} ≥ {_OW_DISASTER_SEVERITY_THRESHOLD}. "
            "Significant weather disruption detected."
        )

    # --- Trigger 2: Tech_Supply_Risk (Section B.6) ---
    # Requires BOTH extreme severity AND a tech supply chain strategic tag.
    # Severity threshold is same as Natural_Disaster_Alert (≥ 4) because
    # "extreme weather" at semiconductor hubs is the qualifying condition.
    if (
        condition_severity >= _OW_DISASTER_SEVERITY_THRESHOLD
        and strategic_tag in _OPENWEATHER_TECH_SUPPLY_TAGS
    ):
        anomaly_flags.append("Tech_Supply_Risk")
        impact_level = max(impact_level, 4)
        trigger_reasons.append(
            f"Tech_Supply_Risk: extreme weather (severity={condition_severity}) "
            f"at '{hotspot_name}' (tag='{strategic_tag}'). "
            "Semiconductor/tech manufacturing supply chain at risk (Section B.6)."
        )

    # --- Trigger 3: Potential_Shipping_Delay (Section B.6) ---
    if (
        wind_speed_knots > _OW_WIND_SHIP_THRESHOLD
        and strategic_tag in _OPENWEATHER_MARITIME_TAGS
    ):
        anomaly_flags.append("Potential_Shipping_Delay")
        impact_level = max(impact_level, 4)
        trigger_reasons.append(
            f"Potential_Shipping_Delay: wind_speed={wind_speed_knots:.1f} knots "
            f"> {_OW_WIND_SHIP_THRESHOLD} at '{hotspot_name}' "
            f"(tag='{strategic_tag}'). "
            "Maritime chokepoint transit risk (Section B.6)."
        )

    trigger_reason: Optional[str] = " | ".join(trigger_reasons) if trigger_reasons else None

    result = copy.deepcopy(gold_record)
    result["metadata_extension"]["anomaly_flags"]  = anomaly_flags
    result["metadata_extension"]["impact_level"]   = impact_level
    result["metadata_extension"]["trigger_reason"] = trigger_reason

    return result


def process_openweather_gold_message(
    silver_metric: dict,
    price_history: list[tuple[str, float]],
    _now: Optional[datetime] = None,
) -> tuple[str, dict]:
    """
    Full Gold enrichment path for an OpenWeather Silver structured metric.

    Why no OpenAI: OpenWeather data is numerical (temperature, wind, pressure).
    The Gold enrichment is deterministic math (momentum deltas) plus rule-based
    trigger evaluation (Section B.6). Zero token cost — same pattern as FRED
    and GoogleTrends (Section 4.2B).

    Steps (Section 4.2B + B.6):
        1. Compute momentum block from price_history via compute_momentum_block()
        2. Build Gold record via build_gold_structured_metric() — promotes layer
           to "Gold", replaces stub momentum_block with computed values
        3. Apply OpenWeather automation triggers via
           apply_openweather_automation_triggers() — adds anomaly_flags,
           impact_level, trigger_reason to metadata_extension

    Cold-start behaviour:
        When price_history=[], compute_momentum_block() returns is_new_market=True
        and all deltas=0.0. apply_openweather_automation_triggers() detects
        is_new_market=True and suppresses all triggers — no spurious alerts on
        the first observation for each hotspot (plan T8 design decision).

    Note on persistence: the Flink OpenWeatherGoldMetricsFunction (Phase 5)
    calls momentum_vault.insert() AFTER this function returns. Keeping
    persistence out of this function ensures it is testable at Gate 2
    without a live database (Section 9.3).

    Args:
        silver_metric: Silver Structured Metric from process.silver.structured_metrics,
                       produced by silver_job.map_openweather_to_silver().
        price_history: Per-hotspot observation history from Flink ValueState.
                       List of (timestamp_utc_iso, temperature_celsius) tuples
                       in chronological order (oldest first).
                       Pass [] on first observation — compute_momentum_block()
                       handles empty history (is_new_market=True, all deltas=0.0).
        _now:          Override current time for deterministic test assertions
                       (same _now injection pattern as FRED and GoogleTrends).

    Returns:
        (GOLD_STRUCTURED_METRICS, gold_metric) — always succeeds for valid Silver
        input. OpenWeather Gold enrichment has no failure paths; all operations
        are deterministic math with no external I/O.
    """
    set_trace_id(silver_metric.get("canonical_event_id", silver_metric.get("bronze_ref", "")))
    current_value = float(
        silver_metric.get("data_point", {}).get("current_value", 0.0)
    )
    momentum    = compute_momentum_block(current_value, price_history, _now=_now)
    gold_record = build_gold_structured_metric(silver_metric, momentum)
    gold_record = apply_openweather_automation_triggers(gold_record, momentum)
    return GOLD_STRUCTURED_METRICS, gold_record


# ==========================================================
# OpenSky — Gold Automation Triggers (Section B.7, 4.2B)
# ==========================================================

# Trigger threshold constants (canonical values — also exported to producer
# module for Gate 1 structural invariant tests, Section 9.3).
_OPENSKY_DENSITY_THRESHOLD: float = 0.30   # change_30d > 0.30 → Aerial_Escalation_Risk

# Tension zones where transponder silence is flagged as a covert indicator (B.7).
# washington_dc_airspace is excluded: D.C. airspace has high volumes of government
# and classified aircraft with intermittent position data — the false-positive rate
# makes transponder silence there uninformative without additional context.
_OPENSKY_TENSION_ZONES: frozenset[str] = frozenset({
    "polish_ukrainian_border",
    "taiwan_strait",
    "iranian_borders",
    "strait_of_hormuz",
    "bab_al_mandab",
    "eastern_mediterranean",
})


def apply_opensky_automation_triggers(
    gold_record: dict,
    momentum:    dict,
) -> dict:
    """
    Apply OpenSky automation triggers to an already-built Gold record.

    Two triggers (Section B.7):

    1. Aerial_Escalation_Risk (impact_level=4):
        Fires when aircraft density is >30% above the 30-day average:
            change_30d > 0.30
        change_30d is fractional — (current - ref) / ref — so 0.30 means a 30%
        increase over the 30-day baseline count (plan approval 2026-04-04).

        Edge case: if 30-day reference was 0 (historically empty box), pct_change
        returns 0.0 and the trigger will not fire. This is intentional — a box
        with no historical baseline provides no context for "above average" density.

    2. Transponder_Deactivation_Alert (impact_level=4):
        Fires when any aircraft in a tension-zone box has a null position while
        apparently airborne (transponder_silence_events > 0 AND the box is in
        _OPENSKY_TENSION_ZONES). The Silver Job counts these aircraft; we read
        the pre-computed value from metadata_extension here.

    anomaly_score:
        Set to max(0.0, min(1.0, change_30d)) — a 0–1 normalized indicator of
        density deviation. 0.30 change_30d → 0.30 anomaly_score. Capped at 1.0
        for extreme spikes. Zero on decrease or no deviation. Updated here so it
        is consistent with the trigger evaluation in this function.

    Cold-start guard (is_new_market):
        When price_history was empty, compute_momentum_block() sets
        is_new_market=True. On the first observation there is no baseline to
        contextualise the density — all triggers are suppressed. Same design
        as OpenWeather (task_plan.md design decision 2026-04-04).

    Multiple triggers can fire simultaneously (independent checks — not
    mutually exclusive). impact_level = max() across all fired triggers.

    Args:
        gold_record: Gold Structured Metric produced by build_gold_structured_metric().
                     Modified deep-copy is returned — original is never mutated.
        momentum:    Computed momentum_block from compute_momentum_block().
                     Used for cold-start guard and Aerial_Escalation_Risk threshold.

    Returns:
        Deep-copy of gold_record with metadata_extension enriched with:
            anomaly_flags    (list[str])  — empty if no triggers fired
            anomaly_score    (float)      — density deviation magnitude (0–1)
            impact_level     (int 1–5)    — max of all fired trigger levels; 1 if none
            trigger_reason   (str|None)   — pipe-separated reasons; None if no trigger
    """
    result = copy.deepcopy(gold_record)
    meta   = result.setdefault("metadata_extension", {})

    anomaly_flags:   list[str] = []
    trigger_reasons: list[str] = []
    impact_level = 1   # default: no trigger fired

    # Cold-start guard — suppress all triggers on the first observation.
    # is_new_market=True means price_history was empty; no 30-day baseline exists.
    if momentum.get("is_new_market", False):
        meta["anomaly_flags"]   = []
        meta["anomaly_score"]   = 0.0
        meta["impact_level"]    = 1
        meta["trigger_reason"]  = None
        return result

    change_30d        = momentum.get("change_30d", 0.0)
    bounding_box_id   = meta.get("bounding_box_id", "")
    silence_events    = int(meta.get("transponder_silence_events", 0))

    # --- Trigger 1: Aerial_Escalation_Risk ---
    # change_30d is fractional: 0.30 = 30% above 30-day baseline count.
    if change_30d > _OPENSKY_DENSITY_THRESHOLD:
        anomaly_flags.append("Aerial_Escalation_Risk")
        trigger_reasons.append(
            f"Aircraft density change_30d={change_30d * 100:.1f}% "
            f"exceeds {_OPENSKY_DENSITY_THRESHOLD * 100:.0f}% threshold for "
            f"box={bounding_box_id}."
        )
        impact_level = max(impact_level, 4)

    # --- Trigger 2: Transponder_Deactivation_Alert ---
    # Only fires for tension-zone boxes — D.C. airspace excluded (see module comment).
    if silence_events > 0 and bounding_box_id in _OPENSKY_TENSION_ZONES:
        anomaly_flags.append("Transponder_Deactivation_Alert")
        trigger_reasons.append(
            f"{silence_events} aircraft with null position detected in "
            f"tension zone box={bounding_box_id} — potential covert operation "
            "indicator (Section B.7)."
        )
        impact_level = max(impact_level, 4)

    # anomaly_score: normalized density deviation (0–1).
    # Negative change (density dropped) → 0.0. Values above 1.0 are capped.
    anomaly_score = max(0.0, min(1.0, change_30d))

    trigger_reason = " | ".join(trigger_reasons) if trigger_reasons else None

    meta["anomaly_flags"]  = anomaly_flags
    meta["anomaly_score"]  = round(anomaly_score, 6)
    meta["impact_level"]   = impact_level
    meta["trigger_reason"] = trigger_reason

    return result


def process_opensky_gold_message(
    silver_metric: dict,
    price_history: list[tuple[str, float]],
    _now: Optional[datetime] = None,
) -> tuple[str, dict]:
    """
    Orchestrate OpenSky Gold enrichment for one Silver Structured Metric.

    Three-step pipeline (no OpenAI — fully deterministic, Section 4.2B):
        1. compute_momentum_block() — fractional change_24h/7d/30d from per-box
           keyed history (aircraft_density_count values).
        2. build_gold_structured_metric() — merge Silver + momentum, promote
           layer to "Gold".
        3. apply_opensky_automation_triggers() — adds anomaly_flags, anomaly_score,
           impact_level, trigger_reason to metadata_extension.

    Cold-start behaviour:
        When price_history=[], compute_momentum_block() returns is_new_market=True
        and all deltas=0.0. apply_opensky_automation_triggers() detects
        is_new_market=True and suppresses all triggers — no spurious alerts on
        the first observation for each box (task_plan.md design decision 2026-04-04).

    Note on persistence: the Flink OpenSkyGoldMetricsFunction (Phase 5) calls
    momentum_vault.insert() AFTER this function returns. Keeping persistence out
    of this function ensures it is testable at Gate 2 without a live DB
    (Section 4.4 Mock-Driven Development).

    Args:
        silver_metric: Silver Structured Metric dict conforming to Section C.2,
                       produced by silver_job.map_opensky_to_silver().
        price_history: Per-box observation history from Flink ValueState.
                       List of (timestamp_utc_iso, aircraft_density_count) tuples
                       in chronological order (oldest first).
                       Pass [] on first observation — compute_momentum_block()
                       handles empty history (is_new_market=True, all deltas=0.0).
        _now:          Override current time for deterministic test assertions
                       (same _now injection pattern as FRED, GoogleTrends, OpenWeather).

    Returns:
        (GOLD_STRUCTURED_METRICS, gold_metric) — always succeeds for valid Silver
        input. OpenSky Gold enrichment has no failure paths; all operations are
        deterministic math with no external I/O.
    """
    set_trace_id(silver_metric.get("canonical_event_id", silver_metric.get("bronze_ref", "")))
    current_value = float(
        silver_metric.get("data_point", {}).get("current_value", 0.0)
    )
    momentum    = compute_momentum_block(current_value, price_history, _now=_now)
    gold_record = build_gold_structured_metric(silver_metric, momentum)
    gold_record = apply_opensky_automation_triggers(gold_record, momentum)
    return GOLD_STRUCTURED_METRICS, gold_record


# ==========================================================
# Flink Pipeline — requires PyFlink (Docker container only)
# ==========================================================

# ==========================================================
# Semantic Rescue — module-level helper (Phase 7B, §4.1A)
# ==========================================================

def compute_semantic_rescue(
    silver_doc: dict,
    openai_client: Any,
    sniper_ref_vec: Any,   # np.ndarray, typed as Any to avoid numpy import at module level
    threshold: float,
) -> tuple[bool, float]:
    """
    Embed silver_doc text and compare cosine similarity to the sniper ref vector.

    Returns (rescued, similarity_score).

    The L2-normalised reference vector reduces cosine similarity to a dot
    product: sim = dot(article_vec / ||article_vec||, ref_vec).

    Why title + lead + truncated body: highest signal-to-noise ratio. Limiting
    body to 512 chars bounds cost to ~100-200 tokens per rescue call (B1).

    Raises:
        Any openai API exception — callers route to DLQ (B7, §3.5).

    References: Phase 7B B1, B2, §4.1A
    """
    import numpy as np

    text = " ".join(filter(None, [
        silver_doc.get("title", ""),
        silver_doc.get("inverted_pyramid_lead", ""),
        (silver_doc.get("full_text_raw", "") or "")[:512],
    ])).strip()

    if not text:
        return False, 0.0

    response = openai_client.embeddings.create(
        model="text-embedding-3-small",
        input=[text],
    )
    # Cost capture on BOTH branches (7B.5-I T4, approved P4): the embed is
    # paid for whether the article is promoted or dropped — drop-path rows
    # joined to filter_rejects via trace_id quantify the filter's wasted
    # spend (§0 goal 2).
    record_usage(
        "text-embedding-3-small", response,
        site="rescue_embed",
        source_name=silver_doc.get("source_name", ""),
        trace_id=silver_doc.get("canonical_event_id", ""),
    )
    article_vec = np.array(response.data[0].embedding, dtype=np.float32)
    norm = float(np.linalg.norm(article_vec))
    if norm == 0.0:
        return False, 0.0

    # Dot product with pre-normalised ref vector = cosine similarity
    similarity = float(np.dot(article_vec / norm, sniper_ref_vec))
    return similarity >= threshold, similarity


def apply_rescue_outcome(
    silver_doc: dict,
    rescued: bool,
    similarity: float,
    *,
    capture_enabled: bool = False,
    run_id: str = "",
) -> bool:
    """
    Apply the semantic-rescue outcome to a sniper-failed doc (Phase 7B.5-I, T3).

    Module-level pure function (PyFlink 1.19 pattern, plan §5.5) so the
    promote/drop handling is Gate-2-testable without a Flink runtime — the
    same reason compute_semantic_rescue() lives at module level. Extracted
    from GlobalNewsGoldFunction.process_element with ZERO change to the
    filter decision itself: `rescued` is decided by compute_semantic_rescue()
    before this function runs, and the return value depends only on it —
    never on capture_enabled (the T6 invariance guard).

    Promote path: flips is_high_signal (pre-existing behavior) and threads
    the cosine onto the doc as `rescue_cosine`, which knowledge_vault.archive()
    persists (§2.2) — sniper-passed docs never enter this function, so their
    vault rows keep rescue_cosine NULL. The validator ignores unknown keys
    (utils/validators._check_fields checks required fields only), so the new
    key is schema-safe.

    Drop path: when capture_enabled, retains the reject via
    persistence/filter_rejects.insert_reject() — which is FAIL-OPEN, so a
    capture failure can never turn a clean drop into a processing error.

    Args:
        silver_doc:      The sniper-failed Silver document (mutated on promote).
        rescued:         Decision from compute_semantic_rescue().
        similarity:      Cosine score from compute_semantic_rescue().
        capture_enabled: settings.REJECT_CAPTURE_ENABLED, read at job startup.
        run_id:          settings.RUN_ID collection-run tag ("" → NULL).

    Returns:
        True  — doc survives (promoted); caller continues to kv_archive + Gold.
        False — doc is dropped; caller returns (no kv_archive, no Gold).
    """
    if rescued:
        silver_doc["is_high_signal"] = True
        silver_doc["rescue_cosine"] = similarity
        return True

    if capture_enabled:
        # Lazy import: keeps module import free of DB deps (same pattern as
        # the persistence imports inside process_element).
        from persistence.filter_rejects import insert_reject
        insert_reject(silver_doc, similarity, run_id=run_id)
    return False


if PYFLINK_AVAILABLE:

    class PolymarketGoldSocialFunction(ProcessFunction):
        """
        Flink ProcessFunction — Social Pulse Gold enrichment.

        Wraps process_social_pulse_message() and handles:
            - Lazy OpenAI client creation (once per task slot in open())
            - persistence/social_vectors.insert() call after enrichment
            - DLQ side output on any failure

        Why client is created in open() not process_element():
        Establishing an HTTP connection on every message would dominate
        processing time. open() is called once per task slot lifetime.
        """

        def open(self, runtime_context):
            # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
            from utils.openai_client import get_openai_client
            from config.settings import OPENAI_API_KEY
            self._openai_client = get_openai_client(api_key=OPENAI_API_KEY)

        def process_element(self, value: str, _ctx: ProcessFunction.Context):
            from persistence.social_vectors import insert as sv_insert
            from persistence.social_vault import (
                insert as sv_archive,
                exists_by_content_hash,
                fetch_social_id_by_content_hash,
            )

            try:
                silver_social = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                dlq = _dlq_record({}, [f"JSON parse error: {exc}"], "deserialise")
                yield (DLQ_TAG, json.dumps(dlq))
                return

            # Archive raw Silver record to social_vault and capture social_id.
            # social_id is wired into gold_record["metadata"]["silver_data_ref"]
            # so the RAG agent can drill down from the Gold vector to the raw
            # Silver comments in social_vault (Gap 1 fix, Sprint 11, Section 5.1).
            social_id: str | None = None
            # Phase 9.5 Stage B Item 1b: retry transient DB errors (DNS race,
            # Postgres restart) before falling through to the non-fatal warning.
            try:
                from utils.retry import retry_on_transient
                content_hash = silver_social.get("content_hash", "")
                if content_hash and exists_by_content_hash(content_hash):
                    # Batch already archived — look up existing social_id rather
                    # than re-inserting, so silver_data_ref is always populated.
                    social_id = fetch_social_id_by_content_hash(content_hash)
                else:
                    social_id = retry_on_transient(
                        sv_archive, silver_social,
                        op_name="social_vault.archive",
                    )
            except Exception as exc:
                # Non-fatal: archive failure must not block Gold enrichment.
                # silver_data_ref will remain None (unknown) — correct sentinel.
                logger.warning("[gold/flink] social_vault.insert failed: %s", exc)

            source_name = silver_social.get("source_name", "")
            if source_name == "hackernews":
                topic, record = process_hackernews_gold_message(
                    silver_social,
                    openai_client=self._openai_client,
                )
            else:
                topic, record = process_social_pulse_message(
                    silver_social,
                    openai_client=self._openai_client,
                )

            if topic is None:
                return  # Intentional empty-batch skip

            if topic == DEAD_LETTER_QUEUE:
                yield (DLQ_TAG, json.dumps(record))
                return

            # Wire the correct social_vault.social_id into silver_data_ref.
            # If archiving failed above, social_id is None and silver_data_ref
            # correctly remains None (unknown reference, not a misleading UUID).
            if social_id:
                record["metadata"]["silver_data_ref"] = social_id

            # Persist Gold vector to PostgreSQL social_vectors (Section 5.2)
            # Phase 9.5 Stage B Item 1b: retry transient DB errors.
            try:
                from utils.retry import retry_on_transient
                retry_on_transient(
                    sv_insert, record,
                    op_name="social_vectors.insert",
                )
            except Exception as exc:
                logger.error("[gold/flink] social_vectors.insert failed: %s", exc)
                yield (DLQ_TAG, json.dumps(
                    _dlq_record(
                        record, [f"DB insert error: {exc}"], "social_vectors_insert"
                    )
                ))
                return

            yield ("main", json.dumps(record))

    class PolymarketGoldMetricsFunction(KeyedProcessFunction):
        """
        Flink KeyedProcessFunction — Structured Metrics Gold enrichment.

        Keyed on canonical_event_id so each asset accumulates its own
        independent price history in ValueState (Section 4.2B).

        State schema: JSON-serialized list of [timestamp_utc_iso, value]
        pairs, kept in chronological order. Capped at _MAX_HISTORY entries
        (≈10,000) to bound per-key state size while covering 30d+ of history
        at standard 5-minute polling frequency.

        Why ValueState[str] (JSON) over ListState[Tuple]:
        PyFlink's TUPLE type requires type info for all constituent parts.
        JSON strings avoid that complexity at negligible serialization cost
        for these small payloads.
        """

        def open(self, runtime_context):
            from pyflink.datastream.state import ValueStateDescriptor
            from pyflink.common.typeinfo import Types
            self._history_state = runtime_context.get_state(
                ValueStateDescriptor("price_history", Types.STRING())
            )

        def _load_history(self) -> list[tuple[str, float]]:
            raw = self._history_state.value()
            if raw is None:
                return []
            return [tuple(entry) for entry in json.loads(raw)]

        def _save_history(self, history: list[tuple[str, float]]) -> None:
            self._history_state.update(json.dumps(history))

        def process_element(self, value: str, _ctx: KeyedProcessFunction.Context):
            try:
                silver_metric = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                dlq = _dlq_record({}, [f"JSON parse error: {exc}"], "deserialise")
                yield (DLQ_TAG, json.dumps(dlq))
                return

            history = self._load_history()
            source_name = silver_metric.get("core_identity", {}).get("source_name", "polymarket")
            if source_name == "fred":
                _, gold_record = process_fred_gold_message(silver_metric, history)
            elif source_name == "googletrends":
                _, gold_record = process_googletrends_gold_message(silver_metric, history)
            elif source_name == "openweather":
                _, gold_record = process_openweather_gold_message(silver_metric, history)
            elif source_name == "opensky":
                _, gold_record = process_opensky_gold_message(silver_metric, history)
            else:
                # Default: Polymarket price_update path
                _, gold_record = process_structured_metrics_message(silver_metric, history)

            # Persist Gold metric to momentum_vault TimescaleDB hypertable (Section 5.3).
            # ON CONFLICT (metric_id, timestamp_utc) DO NOTHING ensures Flink
            # exactly-once re-deliveries never create duplicate rows.
            #
            # Phase 9.5 Stage B Item 1b: wrap the insert in retry_on_transient
            # so a DNS-resolution failure during cluster scale-up (or a brief
            # Postgres restart) does not immediately drop the message to DLQ.
            # 5 attempts × exponential backoff (1s, 2s, 4s, 8s) covers a ~15s
            # transient window — long enough for a normal Postgres recovery,
            # short enough that a true outage degrades gracefully.
            # Permanent errors (schema mismatch, constraint violation) are
            # not retried by retry_on_transient and still route to DLQ on the
            # first failure (correct behaviour — they cannot self-heal).
            try:
                from persistence.momentum_vault import insert as mv_insert
                from utils.retry import retry_on_transient
                retry_on_transient(
                    mv_insert, gold_record,
                    op_name="momentum_vault_insert",
                )
            except Exception as exc:
                logger.error("[gold/flink] momentum_vault.insert failed: %s", exc)
                yield (DLQ_TAG, json.dumps(
                    _dlq_record(
                        silver_metric,
                        [f"momentum_vault insert error: {exc}"],
                        "momentum_vault_insert",
                    )
                ))
                return

            # Append current observation, cap history size
            current_ts    = silver_metric.get("data_point", {}).get("timestamp_utc", "")
            current_value = float(
                silver_metric.get("data_point", {}).get("current_value", 0.0)
            )
            history.append((current_ts, current_value))
            if len(history) > _MAX_HISTORY:
                history = history[-_MAX_HISTORY:]
            self._save_history(history)

            yield ("main", json.dumps(gold_record))

    def _extract_metrics_key(msg: str) -> str:
        """Extract canonical_event_id from a Silver Structured Metric JSON string."""
        try:
            return json.loads(msg).get("core_identity", {}).get("canonical_event_id", "")
        except Exception:
            return ""

    class GlobalNewsGoldFunction(ProcessFunction):
        """
        Flink ProcessFunction — Global News Gold enrichment for SILVER_GLOBAL_NEWS.

        Dispatches to the correct Gold function by source_name:
            "newsapi"  → process_newsapi_gold_message()
            "arxiv"    → process_arxiv_gold_message()
            "telegram" → process_telegram_gold_message()

        All three paths call GPT-4o Cognitive Metadata Extraction and produce
        a Gold Global Signal record (Section C.5), then persist to:
            knowledge_vault.archive(silver_doc)   — Section 5.1 Silver archive
            knowledge_vectors.insert(gold_record) — Section 5.2 vector store

        Why OpenAI in open(): same rationale as PolymarketGoldSocialFunction —
        HTTP connection setup once per task slot, not per message (Section 4.2A).
        """

        def open(self, runtime_context):
            # Phase 9.5 Stage B Item 2: centralised factory (max_retries=5).
            from utils.openai_client import get_openai_client
            from config.settings import (
                OPENAI_API_KEY,
                GOLD_SEMANTIC_RESCUE_THRESHOLD,
                REJECT_CAPTURE_ENABLED,
                RUN_ID,
            )
            from processing.keyword_sniper import SNIPER_REFERENCE_VECTOR_PATH
            import numpy as np

            self._openai_client = get_openai_client(api_key=OPENAI_API_KEY)
            self._rescue_threshold = GOLD_SEMANTIC_RESCUE_THRESHOLD

            # Phase 7B.5-I (§2.6): reject retention is flag-gated and read at
            # startup — toggling requires a pod restart only (env-only change).
            # Logged so a collection run's capture state is verifiable in the
            # job logs before any traffic flows (T8 checklist).
            self._reject_capture_enabled = REJECT_CAPTURE_ENABLED
            self._run_id = RUN_ID
            logger.info(
                "[gold/flink] reject capture %s (run_id=%r)",
                "ENABLED" if self._reject_capture_enabled else "disabled",
                self._run_id,
            )

            # Load sniper reference vector — hard-fail at startup if missing (B4).
            # Prevents silent regression to keyword-only filtering.
            if not SNIPER_REFERENCE_VECTOR_PATH.exists():
                logger.error(
                    "[gold/flink] sniper_reference_vector.npy not found at %s. "
                    "Run processing/build_sniper_reference_vector.py and commit "
                    "the .npy file (Phase 7B T7B.6).",
                    SNIPER_REFERENCE_VECTOR_PATH,
                )
                raise FileNotFoundError(
                    f"sniper_reference_vector.npy missing: {SNIPER_REFERENCE_VECTOR_PATH}"
                )
            self._sniper_ref_vec = np.load(str(SNIPER_REFERENCE_VECTOR_PATH)).astype(np.float32)
            logger.info(
                "[gold/flink] Sniper reference vector loaded — shape=%s threshold=%.2f",
                self._sniper_ref_vec.shape, self._rescue_threshold,
            )

        def _semantic_rescue(self, silver_doc: dict) -> tuple[bool, float]:
            """Delegate to module-level compute_semantic_rescue() for testability."""
            return compute_semantic_rescue(
                silver_doc,
                self._openai_client,
                self._sniper_ref_vec,
                self._rescue_threshold,
            )

        def process_element(self, value: str, _ctx: ProcessFunction.Context):
            from persistence.knowledge_vault import archive as kv_archive
            from persistence.knowledge_vectors import insert as kv_insert

            try:
                silver_doc = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                dlq = _dlq_record({}, [f"JSON parse error: {exc}"], "deserialise")
                yield (DLQ_TAG, json.dumps(dlq))
                return

            # Two-stage SNR gate: keyword sniper (run at Silver) + semantic rescue.
            # Articles that fail BOTH are dropped — no kv_archive, no Gold (B2, §4.1A).
            if not silver_doc.get("is_high_signal", False):
                url = silver_doc.get("original_url", "unknown")[:80]
                try:
                    rescued, similarity = self._semantic_rescue(silver_doc)
                except Exception as exc:
                    # Embedding API failure — cannot make a sound decision; DLQ (B7)
                    logger.error(
                        "[gold/semantic_rescue] embedding API error url=%s: %s — DLQ",
                        url, exc,
                    )
                    yield (DLQ_TAG, json.dumps(
                        _dlq_record(silver_doc, [f"embedding API error: {exc}"], "semantic_rescue")
                    ))
                    return

                # Phase 7B.5-I T3: promote/drop handling extracted to the
                # module-level apply_rescue_outcome() (Gate-2-testable without
                # PyFlink). Promote threads rescue_cosine onto the doc for
                # kv_archive; drop captures the reject when flag-gated ON.
                survived = apply_rescue_outcome(
                    silver_doc, rescued, similarity,
                    capture_enabled=self._reject_capture_enabled,
                    run_id=self._run_id,
                )
                if survived:
                    logger.info(
                        "[gold/semantic_rescue] promoted url=%s score=%.4f threshold=%.2f",
                        url, similarity, self._rescue_threshold,
                    )
                else:
                    logger.info(
                        "[gold/semantic_rescue] dropped url=%s score=%.4f threshold=%.2f",
                        url, similarity, self._rescue_threshold,
                    )
                    return  # no kv_archive, no Gold

            # Archive Silver doc to knowledge_vault.
            # Only reached by articles that passed the two-stage gate above.
            # Returns doc_id (UUID string) or None if document_hash already exists.
            # Non-fatal: archive failure must not block Gold enrichment.
            doc_id: str | None = None
            # Phase 9.5 Stage B Item 1b: retry transient DB errors.
            try:
                from utils.retry import retry_on_transient
                doc_id = retry_on_transient(
                    kv_archive, silver_doc,
                    op_name="knowledge_vault.archive",
                )
            except Exception as exc:
                logger.warning("[gold/flink] knowledge_vault.archive failed: %s", exc)

            # Dispatch to the correct Gold process function by source_name.
            source_name = silver_doc.get("source_name", "")
            if source_name == "newsapi":
                topic, record = process_newsapi_gold_message(
                    silver_doc, openai_client=self._openai_client
                )
            elif source_name == "arxiv":
                topic, record = process_arxiv_gold_message(
                    silver_doc, openai_client=self._openai_client
                )
            elif source_name == "telegram":
                topic, record = process_telegram_gold_message(
                    silver_doc, openai_client=self._openai_client
                )
            else:
                logger.warning(
                    "[gold/flink] GlobalNewsGoldFunction: unknown source_name=%s — routing to DLQ",
                    source_name,
                )
                yield (DLQ_TAG, json.dumps(
                    _dlq_record(silver_doc, [f"unknown source_name: {source_name}"], "dispatch")
                ))
                return

            if topic is None:
                return  # is_high_signal=False guard — intentional skip, zero token spend

            if topic == DEAD_LETTER_QUEUE:
                yield (DLQ_TAG, json.dumps(record))
                return

            # Wire Silver doc_id into gold record for RAG drill-down (Section 5.1).
            if doc_id and record.get("metadata"):
                record["metadata"]["silver_data_ref"] = doc_id

            # Persist Gold vector to PostgreSQL knowledge_vectors (Section 5.2).
            # Phase 9.5 Stage B Item 1b: retry transient DB errors.
            try:
                from utils.retry import retry_on_transient
                retry_on_transient(
                    kv_insert, record,
                    op_name="knowledge_vectors.insert",
                )
            except Exception as exc:
                logger.error("[gold/flink] knowledge_vectors.insert failed: %s", exc)
                yield (DLQ_TAG, json.dumps(
                    _dlq_record(
                        record, [f"DB insert error: {exc}"], "knowledge_vectors_insert"
                    )
                ))
                return

            yield ("main", json.dumps(record))

    def build_pipeline(env: "StreamExecutionEnvironment") -> None:
        """
        Wire the Gold Flink pipeline for all three Silver topics.

        Three independent sub-pipelines share the same environment:
            A. Social Pulse:    SILVER_SOCIAL_PULSE       → PolymarketGoldSocialFunction
                                → GOLD_SOCIAL_PULSE + DLQ
                                (Polymarket comments + HackerNews, dispatched by source_name)
            B. Structured Metrics: SILVER_STRUCTURED_METRICS → PolymarketGoldMetricsFunction
                                → GOLD_STRUCTURED_METRICS + DLQ
                                (Polymarket, FRED, GoogleTrends, OpenWeather, OpenSky,
                                 dispatched by source_name)
            C. Global News:     SILVER_GLOBAL_NEWS        → GlobalNewsGoldFunction
                                → GOLD_GLOBAL_NEWS + DLQ
                                (NewsAPI, ArXiv, Telegram, dispatched by source_name)

        Args:
            env: StreamExecutionEnvironment configured by the caller.
        """
        from config.settings import KAFKA_BOOTSTRAP_SERVERS

        def _kafka_source(topic: str, group_id: str) -> "KafkaSource":
            return (
                KafkaSource.builder()
                .set_bootstrap_servers(KAFKA_BOOTSTRAP_SERVERS)
                .set_topics(topic)
                .set_group_id(group_id)
                .set_starting_offsets(KafkaOffsetsInitializer.earliest())
                .set_value_only_deserializer(SimpleStringSchema())
                .build()
            )

        def _kafka_sink(topic: str) -> "KafkaSink":
            return (
                KafkaSink.builder()
                .set_bootstrap_servers(KAFKA_BOOTSTRAP_SERVERS)
                .set_record_serializer(
                    KafkaRecordSerializationSchema.builder()
                    .set_topic(topic)
                    .set_value_serialization_schema(SimpleStringSchema())
                    .build()
                )
                .build()
            )

        _tuple_type = Types.TUPLE([Types.STRING(), Types.STRING()])

        def _tag_split(stream, tag: str):
            """Filter by tag then strip the tag field, leaving a plain JSON string."""
            return (
                stream
                .filter(lambda x: x[0] == tag)
                .map(lambda x: x[1], output_type=Types.STRING())
            )

        # --- A. Social Pulse pipeline ---
        # Handles Polymarket comments + HackerNews stories via source_name dispatch.
        social_stream = env.from_source(
            _kafka_source(SILVER_SOCIAL_PULSE, "flink-gold-social"),
            WatermarkStrategy.no_watermarks(),
            "silver-social-pulse-source",
        )
        social_processed = social_stream.process(
            PolymarketGoldSocialFunction(),
            output_type=_tuple_type,
        )
        _tag_split(social_processed, "main")   .sink_to(_kafka_sink(GOLD_SOCIAL_PULSE))
        _tag_split(social_processed, DLQ_TAG)  .sink_to(_kafka_sink(DEAD_LETTER_QUEUE))

        # --- B. Structured Metrics pipeline ---
        # Handles Polymarket, FRED, GoogleTrends, OpenWeather, OpenSky via source_name dispatch.
        metrics_stream = env.from_source(
            _kafka_source(SILVER_STRUCTURED_METRICS, "flink-gold-metrics"),
            WatermarkStrategy.no_watermarks(),
            "silver-structured-metrics-source",
        )
        metrics_processed = (
            metrics_stream
            .key_by(_extract_metrics_key)
            .process(PolymarketGoldMetricsFunction(), output_type=_tuple_type)
        )
        _tag_split(metrics_processed, "main")  .sink_to(_kafka_sink(GOLD_STRUCTURED_METRICS))
        _tag_split(metrics_processed, DLQ_TAG) .sink_to(_kafka_sink(DEAD_LETTER_QUEUE))

        # --- C. Global News pipeline ---
        # Handles NewsAPI, ArXiv, Telegram via source_name dispatch.
        global_news_stream = env.from_source(
            _kafka_source(SILVER_GLOBAL_NEWS, "flink-gold-global-news"),
            WatermarkStrategy.no_watermarks(),
            "silver-global-news-source",
        )
        global_news_processed = global_news_stream.process(
            GlobalNewsGoldFunction(),
            output_type=_tuple_type,
        )
        _tag_split(global_news_processed, "main")   .sink_to(_kafka_sink(GOLD_GLOBAL_NEWS))
        _tag_split(global_news_processed, DLQ_TAG)  .sink_to(_kafka_sink(DEAD_LETTER_QUEUE))


def main() -> None:
    """
    Entry point for Flink job submission inside the Docker container.

    Submit via Flink CLI:
        flink run -py processing/gold_job.py

    Requires PyFlink in the apache/flink:1.19-java11 image (Section 8.2).
    Running locally without PyFlink will print an informative error.
    """
    if not PYFLINK_AVAILABLE:
        print(
            "[gold_job] PyFlink is not installed in this environment.\n"
            "This job must be submitted to the Flink JobManager container.\n"
            "See docker-compose.yml — flink-jobmanager service (Section 8.2)."
        )
        return

    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(2)
    build_pipeline(env)
    env.execute("anizai-gold-all-sources")


if __name__ == "__main__":
    main()
