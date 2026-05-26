"""
Silver Job — Flink Standardization & Dual-Persistence (Section 4.1).

Consumes raw Bronze envelopes and produces validated Silver records on the
correct Silver family topic. Four source branches are implemented:

Polymarket (dual payload_type in one Bronze topic):
    price_update → map_price_update_to_silver() → process.silver.structured_metrics
    comment      → map_comment_to_silver()       → process.silver.social_pulse
    invalid      → dead-letter-queue              (never silently dropped, Section 3.5)

FRED (single payload_type — all observations are structured_metrics):
    observation  → map_fred_observation_to_silver() → process.silver.structured_metrics
    invalid      → dead-letter-queue

NewsAPI (single routing path — all articles are Full-Text Documents):
    article      → map_newsapi_article_to_silver() → process.silver.global_news
    invalid      → dead-letter-queue

ArXiv (single routing path — all papers are Full-Text Documents):
    paper        → map_arxiv_paper_to_silver()    → process.silver.global_news
    invalid      → dead-letter-queue

Telegram (single routing path — each message is a micro-article):
    message      → map_telegram_message_to_silver() → process.silver.global_news
    invalid      → dead-letter-queue
    Note: Telegram routes to global_news (not social_pulse). Silver store is
    knowledge_vault; vector target is knowledge_vectors (C.5, entry_type='direct_message').
    See Part 1.5 Vector Persistence Map in task_plan.md.

Architecture note — pure transform functions vs. Flink wiring:
    All transformation logic lives in module-level functions (pure Python,
    no PyFlink dependency). The Flink pipeline wiring is isolated in
    build_pipeline() and main(), guarded by PYFLINK_AVAILABLE.

    Why this split: Gate 2 tests (Section 9.3) must run without a live
    Flink cluster. Importing this module in a pytest context must not fail
    even though PyFlink is not installed locally (it runs inside Docker only,
    Section 8.2). The transform functions can be called directly by tests
    using mock payloads from tests/mocks/.

Flink operational settings (Section 4.3):
    - Checkpointing every 60 s, EXACTLY_ONCE semantics.
    - Restart strategy: fixed-delay, 5 attempts.
    - momentum_block values are stubbed to 0.0 at Silver layer;
      actual change_24h/7d/30d are computed by the Gold Job via keyed
      state management (Section 4.2B).

References:
    - Section 4.1:  Silver Job specification
    - Section 4.1C: SHA-256 deduplication
    - Section 4.3:  Checkpointing / exactly-once / DLQ
    - Section 3.5:  Dead-Letter Queue routing
    - Section C.2:  Silver Structured Metric schema (price_update + FRED paths)
    - Section C.3:  Silver Social schema — Polymarket pattern (comment path)
    - Section B.3:  FRED parameters (series_id, release_priority, seasonal_adjustment)
    - Section B.8:  Polymarket parameters (whale_alert, metadata_extension fields)
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timezone
from typing import Any, Optional

from config.kafka_topics import (
    BRONZE_ARXIV,
    BRONZE_FRED,
    BRONZE_GOOGLETRENDS,
    BRONZE_HACKERNEWS,
    BRONZE_NEWSAPI,
    BRONZE_OPENSKY,
    BRONZE_OPENWEATHER,
    BRONZE_POLYMARKET,
    BRONZE_TELEGRAM,
    DEAD_LETTER_QUEUE,
    SILVER_GLOBAL_NEWS,
    SILVER_SOCIAL_PULSE,
    SILVER_STRUCTURED_METRICS,
)
from processing.deduplication import hash_document, hash_social_batch
from processing.keyword_sniper import snipe, snipe_article
from processing.translation import (
    needs_translation,
    translate_to_english,
    translate_keyword_to_english,
)
from utils.validators import (
    validate_bronze_payload,
    validate_envelope,
    validate_silver_document,
    validate_silver_social,
    validate_silver_structured_metric,
    route_to_dlq,
)
from utils.logging_config import setup_logging, set_trace_id

logger = logging.getLogger(__name__)
setup_logging()  # structured JSON + 1% INFO sampling + trace_id injection (Section 7.2)

# ==========================================================
# PyFlink — optional import (Docker container only, Section 8.2)
# ==========================================================
# PyFlink is NOT installed in the local venv (removed from requirements.txt).
# It is pre-installed inside the apache/flink:1.19-java11 Docker image.
# All code below that touches pyflink is gated by PYFLINK_AVAILABLE so
# this module remains importable in local pytest environments.

try:
    from pyflink.common import SimpleStringSchema, Types, WatermarkStrategy
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.connectors.kafka import (
        KafkaOffsetsInitializer,
        KafkaRecordSerializationSchema,
        KafkaSink,
        KafkaSource,
    )
    from pyflink.datastream.functions import ProcessFunction
    PYFLINK_AVAILABLE = True
except ImportError:
    PYFLINK_AVAILABLE = False


# OutputTag constants — used to side-route DLQ and social_pulse records
# from the main structured_metrics stream inside the Flink ProcessFunction.
# Defined at module level so Gate 2 tests can reference them without Flink.
DLQ_TAG    = "dlq"
SOCIAL_TAG = "social_pulse"


# ==========================================================
# Transform Functions — Pure Python, no PyFlink dependency
# ==========================================================

def map_price_update_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map a Polymarket price_update raw payload to the Silver Structured Metric
    schema (Section C.2).

    Why momentum_block is stubbed: change_24h / change_7d / change_30d require
    the previous N price observations for the same asset. At the Silver layer
    there is no state — these are computed by the Gold Job's keyed state
    operator (Section 4.2B). Stubs of 0.0 pass the Silver validator and signal
    to the Gold Job that deltas are not yet calculated.

    Args:
        raw:      The raw_payload dict from the Bronze envelope (payload_type="price_update").
        envelope: The full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Structured Metric dict conforming to Section C.2.
    """
    return {
        "schema_version": "2.0",
        "layer":          "Silver",
        "entity_type":    "Structured_Metric",
        "core_identity": {
            "metric_id":             str(uuid.uuid4()),
            "canonical_event_id":    envelope.get("event_id", ""),
            "parent_id":             raw.get("market_id", ""),
            "source_name":           "polymarket",
            # asset_id for WebSocket events; condition_id for REST snapshots
            "external_reference_id": (
                raw.get("asset_id") or raw.get("condition_id", "")
            ),
        },
        "data_point": {
            "current_value": float(raw.get("price", 0.0)),
            "unit":          "USD",
            "status":        "active",
            "timestamp_utc": (
                raw.get("timestamp")
                or envelope.get("producer_timestamp", "")
            ),
        },
        # Stubs — Gold Job computes actual deltas via keyed state (Section 4.2B)
        "momentum_block": {
            "change_24h":    0.0,
            "change_7d":     0.0,
            "change_30d":    0.0,
            "is_new_market": False,
        },
        # Polymarket metadata_extension fields (Section C.2 table).
        # `question` is propagated only when present in raw (REST-snapshot path
        # — `_fetch_market_prices()` in polymarket_producer.py:208). WebSocket
        # `last_trade` events carry no question text in their raw payload, so
        # this resolves to "" for those rows. Added in Sprint 22 T22.1 D1 as a
        # prerequisite for the agent-side fuzzy-match resolver in
        # persistence/momentum_vault.find_polymarket_market_by_question:
        # without this propagation the resolver has nothing to match against.
        "metadata_extension": {
            "liquidity_pool_tvl": float(raw.get("liquidity_usd", 0.0)),
            "bid_ask_spread":     0.0,
            "24h_volume":         float(raw.get("volume_24h_usd", 0.0)),
            "is_divergent":       False,
            "whale_alert":        bool(raw.get("whale_alert", False)),
            "resolution_rules":   "",
            "question":           raw.get("question", ""),
        },
    }


def map_comment_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map a Polymarket comment batch payload to the Silver Social schema
    (Section C.3 — Polymarket volume-centric pattern).

    A SHA-256 content_hash is computed over the comment IDs so the
    persistence layer can skip re-ingestion of previously seen batches
    (Section 4.1C deduplication).

    Args:
        raw:      The raw_payload dict from the Bronze envelope (payload_type="comment").
        envelope: The full Bronze Message Envelope (provides trace lineage fields).

    Returns:
        Silver Social dict conforming to the Polymarket pattern in Section C.3.
        Required fields: source_name, ingested_at, market_id, raw_comments.
    """
    market_id    = raw.get("market_id", "")
    raw_comments = raw.get("raw_comments", [])

    return {
        "source_name":    "polymarket",
        "ingested_at":    envelope.get("producer_timestamp", ""),
        "market_id":      market_id,
        "question":       raw.get("question", ""),
        "raw_comments":   raw_comments,
        # SHA-256 of market_id + sorted comment IDs (Section 4.1C)
        "content_hash":   hash_social_batch(market_id, raw_comments),
        # Lineage bridge back to the Bronze record for replay (Section 3.2)
        "bronze_ref":     envelope.get("event_id", ""),
    }


# ==========================================================
# Dispatch — routes one envelope to (topic, silver_record)
# ==========================================================

def process_polymarket_message(
    envelope: dict,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single Polymarket Bronze envelope to the correct
    Silver topic.

    Decision tree:
        1. Envelope fails validate_envelope()  → (DEAD_LETTER_QUEUE, dlq_record)
        2. Payload fails validate_bronze_payload() → (DEAD_LETTER_QUEUE, dlq_record)
        3. payload_type == "price_update"
               → map_price_update_to_silver()
               → validate_silver_structured_metric()
               → pass: (SILVER_STRUCTURED_METRICS, silver_record)
               → fail: (DEAD_LETTER_QUEUE, dlq_record)
        4. payload_type == "comment", raw_comments is empty → (None, None)  [skip]
        5. payload_type == "comment"
               → map_comment_to_silver()
               → validate_silver_social()
               → pass: (SILVER_SOCIAL_PULSE, silver_record)
               → fail: (DEAD_LETTER_QUEUE, dlq_record)
        6. Unknown payload_type → (DEAD_LETTER_QUEUE, dlq_record)

    Args:
        envelope: Full Bronze Message Envelope dict (as produced by
                  build_bronze_message() in kafka_utils.py).

    Returns:
        (topic, record): topic is the Kafka topic string to emit to.
                         record is the Silver dict to publish.
                         (None, None) means the message is intentionally skipped
                         (empty comment batch — not an error).

    Why return a tuple instead of emitting directly: keeps this function
    pure and testable. The Flink ProcessFunction calls it and handles the
    actual Kafka sink routing.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/polymarket] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _dlq_record(envelope, env_result.errors, "envelope")

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/polymarket] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _dlq_record(envelope, bronze_result.errors, "bronze_payload")

    raw          = payload.get("raw_payload", {})
    payload_type = raw.get("payload_type", "")

    # --- Branch: price_update → structured_metrics ---
    if payload_type == "price_update":
        # Only last_trade WebSocket events carry a meaningful execution price
        # (Section B.8). price_change events reflect bid/ask order-book updates;
        # book events carry full order-book snapshots — neither represents an
        # executed trade. Allowing them through sets current_value=0.0, which
        # accumulates in Flink keyed state and corrupts momentum deltas.
        # Returning (None, None) signals an intentional skip (same pattern as
        # empty comment batches) — this is not a DLQ error.
        # REST snapshots (ingestion_mode="rest_snapshot") have no event_type and
        # carry supplementary metadata; they pass through unchanged.
        event_type     = raw.get("event_type", "")
        ingestion_mode = raw.get("ingestion_mode", "")
        if ingestion_mode == "websocket" and event_type != "last_trade":
            logger.debug(
                "[silver/polymarket] Skipping non-trade WebSocket event "
                "event_type=%r asset_id=%s",
                event_type, raw.get("asset_id", "unknown"),
            )
            return None, None   # intentional skip — not a DLQ error

        silver = map_price_update_to_silver(raw, envelope)
        result = validate_silver_structured_metric(silver)
        if not result.is_valid:
            logger.error(
                "[silver/polymarket] Silver metric validation failed: %s",
                result.errors,
            )
            return DEAD_LETTER_QUEUE, _dlq_record(envelope, result.errors, "silver_structured_metric")
        return SILVER_STRUCTURED_METRICS, silver

    # --- Branch: comment → social_pulse ---
    if payload_type == "comment":
        if not raw.get("raw_comments"):
            # Empty batch — Gamma API returned nothing for this market.
            # Not a schema error; skip without routing to DLQ.
            logger.debug(
                "[silver/polymarket] Empty comment batch for market_id=%s — skipping",
                raw.get("market_id", "unknown"),
            )
            return None, None

        silver = map_comment_to_silver(raw, envelope)
        result = validate_silver_social(silver)
        if not result.is_valid:
            logger.error(
                "[silver/polymarket] Silver social validation failed: %s",
                result.errors,
            )
            return DEAD_LETTER_QUEUE, _dlq_record(envelope, result.errors, "silver_social")
        return SILVER_SOCIAL_PULSE, silver

    # --- Fallthrough: unknown payload_type ---
    logger.warning(
        "[silver/polymarket] Unknown payload_type='%s' for event_id=%s",
        payload_type, envelope.get("event_id", "unknown"),
    )
    return DEAD_LETTER_QUEUE, _dlq_record(
        envelope,
        [f"Unknown payload_type: '{payload_type}'. Expected 'price_update' or 'comment'."],
        "dispatch",
    )


def _dlq_record(envelope: dict, errors: list[str], failed_stage: str) -> dict:
    """
    Build a dead-letter-queue record wrapping the original envelope and errors.

    Matches the DLQ envelope format produced by route_to_dlq() in validators.py
    so DLQ consumers have a consistent structure regardless of which job or
    stage produced the failure (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_POLYMARKET,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# FRED Silver Branch — Transform Functions
# ==========================================================

def map_fred_observation_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map a FRED observation raw payload to the Silver Structured Metric schema
    (Section C.2 — FRED row in the metadata_extension table).

    Why observation_date → timestamp_utc conversion: FRED publishes daily
    observations as "YYYY-MM-DD" date strings. The Silver schema requires a
    full ISO8601 UTC datetime for the momentum_vault's TIMESTAMPTZ partition
    key. Midnight UTC ("T00:00:00+00:00") is the canonical representation for
    a date-only FRED observation — there is no intra-day resolution for most
    FRED series (FEDFUNDS, CPIAUCSL, UNRATE, etc.).

    Why external_reference_id = series_id: the Gold Job's keyed-state momentum
    operator keys by (source_name, external_reference_id). Using series_id as
    the key ensures per-series state — "FEDFUNDS" history is isolated from
    "VIXCLS" history and never cross-contaminated (Section 4.2B).

    Why parent_id = "": FRED series have no parent concept. Polymarket uses
    parent_id for condition_id (the market that owns a token). FRED's atomic
    unit is the series itself — there is no hierarchy above series_id.

    Why status = "actual": FRED publishes only confirmed, official government
    or exchange data. There are no forecast or preliminary observations in the
    9 mandated series — the field signals data quality to the RAG agent.

    Why momentum_block stubs are 0.0: same rationale as Polymarket — computing
    change_24h/7d/30d requires prior observations for the same series_id.
    The Gold Job maintains per-series history in Flink ValueState and computes
    the real deltas (Section 4.2B).

    Args:
        raw:      The raw_payload dict from the Bronze envelope, as produced
                  by FREDProducer._build_raw_payload().
        envelope: The full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Structured Metric dict conforming to Section C.2.
    """
    series_id       = raw.get("series_id", "")
    observation_date = raw.get("observation_date", "")

    # Convert "YYYY-MM-DD" → ISO8601 UTC midnight for the TIMESTAMPTZ column.
    # If observation_date is already a full ISO string (unlikely from FRED but
    # defensive), pass it through unchanged.
    if observation_date and len(observation_date) == 10:
        timestamp_utc = f"{observation_date}T00:00:00+00:00"
    else:
        timestamp_utc = observation_date or envelope.get("producer_timestamp", "")

    return {
        "schema_version": "2.0",
        "layer":          "Silver",
        "entity_type":    "Structured_Metric",
        "core_identity": {
            "metric_id":             str(uuid.uuid4()),
            "canonical_event_id":    envelope.get("event_id", ""),
            "parent_id":             "",   # No parent concept for FRED series
            "source_name":           "fred",
            # series_id is the keyed-state key in the Gold Job's momentum operator
            "external_reference_id": series_id,
        },
        "data_point": {
            "current_value": float(raw.get("value", 0.0)),
            "unit":          raw.get("unit", ""),
            "status":        "actual",     # FRED publishes confirmed official data
            "timestamp_utc": timestamp_utc,
        },
        # Stubs — Gold Job computes actual deltas via keyed state (Section 4.2B)
        "momentum_block": {
            "change_24h":    0.0,
            "change_7d":     0.0,
            "change_30d":    0.0,
            "is_new_market": False,
        },
        # FRED metadata_extension fields (Section C.2 table — FRED row)
        "metadata_extension": {
            "series_id":          series_id,
            "observation_date":   observation_date,
            "release_priority":   int(raw.get("release_priority", 3)),
            "seasonal_adjustment": raw.get("seasonal_adjustment", ""),
        },
    }


def process_fred_message(
    envelope: dict,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single FRED Bronze envelope to SILVER_STRUCTURED_METRICS.

    FRED has only one routing path — every valid observation is a structured
    metric. There is no social or document branch for FRED (Section 3.1:
    BRONZE_FRED → SILVER_STRUCTURED_METRICS in BRONZE_TO_SILVER_ROUTING).

    Decision tree:
        1. Envelope fails validate_envelope()         → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()    → (DEAD_LETTER_QUEUE, dlq)
        3. raw_payload.value is missing or non-numeric → (DEAD_LETTER_QUEUE, dlq)
        4. map_fred_observation_to_silver() succeeds
           + validate_silver_structured_metric() passes → (SILVER_STRUCTURED_METRICS, silver)
        5. validate_silver_structured_metric() fails  → (DEAD_LETTER_QUEUE, dlq)

    Why validate value before mapping: the FRED sentinel "." should have been
    filtered by the producer. If one slips through (e.g., a replay of old Bronze
    messages), converting "." to float raises ValueError. Catching that here
    routes it to DLQ rather than crashing the Flink job (Section 3.5).

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/fred] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _fred_dlq_record(envelope, env_result.errors, "envelope")

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/fred] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _fred_dlq_record(envelope, bronze_result.errors, "bronze_payload")

    raw = payload.get("raw_payload", {})

    # --- Gate: numeric value guard ---
    # The producer filters FRED's "." sentinel, but replay of old Bronze
    # messages may include pre-filter records. Catch here, route to DLQ.
    try:
        float(raw.get("value", ""))
    except (TypeError, ValueError):
        errors = [
            f"raw_payload.value='{raw.get('value')}' is not numeric. "
            "FRED sentinel '.' must be filtered at ingestion (Section B.3)."
        ]
        logger.error("[silver/fred] Non-numeric value for series_id=%s: %s",
                     raw.get("series_id", "unknown"), errors)
        return DEAD_LETTER_QUEUE, _fred_dlq_record(envelope, errors, "value_guard")

    # --- Map to Silver ---
    silver = map_fred_observation_to_silver(raw, envelope)

    # --- Gate: Silver schema ---
    result = validate_silver_structured_metric(silver)
    if not result.is_valid:
        logger.error(
            "[silver/fred] Silver metric validation failed for series_id=%s: %s",
            raw.get("series_id", "unknown"), result.errors,
        )
        return DEAD_LETTER_QUEUE, _fred_dlq_record(envelope, result.errors, "silver_structured_metric")

    return SILVER_STRUCTURED_METRICS, silver


def _fred_dlq_record(envelope: dict, errors: list[str], failed_stage: str) -> dict:
    """
    Build a DLQ record for a failed FRED Bronze message.

    Separate from _dlq_record() to correctly tag source_topic=BRONZE_FRED
    instead of BRONZE_POLYMARKET, so DLQ operators can filter by source when
    diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_FRED,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# NewsAPI Silver Branch — Transform Functions
# ==========================================================

def map_newsapi_article_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map a NewsAPI article raw payload to the Silver Full-Text Document Store
    schema (Section C.3).

    Why full_text_raw uses content ‖ description fallback:
        NewsAPI truncates the 'content' field at ~200 chars on some plan tiers.
        When content is empty, description (the lede paragraph) is the best
        available body text. The Knowledge Vault stores whatever is available;
        the Gold Job embeds the executive_summary from OpenAI regardless of
        truncation (Section 4.2A).

    Why inverted_pyramid_lead = description:
        Section C.3 defines inverted_pyramid_lead as the first paragraph /
        lede sentence. NewsAPI's 'description' field maps exactly to this —
        it is always the summary/lede, regardless of how much 'content' is present.

    Why detected_entities is empty here:
        Entity extraction (actors, locations, weapon systems) is performed by
        the Gold Job's OpenAI Cognitive Metadata call (Section 4.2A).
        Leaving it as [] at Silver layer signals to the Gold Job that extraction
        has not yet run — it must never be None (validate_silver_document requires list).

    Why relevance_score comes from the Keyword Sniper:
        Section 4.1A mandates sniper scoring before heavy processing. The score
        is stored in the Silver document so the Gold Job can gate its OpenAI call
        without re-running the sniper. High-signal articles (score >= DEFAULT_THRESHOLD)
        proceed to embedding + enrichment; low-signal articles are stored in the
        Knowledge Vault for text search but skip the OpenAI token spend.

    Args:
        raw:      Bronze raw_payload dict from NewsAPIProducer._build_raw_payload().
        envelope: Full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Full-Text Document Store dict conforming to Section C.3.
    """
    url         = raw.get("url", "")
    description = raw.get("description", "")
    content     = raw.get("content", "")
    full_text   = content or description   # prefer content; fall back to lede

    # Keyword Sniper (Section 4.1A) — score gates OpenAI enrichment in Gold Job
    sniper = snipe_article(raw)

    # SHA-256 dedup hash (Section 4.1C) — Knowledge Vault checks this before write
    doc_hash = hash_document(full_text, url)

    source = raw.get("source") or {}

    return {
        # --- Silver Full-Text Document Store (Section C.3) ---
        "doc_id":                str(uuid.uuid4()),
        "document_hash":         doc_hash,
        "canonical_event_id":    envelope.get("event_id", ""),
        "full_text_raw":         full_text,
        "inverted_pyramid_lead": description,
        "source_name":           "newsapi",
        "original_url":          url,
        "author":                raw.get("author", ""),
        "publish_date":          raw.get("published_at", ""),
        "detected_entities":     [],          # populated by Gold Job (OpenAI)
        "relevance_score":       sniper.relevance_score,
        # --- NewsAPI-specific fields (consumed by knowledge_vault.py + Gold Job) ---
        "title":                 raw.get("title", ""),
        "source_display_name":   source.get("name", ""),
        "category":              raw.get("category", ""),
        "impact_boost":          bool(raw.get("impact_boost", False)),
        "impact_boost_reason":   raw.get("impact_boost_reason", ""),
        "sniper_keywords":       sniper.matched_keywords,
        "is_high_signal":        sniper.is_high_signal,
        "fetch_mode":            raw.get("fetch_mode", "pulse"),
        # --- Lineage (Section 3.2 — trace back to Bronze for replay) ---
        "bronze_ref":            envelope.get("event_id", ""),
    }


def process_newsapi_message(
    envelope: dict,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single NewsAPI Bronze envelope to SILVER_GLOBAL_NEWS.

    NewsAPI has a single routing path — every valid authority-whitelisted
    article is a Full-Text Document (Section C.3). There is no structured_metrics
    or social_pulse branch for NewsAPI.

    Decision tree:
        1. Envelope fails validate_envelope()         → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()    → (DEAD_LETTER_QUEUE, dlq)
        3. raw_payload.url is empty                   → (DEAD_LETTER_QUEUE, dlq)
           Why: URL is the canonical dedup key for hash_document() (Section 4.1C).
           An article without a URL cannot be deduplicated and must not enter
           the Knowledge Vault.
        4. map_newsapi_article_to_silver() succeeds
           + validate_silver_document() passes        → (SILVER_GLOBAL_NEWS, silver)
        5. validate_silver_document() fails           → (DEAD_LETTER_QUEUE, dlq)

    Note on Keyword Sniper and routing:
        ALL valid articles are emitted to SILVER_GLOBAL_NEWS regardless of
        sniper score. The relevance_score field in the Silver record carries
        the sniper result. The Gold Job gates its OpenAI call on this score,
        which is where "reducing downstream token costs" (Section 4.1A) takes
        effect. Low-signal articles are stored in the Knowledge Vault for
        plain-text search but do not receive vector embeddings.

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/newsapi] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(envelope, env_result.errors, "envelope")

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/newsapi] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(
            envelope, bronze_result.errors, "bronze_payload"
        )

    raw = payload.get("raw_payload", {})

    # --- Guard: URL must be non-empty ---
    if not (raw.get("url") or "").strip():
        errors = [
            "raw_payload.url is empty — URL is the canonical dedup key for "
            "hash_document() and must be present (Section 4.1C)."
        ]
        logger.error("[silver/newsapi] Missing URL for event_id=%s",
                     envelope.get("event_id", "unknown"))
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(envelope, errors, "url_guard")

    # --- Map to Silver ---
    silver = map_newsapi_article_to_silver(raw, envelope)

    # --- Gate: Silver document schema ---
    result = validate_silver_document(silver)
    if not result.is_valid:
        logger.error(
            "[silver/newsapi] Silver document validation failed for url=%s: %s",
            raw.get("url", "unknown"), result.errors,
        )
        return DEAD_LETTER_QUEUE, _newsapi_dlq_record(
            envelope, result.errors, "silver_document"
        )

    logger.debug(
        "[silver/newsapi] url=%s  sniper_score=%.3f  is_high_signal=%s  keywords=%s",
        raw.get("url", "")[:80],
        silver["relevance_score"],
        silver["is_high_signal"],
        silver["sniper_keywords"],
    )

    return SILVER_GLOBAL_NEWS, silver


def _newsapi_dlq_record(envelope: dict, errors: list[str], failed_stage: str) -> dict:
    """
    Build a DLQ record for a failed NewsAPI Bronze message.

    Tags source_topic=BRONZE_NEWSAPI so DLQ operators can filter by source
    when diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_NEWSAPI,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# ArXiv Silver Branch — Transform Functions
# ==========================================================

def map_arxiv_paper_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map an ArXiv paper raw payload to the Silver Full-Text Document Store
    schema (Section C.3).

    Why full_text_raw = abstract (not a title+abstract concatenation):
        Section C.3 defines full_text_raw as the cleaned document body.
        For arXiv papers, the abstract is the complete text available at
        Bronze time — the full paper PDF is not ingested. Storing the abstract
        as full_text_raw is the correct contract; the Gold Job's OpenAI call
        will produce the executive_summary from this text (Section 4.2A).

    Why inverted_pyramid_lead = abstract:
        Section C.3 defines inverted_pyramid_lead as the first paragraph /
        lede sentence. For academic papers the abstract IS the lede —
        it encapsulates the paper's contribution in one block. Unlike NewsAPI
        where description and content are distinct fields (description = lede,
        content = body), arXiv has only the abstract. Both fields point to it.

    Why the Keyword Sniper uses snipe() directly (not snipe_article()):
        snipe_article() reads raw_payload keys "description" and "content".
        ArXiv raw_payload uses "abstract" instead. Calling snipe() directly
        with abstract mapped to the description parameter (weight 1.5) gives
        correct position-weighted scoring. snipe_article() is kept for NewsAPI
        only; this is the field-name isolation its docstring anticipated.

    Why abstract is passed as description (not content):
        DESCRIPTION_WEIGHT (1.5) > CONTENT_WEIGHT (1.0). The abstract is a
        dense, curated summary — semantically equivalent to the lede paragraph
        in the inverted-pyramid model. Giving it the higher weight correctly
        inflates the relevance_score for on-topic academic papers vs.
        incidental keyword matches buried in news body text.

    Why impact_boost = False:
        Section B.1 explicitly states: "No Impact Boost rule (academic papers
        are not breaking-news sources)." Impact Boost is reserved for
        Israeli/Middle East security and energy articles from live feeds (B.4).

    Why authors is stored as a list (not a string like NewsAPI):
        Section C.5 domain_context for ArXiv includes an "authors" list field.
        The Knowledge Vault and Gold Job both consume it as a list. Converting
        it to a string here would force the Knowledge Vault to re-parse it.

    Args:
        raw:      Bronze raw_payload dict from ArXivProducer._build_raw_payload().
        envelope: Full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Full-Text Document Store dict conforming to Section C.3.
    """
    url      = raw.get("url", "")        # canonical abstract URL (dedup key)
    abstract = raw.get("abstract", "")

    # Keyword Sniper (Section 4.1A) — abstract mapped to description slot (weight 1.5)
    # because abstract is denser than a news lede but shorter than a full article body.
    sniper = snipe(
        title=raw.get("title", ""),
        description=abstract,
        content="",        # no full-text body at Bronze time for arXiv
    )

    # SHA-256 dedup hash (Section 4.1C) — canonical URL as stable dedup anchor
    doc_hash = hash_document(abstract, url)

    return {
        # --- Silver Full-Text Document Store (Section C.3) ---
        "doc_id":                str(uuid.uuid4()),
        "document_hash":         doc_hash,
        "canonical_event_id":    envelope.get("event_id", ""),
        "full_text_raw":         abstract,
        "inverted_pyramid_lead": abstract,   # abstract IS the lede for academic papers
        "source_name":           "arxiv",
        "original_url":          url,
        "author":                ", ".join(raw.get("authors", [])),  # str for base C.3 compat
        "publish_date":          raw.get("published", ""),
        "detected_entities":     [],         # populated by Gold Job (OpenAI, Section 4.2A)
        "relevance_score":       sniper.relevance_score,
        # --- ArXiv-specific fields (consumed by knowledge_vault.py + Gold Job) ---
        "title":                 raw.get("title", ""),
        "arxiv_id":              raw.get("arxiv_id", ""),
        "authors":               raw.get("authors", []),     # list for domain_context (C.5)
        "primary_category":      raw.get("primary_category", ""),
        "categories":            raw.get("categories", []),
        "pdf_url":               raw.get("pdf_url", ""),
        "impact_boost":          False,   # No Impact Boost for academic papers (Section B.1)
        "impact_boost_reason":   "",
        "sniper_keywords":       sniper.matched_keywords,
        "is_high_signal":        sniper.is_high_signal,
        "fetch_mode":            raw.get("fetch_mode", "pulse"),
        # --- Lineage (Section 3.2 — trace back to Bronze for replay) ---
        "bronze_ref":            envelope.get("event_id", ""),
    }


def process_arxiv_message(
    envelope: dict,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single ArXiv Bronze envelope to SILVER_GLOBAL_NEWS.

    ArXiv has a single routing path — every valid paper is a Full-Text Document
    (Section C.3). There is no structured_metrics or social_pulse branch for
    ArXiv (Section 3.1: BRONZE_ARXIV → SILVER_GLOBAL_NEWS in BRONZE_TO_SILVER_ROUTING).

    Decision tree:
        1. Envelope fails validate_envelope()         → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()    → (DEAD_LETTER_QUEUE, dlq)
        3. raw_payload.url is empty                   → (DEAD_LETTER_QUEUE, dlq)
           Why: URL is the canonical dedup key for hash_document() (Section 4.1C).
           A paper without a canonical URL cannot be deduplicated and must not
           enter the Knowledge Vault.
        4. map_arxiv_paper_to_silver() succeeds
           + validate_silver_document() passes        → (SILVER_GLOBAL_NEWS, silver)
        5. validate_silver_document() fails           → (DEAD_LETTER_QUEUE, dlq)

    Note on Keyword Sniper and routing (identical to NewsAPI):
        ALL valid papers are emitted to SILVER_GLOBAL_NEWS regardless of sniper
        score. The relevance_score field carries the result. The Gold Job gates
        its OpenAI call on this score — low-signal papers are stored in the
        Knowledge Vault for plain-text search but do not receive embeddings
        (Section 4.1A, 4.2A).

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/arxiv] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _arxiv_dlq_record(envelope, env_result.errors, "envelope")

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/arxiv] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _arxiv_dlq_record(
            envelope, bronze_result.errors, "bronze_payload"
        )

    raw = payload.get("raw_payload", {})

    # --- Guard: URL must be non-empty ---
    # canonical_url = "https://arxiv.org/abs/{base_id}" — set by the producer.
    # An empty URL means the Atom entry ID could not be parsed; the paper cannot
    # be deduplicated and must not enter the Knowledge Vault (Section 4.1C).
    if not (raw.get("url") or "").strip():
        errors = [
            "raw_payload.url is empty — canonical_url is the dedup key for "
            "hash_document() and must be present (Section 4.1C)."
        ]
        logger.error(
            "[silver/arxiv] Missing canonical URL for arxiv_id=%s event_id=%s",
            raw.get("arxiv_id", "unknown"), envelope.get("event_id", "unknown"),
        )
        return DEAD_LETTER_QUEUE, _arxiv_dlq_record(envelope, errors, "url_guard")

    # --- Map to Silver ---
    silver = map_arxiv_paper_to_silver(raw, envelope)

    # --- Gate: Silver document schema ---
    result = validate_silver_document(silver)
    if not result.is_valid:
        logger.error(
            "[silver/arxiv] Silver document validation failed for arxiv_id=%s: %s",
            raw.get("arxiv_id", "unknown"), result.errors,
        )
        return DEAD_LETTER_QUEUE, _arxiv_dlq_record(
            envelope, result.errors, "silver_document"
        )

    logger.debug(
        "[silver/arxiv] arxiv_id=%s  sniper_score=%.3f  is_high_signal=%s  keywords=%s",
        raw.get("arxiv_id", "")[:20],
        silver["relevance_score"],
        silver["is_high_signal"],
        silver["sniper_keywords"],
    )

    return SILVER_GLOBAL_NEWS, silver


def _arxiv_dlq_record(envelope: dict, errors: list[str], failed_stage: str) -> dict:
    """
    Build a DLQ record for a failed ArXiv Bronze message.

    Tags source_topic=BRONZE_ARXIV so DLQ operators can filter by source
    when diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_ARXIV,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# Telegram Silver Branch — Transform Functions
# ==========================================================

def map_telegram_message_to_silver(raw: dict, envelope: dict, client: Any = None) -> dict:
    """
    Map a Telegram message raw payload to the Silver Full-Text Document Store
    schema (Section C.3).

    Why full_text_raw = message_text (not truncated):
        Telegram messages from the 7 vetted channels are micro-articles —
        concise, high-signal posts typically 50–500 characters. Unlike news
        articles where content and description are distinct fields, the entire
        message text IS both the body and the lede. Storing it in both
        full_text_raw and inverted_pyramid_lead matches the ArXiv pattern where
        abstract = full_text_raw = inverted_pyramid_lead. The Gold Job reads
        full_text_raw to generate the OpenAI executive_summary (Section 4.2A).

    Why original_url = message_url (not channel URL):
        Section 4.1C mandates hash_document(full_text, url) for SHA-256 dedup.
        The message_url (https://t.me/{username}/{id}) is unique per message —
        the correct canonical anchor, equivalent to canonical_url in
        arxiv_producer and url in newsapi_producer.

    Why author = channel_title (not channel_username):
        The knowledge_vault.author column stores a human-readable attribution
        string (e.g. "Clash Report") rather than a machine handle
        ("@clashreport"). This is consistent with how newsapi stores
        source.name and arxiv stores authors[0]. The username is preserved in
        the source-specific channel_username field for routing/filtering.

    Why Keyword Sniper uses snipe() with description=message_text:
        snipe_article() reads keys "description" and "content" from raw_payload,
        which Telegram does not have. snipe() is called directly with
        message_text mapped to the description slot (weight 1.5) because Telegram
        messages are dense single-paragraph signals — semantically equivalent to
        a news article lede. No content slot (weight 1.0) needed; the full signal
        is in the description. This matches the ArXiv branch's approach with abstract.

    Why no impact_boost:
        Impact Boost (+1 to impact_level) is reserved for NewsAPI articles
        covering Israeli/Middle East security or energy topics (Section B.4).
        Telegram channels are already curated for geopolitical signal — boosting
        them further would skew Gold Job scoring. impact_level is set purely by
        the Gold Job's Cognitive Metadata Extraction (Section 4.2A).

    Args:
        raw:      Bronze raw_payload dict from TelegramProducer._build_raw_payload().
        envelope: Full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Full-Text Document Store dict conforming to Section C.3.
    """
    message_text     = raw.get("message_text", "")
    message_url      = raw.get("message_url", "")   # canonical dedup key (Section 4.1C)
    channel_username = raw.get("channel_username", "")

    # SHA-256 dedup hash (Section 4.1C) — always computed on the ORIGINAL text so
    # the hash is stable across reruns regardless of translation model output variation.
    doc_hash = hash_document(message_text, message_url)

    # Translation (Section 4.1D) — translate non-English channels to English before
    # sniper scoring and persistence so the Gold Layer receives a unified English corpus.
    # Pass-through when client=None (Gate 2 tests) or when channel is English-only.
    translated_text = translate_to_english(message_text, channel_username, client)

    # Keyword Sniper (Section 4.1A) — run on translated_text so English keywords match
    # content from Hebrew channels (abualiexpress, yediotnews25) correctly.
    sniper = snipe(
        title=raw.get("channel_title", ""),  # channel name as a weak title signal
        description=translated_text,
        content="",                          # no separate body content for Telegram
    )

    # original_text: stored only when translation was performed — signals to the Gold Job
    # and RAG agent that full_text_raw is a translation, not the raw channel text.
    # Empty string when no translation needed (English channels) to avoid storage waste.
    original_text = message_text if needs_translation(channel_username) else ""

    return {
        # --- Silver Full-Text Document Store (Section C.3) ---
        "doc_id":                str(uuid.uuid4()),
        "document_hash":         doc_hash,
        "canonical_event_id":    envelope.get("event_id", ""),
        "full_text_raw":         translated_text,
        "inverted_pyramid_lead": translated_text,  # whole message IS the lede (micro-article)
        "source_name":           "telegram",
        "original_url":          message_url,       # dedup key (Section 4.1C)
        "author":                raw.get("channel_title", ""),
        "publish_date":          raw.get("message_date", ""),
        "detected_entities":     [],                # populated by Gold Job (OpenAI, Section 4.2A)
        "relevance_score":       sniper.relevance_score,
        # --- Telegram-specific fields (consumed by knowledge_vault.py + Gold Job) ---
        "title":                 translated_text[:120],  # first 120 chars of (translated) message
        "channel_username":      channel_username,
        "extracted_links":       raw.get("extracted_links", []),     # domain_context (C.5)
        "is_forwarded":          raw.get("is_forwarded", False),
        "forwarded_from":        raw.get("forwarded_from"),          # str or None
        "views":                 raw.get("views"),                   # int or None
        "has_media":             raw.get("has_media", False),
        "impact_boost":          False,  # not applicable to Telegram (Section B.4)
        "impact_boost_reason":   "",
        "sniper_keywords":       sniper.matched_keywords,
        "is_high_signal":        sniper.is_high_signal,
        # --- Translation audit fields (Section 4.1D) ---
        "original_text":         original_text,    # pre-translation text; "" when not translated
        # --- Lineage (Section 3.2 — trace back to Bronze for replay) ---
        "bronze_ref":            envelope.get("event_id", ""),
    }


def process_telegram_message(
    envelope: dict,
    client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single Telegram Bronze envelope to SILVER_GLOBAL_NEWS.

    Telegram has a single routing path — every valid message is a Full-Text
    Document persisted to knowledge_vault (Silver) and knowledge_vectors (Gold).
    It routes to SILVER_GLOBAL_NEWS, not SILVER_SOCIAL_PULSE, because Telegram
    channels are treated as direct-signal micro-articles, not community discourse
    (Section C.3 Telegram News-Centric pattern, Part 1.5 Vector Persistence Map).

    Decision tree:
        1. Envelope fails validate_envelope()         → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()    → (DEAD_LETTER_QUEUE, dlq)
        3. raw_payload.message_url is empty           → (DEAD_LETTER_QUEUE, dlq)
           Why: message_url is the canonical dedup key for hash_document()
           (Section 4.1C). A message without a URL cannot be deduplicated and
           must not enter the Knowledge Vault.
        4. raw_payload.message_text is empty          → (DEAD_LETTER_QUEUE, dlq)
           Why: Defense in depth — the producer's media-only filter (Section A.1)
           should prevent this, but an empty-text message must never reach
           knowledge_vault (full_text_raw has a NOT NULL constraint).
        5. map_telegram_message_to_silver() succeeds
           + validate_silver_document() passes        → (SILVER_GLOBAL_NEWS, silver)
        6. validate_silver_document() fails           → (DEAD_LETTER_QUEUE, dlq)

    Note on Keyword Sniper and routing:
        ALL valid messages are emitted to SILVER_GLOBAL_NEWS regardless of sniper
        score. The relevance_score field carries the result. The Gold Job gates
        its OpenAI call on this score — low-signal messages are stored in the
        Knowledge Vault for plain-text search but do not receive embeddings
        (Section 4.1A, 4.2A).

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/telegram] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _telegram_dlq_record(
            envelope, env_result.errors, "envelope"
        )

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/telegram] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _telegram_dlq_record(
            envelope, bronze_result.errors, "bronze_payload"
        )

    raw = payload.get("raw_payload", {})

    # --- Guard: message_url must be non-empty ---
    # message_url is the canonical dedup key for hash_document() (Section 4.1C).
    # Format: https://t.me/{channel_username}/{message_id}
    if not (raw.get("message_url") or "").strip():
        errors = [
            "raw_payload.message_url is empty — message_url is the dedup key for "
            "hash_document() and must be present (Section 4.1C)."
        ]
        logger.error(
            "[silver/telegram] Missing message_url for event_id=%s",
            envelope.get("event_id", "unknown"),
        )
        return DEAD_LETTER_QUEUE, _telegram_dlq_record(envelope, errors, "url_guard")

    # --- Guard: message_text must be non-empty ---
    # Defense in depth — the producer's media-only filter should have caught this,
    # but a message reaching Silver with empty text must not corrupt knowledge_vault.
    if not (raw.get("message_text") or "").strip():
        errors = [
            "raw_payload.message_text is empty — media-only messages must be filtered "
            "by the producer (Section A.1). This message bypassed the filter."
        ]
        logger.error(
            "[silver/telegram] Empty message_text for event_id=%s channel=%s",
            envelope.get("event_id", "unknown"),
            raw.get("channel_username", "unknown"),
        )
        return DEAD_LETTER_QUEUE, _telegram_dlq_record(envelope, errors, "text_guard")

    # --- Map to Silver (client passed for translation, Section 4.1D) ---
    silver = map_telegram_message_to_silver(raw, envelope, client)

    # --- Gate: Silver document schema ---
    result = validate_silver_document(silver)
    if not result.is_valid:
        logger.error(
            "[silver/telegram] Silver document validation failed for channel=%s "
            "message_url=%s: %s",
            raw.get("channel_username", "unknown"),
            raw.get("message_url", "unknown"),
            result.errors,
        )
        return DEAD_LETTER_QUEUE, _telegram_dlq_record(
            envelope, result.errors, "silver_document"
        )

    logger.debug(
        "[silver/telegram] @%s message_url=%s  sniper_score=%.3f  "
        "is_high_signal=%s  keywords=%s",
        raw.get("channel_username", ""),
        raw.get("message_url", "")[-40:],
        silver["relevance_score"],
        silver["is_high_signal"],
        silver["sniper_keywords"],
    )

    return SILVER_GLOBAL_NEWS, silver


def _telegram_dlq_record(envelope: dict, errors: list[str], failed_stage: str) -> dict:
    """
    Build a DLQ record for a failed Telegram Bronze message.

    Tags source_topic=BRONZE_TELEGRAM so DLQ operators can filter by source
    when diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_TELEGRAM,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# HackerNews Silver Branch — Transform Functions
# ==========================================================

def map_hackernews_story_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map a HackerNews story raw payload to the Silver Social Discourse Store
    schema (Section C.3 — Story-Centric pattern).

    Why social_vault (not knowledge_vault):
        HackerNews stories are community discourse — expert commentary and
        discussion threads — not authoritative articles or research papers.
        Section C.3 explicitly categorises HackerNews as Story-Centric and
        places it alongside Polymarket in the Social Vault. The Gold
        vector target is social_vectors (C.6), not knowledge_vectors (C.5).

    Why Keyword Sniper uses title + story_text (not url):
        For standard link stories, story_text is empty — the signal is in the
        title. For Ask HN / Show HN, story_text is the post body (the actual
        signal). Passing story_text to the description slot (weight 1.5) and
        title to the title slot correctly weights both content types without
        double-counting. content="" because there is no separate article body
        at Bronze time (Section 4.1A).

    Why content_hash uses hash_social_batch(story_id, top_comments):
        Follows the Polymarket comment branch pattern — story_id anchors the
        batch, top comment IDs form the content fingerprint. A story re-fetched
        within the same pulse window with identical top_comments produces the
        same hash and is deduplicated by the Social Vault (Section 4.1C).
        If new top comments appear in the next pulse, the hash changes and the
        story is re-ingested with updated commentary.

    Why no impact_boost:
        Impact Boost is reserved for NewsAPI articles covering Israeli/Middle
        East security or energy topics (Section B.4). HackerNews stories
        receive their impact_level from the Gold Job's Consensus Bundling
        enrichment (Section 4.2A, C.6).

    Why published_at = created_at from Bronze:
        Algolia's created_at is the story's submission timestamp on HN, which
        is the canonical publication time. The Silver validator requires
        published_at as a string (Section C.3 hackernews branch).

    Args:
        raw:      Bronze raw_payload dict from HackerNewsProducer._build_raw_payload().
        envelope: Full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Social dict conforming to the HackerNews Story-Centric pattern
        in Section C.3, passing validate_silver_social() without errors.
    """
    story_id     = str(raw.get("story_id", ""))
    title        = raw.get("title", "")
    url          = raw.get("url", "")           # empty for Ask HN (no external link)
    story_text   = raw.get("story_text", "")    # Ask HN / Show HN body text
    top_comments = raw.get("top_comments") or []

    # Keyword Sniper (Section 4.1A):
    # - title → title slot for all story types
    # - story_text → description slot: non-empty for Ask/Show HN; "" for link stories
    # This avoids double-counting the title for link stories where story_text is empty.
    sniper = snipe(
        title=title,
        description=story_text,  # "" for link stories; body text for Ask/Show HN
        content="",              # no full-text body available at Bronze time
    )

    # SHA-256 dedup (Section 4.1C) — story_id + sorted top comment IDs
    content_hash = hash_social_batch(story_id, top_comments)

    return {
        # --- Silver Social Discourse Store — HackerNews Story-Centric (Section C.3) ---
        "source_name":     "hackernews",
        "ingested_at":     envelope.get("producer_timestamp", ""),
        # validate_silver_social() hackernews branch required fields:
        "story_id":        story_id,
        "title":           title,
        "url":             url,                          # empty string for Ask HN
        "author":          raw.get("author", ""),
        "points":          int(raw.get("points") or 0),
        "published_at":    raw.get("created_at", ""),   # Algolia submission timestamp
        "top_comments":    top_comments,                 # up to 10 (Section B.2)
        # --- Extra fields for social_vault + Gold Job consumption ---
        "num_comments":    int(raw.get("num_comments") or 0),
        "story_text":      story_text,                  # Ask/Show HN body
        "story_type":      raw.get("story_type", "story"),  # "story"|"ask_hn"|"show_hn"
        "content_hash":    content_hash,                 # dedup anchor (Section 4.1C)
        "relevance_score": sniper.relevance_score,       # gates Gold Job OpenAI call
        "sniper_keywords": sniper.matched_keywords,
        "is_high_signal":  sniper.is_high_signal,
        "fetch_mode":      raw.get("fetch_mode", "pulse"),
        # --- Lineage (Section 3.2 — trace back to Bronze for replay) ---
        "bronze_ref":      envelope.get("event_id", ""),
    }


def process_hackernews_message(
    envelope: dict,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single HackerNews Bronze envelope to SILVER_SOCIAL_PULSE.

    HackerNews has a single routing path — every valid story is a Social
    Discourse record persisted to social_vault (Silver) and social_vectors
    (Gold, entry_type='hackernews_story_summary', Section C.6).
    It routes to SILVER_SOCIAL_PULSE, not SILVER_GLOBAL_NEWS (Section 3.1:
    BRONZE_HACKERNEWS → SILVER_SOCIAL_PULSE in BRONZE_TO_SILVER_ROUTING,
    Part 1.5 Vector Persistence Map in task_plan.md).

    Decision tree:
        1. Envelope fails validate_envelope()         → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()    → (DEAD_LETTER_QUEUE, dlq)
        3. raw_payload.story_id is empty              → (DEAD_LETTER_QUEUE, dlq)
           Why: story_id is the Kafka partition key and the dedup anchor for
           hash_social_batch(). A story without an ID cannot be deduplicated
           and must not enter the Social Vault (Section 4.1C).
        4. raw_payload.title is empty                 → (DEAD_LETTER_QUEUE, dlq)
           Why: title is the primary display field in the Gold Social schema
           (Section C.6) and the main text input for the Keyword Sniper
           (Section 4.1A). A story without a title cannot be embedded.
        5. map_hackernews_story_to_silver() succeeds
           + validate_silver_social() passes          → (SILVER_SOCIAL_PULSE, silver)
        6. validate_silver_social() fails             → (DEAD_LETTER_QUEUE, dlq)

    Note on Keyword Sniper and routing:
        ALL valid stories are emitted to SILVER_SOCIAL_PULSE regardless of
        sniper score (same policy as ArXiv/Telegram). The relevance_score field
        carries the result. The Gold Job gates its OpenAI Consensus Bundling
        call on this score — low-signal stories are stored in the Social Vault
        for plain-text drill-down but skip the OpenAI token spend.

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/hackernews] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _hackernews_dlq_record(
            envelope, env_result.errors, "envelope"
        )

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/hackernews] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _hackernews_dlq_record(
            envelope, bronze_result.errors, "bronze_payload"
        )

    raw = payload.get("raw_payload", {})

    # --- Guard: story_id must be non-empty ---
    # story_id is the Kafka partition key and hash_social_batch() anchor.
    # An empty story_id means the Algolia objectID was missing — this story
    # cannot be deduplicated and must not enter the Social Vault (Section 4.1C).
    if not str(raw.get("story_id") or "").strip():
        errors = [
            "raw_payload.story_id is empty — story_id is the partition key and "
            "dedup anchor for hash_social_batch() (Section 4.1C)."
        ]
        logger.error(
            "[silver/hackernews] Missing story_id for event_id=%s",
            envelope.get("event_id", "unknown"),
        )
        return DEAD_LETTER_QUEUE, _hackernews_dlq_record(envelope, errors, "story_id_guard")

    # --- Guard: title must be non-empty ---
    # title is the primary Keyword Sniper input (Section 4.1A) and the text
    # field required by the Gold Job for Consensus Bundling (Section 4.2A).
    # A story with an empty title cannot be embedded or displayed.
    if not (raw.get("title") or "").strip():
        errors = [
            "raw_payload.title is empty — title is the primary Keyword Sniper "
            "input and Gold embedding field (Sections 4.1A, 4.2A)."
        ]
        logger.error(
            "[silver/hackernews] Empty title for story_id=%s event_id=%s",
            raw.get("story_id", "unknown"), envelope.get("event_id", "unknown"),
        )
        return DEAD_LETTER_QUEUE, _hackernews_dlq_record(envelope, errors, "title_guard")

    # --- Map to Silver ---
    silver = map_hackernews_story_to_silver(raw, envelope)

    # --- Gate: Silver social schema ---
    result = validate_silver_social(silver)
    if not result.is_valid:
        logger.error(
            "[silver/hackernews] Silver social validation failed for story_id=%s: %s",
            raw.get("story_id", "unknown"), result.errors,
        )
        return DEAD_LETTER_QUEUE, _hackernews_dlq_record(
            envelope, result.errors, "silver_social"
        )

    logger.debug(
        "[silver/hackernews] story_id=%s  points=%d  sniper_score=%.3f  "
        "is_high_signal=%s  keywords=%s",
        raw.get("story_id", ""),
        int(raw.get("points") or 0),
        silver["relevance_score"],
        silver["is_high_signal"],
        silver["sniper_keywords"],
    )

    return SILVER_SOCIAL_PULSE, silver


def _hackernews_dlq_record(
    envelope: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for a failed HackerNews Bronze message.

    Tags source_topic=BRONZE_HACKERNEWS so DLQ operators can filter by
    source when diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_HACKERNEWS,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# Google Trends Silver Branch — Transform Functions
# ==========================================================

def _translate_keyword(term: str, geo: str = "", client: Any = None) -> str:
    """
    Translate a non-English Google Trends keyword to English (Section 4.1D, B.5).

    Delegates to translate_keyword_to_english() from processing.translation.
    English geos (US, GB, SG) are passed through unchanged — zero API cost.
    Non-English geos (IL → Hebrew, DE → German) trigger a GPT-3.5-turbo call
    when client is provided.

    client=None is a valid no-op: tests that call map_googletrends_to_silver()
    without a client get the original term unchanged (same as pre-Phase 5 behaviour).

    Args:
        term:   Keyword string as returned by Pytrends (may be in any language).
        geo:    Google Trends geo code (e.g. "US", "IL", "DE").
        client: openai.OpenAI instance, or None for pass-through.

    Returns:
        English translation of the term, or the original term unchanged.
    """
    return translate_keyword_to_english(term, geo, client)


def map_googletrends_to_silver(raw: dict, envelope: dict, client: Any = None) -> dict:
    """
    Map a Google Trends raw_payload to the Silver Structured Metric schema
    (Section C.2 — Google Trends row in the metadata_extension table).

    Why external_reference_id = translated keyword:
        The Gold Job's keyed-state momentum operator keys by
        (source_name, external_reference_id). Using the keyword as the
        key ensures per-keyword state isolation — "Inflation" history is
        never cross-contaminated with "Crude Oil" history (Section 4.2B).
        The keyword is passed through _translate_keyword() so DE/IL terms
        are normalised to their English equivalent before becoming a state
        key (Phase 5 wires the real translation).

    Why timestamp_utc uses observation_date when present:
        reactive and backfill modes include observation_date ("YYYY-MM-DD")
        from interest_over_time() — that is the actual date the interest
        score refers to. static mode has no per-observation date; we fall
        back to fetch_timestamp (the wall-clock capture time) which is the
        closest available anchor for the daily snapshot.

    Why status = "trending" for static, "observed" for reactive/backfill:
        "trending" signals that the keyword appeared in the daily trending
        feed (binary — it either appeared or it didn't).
        "observed" signals a quantitative interest score returned by
        interest_over_time() for a specific date. Downstream consumers
        can distinguish static ranking from absolute score measurements
        without inspecting metadata_extension.

    Why parent_id = "":
        Google Trends keywords have no parent concept. Polymarket uses
        parent_id for condition_id; FRED has no parent. Same reasoning
        applies here — there is no hierarchy above the keyword itself.

    Why momentum_block stubs are 0.0:
        Computing change_24h/7d/30d requires prior observations for the
        same keyword. The Gold Job maintains per-keyword history in Flink
        ValueState and computes the real deltas (Section 4.2B), exactly
        as it does for FRED series.

    Args:
        raw:      The raw_payload dict from the Bronze envelope, as produced
                  by GoogleTrendsProducer._build_raw_payload().
        envelope: The full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Structured Metric dict conforming to Section C.2.
    """
    keyword        = raw.get("keyword", "")
    geo            = raw.get("geo", "")
    geo_name       = raw.get("geo_name", "")
    interest_score = float(raw.get("interest_score", 0.0))
    mode           = raw.get("mode", "static")
    observation_date = raw.get("observation_date")   # present for reactive/backfill
    fetch_timestamp  = raw.get("fetch_timestamp", envelope.get("producer_timestamp", ""))

    # Translate keyword to English (Section 4.1D — wired in Phase 5).
    # State key in Gold Job uses the English term, so DE/IL keywords
    # (Hebrew/German) are normalised before becoming momentum keyed-state keys.
    # English geos (US, GB, SG) pass through unchanged — zero API cost.
    keyword_en = _translate_keyword(keyword, geo, client)

    # Derive timestamp_utc:
    #   reactive / backfill — use observation_date (the actual data point date),
    #                          same midnight-UTC convention as FRED.
    #   static              — use fetch_timestamp (daily snapshot has no finer
    #                          resolution than the fetch time).
    if observation_date and len(observation_date) == 10:
        timestamp_utc = f"{observation_date}T00:00:00+00:00"
    else:
        timestamp_utc = fetch_timestamp or envelope.get("producer_timestamp", "")

    # status: "trending" for daily top-list appearances, "observed" for
    # quantitative interest_over_time() scores (reactive/backfill).
    status = "trending" if mode == "static" else "observed"

    silver: dict = {
        "schema_version": "2.0",
        "layer":          "Silver",
        "entity_type":    "Structured_Metric",
        "core_identity": {
            "metric_id":             str(uuid.uuid4()),
            "canonical_event_id":    envelope.get("event_id", ""),
            "parent_id":             "",      # No parent concept for keywords
            "source_name":           "googletrends",
            # keyword_en is the keyed-state key in the Gold Job's momentum operator.
            "external_reference_id": keyword_en,
        },
        "data_point": {
            "current_value": interest_score,
            "unit":          "interest_score_0_100",
            "status":        status,
            "timestamp_utc": timestamp_utc,
        },
        # Stubs — Gold Job computes actual deltas via keyed state (Section 4.2B)
        "momentum_block": {
            "change_24h":    0.0,
            "change_7d":     0.0,
            "change_30d":    0.0,
            "is_new_market": False,
        },
        # Google Trends metadata_extension (Section C.2 — Google Trends row).
        # Stored verbatim so the RAG agent can filter by geo, mode, or rank.
        "metadata_extension": {
            "keyword":          keyword,        # original (pre-translation) term
            "keyword_en":       keyword_en,     # translated term (= keyword until Phase 5)
            "geo":              geo,
            "geo_name":         geo_name,
            "mode":             mode,
            "rank_in_trending": raw.get("rank_in_trending"),  # int or None
            "timeframe":        raw.get("timeframe"),          # str or None
        },
    }
    return silver


def process_googletrends_message(
    envelope: dict,
    client: Any = None,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single Google Trends Bronze envelope to
    SILVER_STRUCTURED_METRICS.

    Google Trends has only one routing path — every valid observation is a
    structured metric. There is no social or document branch (Section 3.1:
    BRONZE_GOOGLETRENDS → SILVER_STRUCTURED_METRICS in BRONZE_TO_SILVER_ROUTING).

    Decision tree:
        1. Envelope fails validate_envelope()          → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()     → (DEAD_LETTER_QUEUE, dlq)
        3. interest_score is missing or not numeric    → (DEAD_LETTER_QUEUE, dlq)
        4. interest_score is outside [0.0, 100.0]      → (DEAD_LETTER_QUEUE, dlq)
        5. map_googletrends_to_silver() succeeds
           + validate_silver_structured_metric() passes → (SILVER_STRUCTURED_METRICS, silver)
        6. validate_silver_structured_metric() fails   → (DEAD_LETTER_QUEUE, dlq)

    Why validate interest_score range [0.0, 100.0]:
        Google Trends scores are definitionally bounded at 0-100.
        For static mode the rank-to-100 mapping produces values in [2.0, 100.0].
        A value outside [0.0, 100.0] indicates a producer bug or a corrupt
        replay — routing to DLQ makes the defect visible rather than silently
        storing an out-of-spec numeric in momentum_vault.

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/googletrends] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _googletrends_dlq_record(
            envelope, env_result.errors, "envelope"
        )

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/googletrends] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _googletrends_dlq_record(
            envelope, bronze_result.errors, "bronze_payload"
        )

    raw = payload.get("raw_payload", {})

    # --- Gate: interest_score numeric guard ---
    try:
        score = float(raw.get("interest_score", ""))
    except (TypeError, ValueError):
        errors = [
            f"raw_payload.interest_score='{raw.get('interest_score')}' is not numeric. "
            "Producer must always emit a float in [0.0, 100.0] (Section B.5)."
        ]
        logger.error(
            "[silver/googletrends] Non-numeric interest_score for keyword='%s': %s",
            raw.get("keyword", "unknown"), errors,
        )
        return DEAD_LETTER_QUEUE, _googletrends_dlq_record(
            envelope, errors, "interest_score_guard"
        )

    # --- Gate: interest_score range guard ---
    if not (0.0 <= score <= 100.0):
        errors = [
            f"raw_payload.interest_score={score} is outside the valid range [0.0, 100.0]. "
            "Google Trends scores are bounded at 0-100 by definition (Section B.5)."
        ]
        logger.error(
            "[silver/googletrends] Out-of-range interest_score for keyword='%s': %s",
            raw.get("keyword", "unknown"), errors,
        )
        return DEAD_LETTER_QUEUE, _googletrends_dlq_record(
            envelope, errors, "interest_score_range"
        )

    # --- Gate: keyword presence ---
    if not raw.get("keyword", "").strip():
        errors = ["raw_payload.keyword is missing or empty."]
        logger.error("[silver/googletrends] Missing keyword: %s", errors)
        return DEAD_LETTER_QUEUE, _googletrends_dlq_record(
            envelope, errors, "keyword_guard"
        )

    # --- Map to Silver (client passed for keyword translation, Section 4.1D) ---
    silver = map_googletrends_to_silver(raw, envelope, client)

    # --- Gate: Silver schema ---
    result = validate_silver_structured_metric(silver)
    if not result.is_valid:
        logger.error(
            "[silver/googletrends] Silver metric validation failed for keyword='%s': %s",
            raw.get("keyword", "unknown"), result.errors,
        )
        return DEAD_LETTER_QUEUE, _googletrends_dlq_record(
            envelope, result.errors, "silver_structured_metric"
        )

    return SILVER_STRUCTURED_METRICS, silver


def _googletrends_dlq_record(
    envelope: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for a failed Google Trends Bronze message.

    Tags source_topic=BRONZE_GOOGLETRENDS so DLQ operators can filter by
    source when diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_GOOGLETRENDS,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# OpenWeather Silver Branch — Transform Functions
# ==========================================================

# OWM weather[0].id → severity 1–5 mapping (Section B.6 / T2 design decision).
#
# OpenWeatherMap condition codes are grouped by weather phenomenon:
#   2xx — Thunderstorm   5xx — Rain        7xx — Atmosphere (fog, dust, …)
#   3xx — Drizzle        6xx — Snow/Ice    800 — Clear sky
#   80x — Clouds
#
# Severity scale (matches Gold Job trigger thresholds):
#   1 — Clear / light clouds     (no operational impact)
#   2 — Scattered clouds / mist  (minor)
#   3 — Rain / drizzle / fog     (notable — vessel/flight caution)
#   4 — Heavy rain / snow / dense atmosphere / thunderstorm (significant disruption)
#   5 — Extreme thunderstorm / tornado / volcanic ash (critical — Disaster Alert)
#
# Why a flat dict rather than range logic:
#   OWM codes are not strictly sequential within a group (e.g. 771 Squall and
#   762 Volcanic Ash both sit in 7xx but have very different severity). A flat
#   mapping makes every assignment explicit and testable (Gate 1 verifies full
#   coverage). Missing code → DLQ at Silver; never silently defaulted.
#
# Source: https://openweathermap.org/weather-conditions (all documented codes)

CONDITION_ID_TO_SEVERITY: dict[int, int] = {
    # 2xx — Thunderstorm
    200: 4,  # thunderstorm with light rain
    201: 4,  # thunderstorm with rain
    202: 5,  # thunderstorm with heavy rain
    210: 4,  # light thunderstorm
    211: 4,  # thunderstorm
    212: 5,  # heavy thunderstorm
    221: 5,  # ragged thunderstorm
    230: 4,  # thunderstorm with light drizzle
    231: 4,  # thunderstorm with drizzle
    232: 4,  # thunderstorm with heavy drizzle

    # 3xx — Drizzle
    300: 2,  # light intensity drizzle
    301: 2,  # drizzle
    302: 3,  # heavy intensity drizzle
    310: 2,  # light intensity drizzle rain
    311: 3,  # drizzle rain
    312: 3,  # heavy intensity drizzle rain
    313: 3,  # shower rain and drizzle
    314: 3,  # heavy shower rain and drizzle
    321: 2,  # shower drizzle

    # 5xx — Rain
    500: 3,  # light rain
    501: 3,  # moderate rain
    502: 4,  # heavy intensity rain
    503: 4,  # very heavy rain
    504: 5,  # extreme rain
    511: 4,  # freezing rain
    520: 3,  # light intensity shower rain
    521: 3,  # shower rain
    522: 4,  # heavy intensity shower rain
    531: 4,  # ragged shower rain

    # 6xx — Snow / Ice
    600: 3,  # light snow
    601: 3,  # snow
    602: 4,  # heavy snow
    611: 3,  # sleet
    612: 3,  # light shower sleet
    613: 3,  # shower sleet
    615: 3,  # light rain and snow
    616: 3,  # rain and snow
    620: 3,  # light shower snow
    621: 3,  # shower snow
    622: 4,  # heavy shower snow

    # 7xx — Atmosphere (fog, haze, dust, ash, squall, tornado)
    701: 2,  # mist
    711: 3,  # smoke
    721: 2,  # haze
    731: 3,  # sand/dust whirls
    741: 3,  # fog
    751: 3,  # sand
    761: 3,  # dust
    762: 5,  # volcanic ash
    771: 4,  # squalls
    781: 5,  # tornado

    # 800 — Clear sky
    800: 1,  # clear sky

    # 80x — Clouds
    801: 1,  # few clouds (11–25%)
    802: 2,  # scattered clouds (25–50%)
    803: 2,  # broken clouds (51–84%)
    804: 2,  # overcast clouds (85–100%)
}

# Wind speed conversion factor: m/s → knots (Silver Job responsibility, §3.3)
_MS_TO_KNOTS: float = 1.94384


def map_openweather_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map an OpenWeather raw_payload to the Silver Structured Metric schema
    (Section C.2 — OpenWeather row in the metadata_extension table).

    Why current_value = temperature_celsius:
        Temperature is the most universally comparable metric across hotspots
        and enables momentum deltas like "Taipei +8°C vs last week →
        semiconductor supply chain risk" (task_plan.md design decision 2026-04-02).

    Why wind m/s → knots conversion happens here (not in producer):
        Service Isolation (Section 3.3) — the producer emits raw API values.
        Unit normalization is a transformation, not ingestion. knots is the
        maritime standard unit used by the Potential_Shipping_Delay trigger in
        the Gold Job, so converting here keeps the Gold trigger logic clean.

    Why CONDITION_ID_TO_SEVERITY mapping happens here (not in producer):
        Severity derivation is a domain transformation — the OWM condition code
        is raw data; the 1–5 severity scale is a project-specific abstraction.
        Applying it at Silver keeps the mapping centralized and testable
        (Gate 1 verifies CONDITION_ID_TO_SEVERITY coverage; Gate 2 Silver
        verifies unknown code → DLQ). A missing code → DLQ, never defaulted.

    Why external_reference_id = hotspot_name:
        The Gold Job's keyed-state momentum operator keys by
        (source_name, external_reference_id). Using the hotspot name ensures
        per-hotspot state isolation — "Taipei" history is never mixed with
        "Suez_Canal" history (Section 4.2B).

    Why timestamp_utc = fetch_timestamp:
        OpenWeather current conditions have no observation_date distinct from
        the fetch time — the API returns the snapshot at request time.
        fetch_timestamp is the correct anchor (same as GoogleTrends static mode).

    Why momentum_block is stubbed to 0.0:
        Computing change_24h/7d/30d requires prior observations for the same
        hotspot. The Gold Job maintains per-hotspot history in Flink ValueState
        and computes the real deltas (Section 4.2B), exactly as for FRED.

    Args:
        raw:      The raw_payload dict from the Bronze envelope, as produced
                  by OpenWeatherProducer._build_raw_payload().
        envelope: The full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Structured Metric dict conforming to Section C.2.

    Raises:
        KeyError: If weather_id is not found in CONDITION_ID_TO_SEVERITY —
                  caller (process_openweather_message) routes to DLQ before
                  calling this function, so this should never raise in production.
    """
    hotspot_name      = raw.get("hotspot_name", "")
    temp_celsius      = float(raw.get("temperature_celsius", 0.0))
    wind_speed_ms     = float(raw.get("wind_speed_ms", 0.0))
    pressure_hpa      = float(raw.get("pressure_hpa", 0.0))
    humidity_pct      = float(raw.get("humidity_pct", 0.0))
    weather_id        = int(raw.get("weather_id", 0))
    strategic_tag     = raw.get("strategic_tag", "")
    lat               = raw.get("lat")
    lon               = raw.get("lon")
    official_alerts   = raw.get("official_alerts", [])
    fetch_timestamp   = raw.get("fetch_timestamp", envelope.get("producer_timestamp", ""))
    mode              = raw.get("mode", "static")

    # Unit conversion: m/s → knots (Gold Job trigger uses knots threshold)
    wind_speed_knots = round(wind_speed_ms * _MS_TO_KNOTS, 4)

    # Severity mapping — caller guarantees weather_id is in the table
    condition_severity = CONDITION_ID_TO_SEVERITY[weather_id]

    silver: dict = {
        "schema_version": "2.0",
        "layer":          "Silver",
        "entity_type":    "Structured_Metric",
        "core_identity": {
            "metric_id":             str(uuid.uuid4()),
            "canonical_event_id":    envelope.get("event_id", ""),
            "parent_id":             "",           # No parent hierarchy for hotspots
            "source_name":           "openweather",
            "external_reference_id": hotspot_name, # keyed-state key in Gold Job
        },
        "data_point": {
            "current_value": temp_celsius,         # temperature_celsius (design decision)
            "unit":          "celsius",
            "status":        mode,                 # "static" | "reactive"
            "timestamp_utc": fetch_timestamp,
        },
        # Stubs — Gold Job computes actual deltas via keyed state (Section 4.2B)
        "momentum_block": {
            "change_24h":    0.0,
            "change_7d":     0.0,
            "change_30d":    0.0,
            "is_new_market": False,
        },
        # OpenWeather metadata_extension (Section C.2 — OpenWeather row)
        "metadata_extension": {
            "wind_speed_knots":  wind_speed_knots,
            "pressure_hpa":      pressure_hpa,
            "humidity_pct":      humidity_pct,
            "condition_code":    weather_id,
            "condition_severity": condition_severity,
            "strategic_tag":     strategic_tag,
            "coordinates":       {"lat": lat, "lon": lon},
            "official_alerts":   official_alerts,
        },
    }
    return silver


def process_openweather_message(
    envelope: dict,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single OpenWeather Bronze envelope to
    SILVER_STRUCTURED_METRICS.

    OpenWeather has only one routing path — every valid observation is a
    structured metric. There is no social or document branch (Section 3.1:
    BRONZE_OPENWEATHER → SILVER_STRUCTURED_METRICS in BRONZE_TO_SILVER_ROUTING).

    Decision tree:
        1. Envelope fails validate_envelope()                 → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()            → (DEAD_LETTER_QUEUE, dlq)
        3. raw_payload.lat or raw_payload.lon is missing      → (DEAD_LETTER_QUEUE, dlq)
        4. raw_payload.strategic_tag is missing or empty      → (DEAD_LETTER_QUEUE, dlq)
        5. raw_payload.wind_speed_ms is missing or non-numeric → (DEAD_LETTER_QUEUE, dlq)
        6. raw_payload.weather_id not in CONDITION_ID_TO_SEVERITY → (DEAD_LETTER_QUEUE, dlq)
        7. map_openweather_to_silver() succeeds
           + validate_silver_structured_metric() passes       → (SILVER_STRUCTURED_METRICS, silver)
        8. validate_silver_structured_metric() fails          → (DEAD_LETTER_QUEUE, dlq)

    Why lat/lon are DLQ guards (not just metadata):
        Coordinates are the canonical identity of a hotspot observation.
        A reading without coordinates cannot be attributed to a geographic
        location — it is useless to the RAG agent and triggers. Routing
        to DLQ makes the defect visible for operator investigation.

    Why strategic_tag is a DLQ guard:
        The Gold Job's trigger logic (Natural_Disaster_Alert, Tech_Supply_Risk,
        Potential_Shipping_Delay) keys on strategic_tag. A record without this
        tag would reach momentum_vault with null trigger evaluation — a silent
        data quality failure. DLQ routing surfaces the producer bug.

    Why wind_speed_ms is a DLQ guard:
        wind_speed_knots (derived from wind_speed_ms) is the Potential_Shipping_Delay
        trigger input. A missing wind value would produce a spuriously zero knot
        reading — DLQ routing prevents false-negative trigger suppression.

    Why unknown weather_id → DLQ (not default):
        CONDITION_ID_TO_SEVERITY covers every documented OWM condition code.
        An unknown ID means either a new OWM code was added (update the mapping)
        or the producer sent corrupt data. Silent defaulting would hide this —
        DLQ routing makes it visible and auditable (Section 3.5).

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/openweather] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, env_result.errors, "envelope"
        )

    payload = envelope.get("payload", {})

    # --- Gate: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/openweather] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, bronze_result.errors, "bronze_payload"
        )

    raw = payload.get("raw_payload", {})

    # --- Gate: lat/lon presence ---
    lat = raw.get("lat")
    lon = raw.get("lon")
    if lat is None or lon is None:
        errors = [
            "raw_payload.lat and raw_payload.lon are required — "
            "hotspot coordinates are the canonical geographic identity (Section B.6)."
        ]
        logger.error(
            "[silver/openweather] Missing coordinates for hotspot='%s': %s",
            raw.get("hotspot_name", "unknown"), errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, errors, "coordinates_guard"
        )

    # --- Gate: strategic_tag presence ---
    strategic_tag = raw.get("strategic_tag", "")
    if not strategic_tag:
        errors = [
            "raw_payload.strategic_tag is required — Gold Job trigger logic "
            "(Natural_Disaster_Alert, Tech_Supply_Risk, Potential_Shipping_Delay) "
            "keys on this field (Section B.6)."
        ]
        logger.error(
            "[silver/openweather] Missing strategic_tag for hotspot='%s': %s",
            raw.get("hotspot_name", "unknown"), errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, errors, "strategic_tag_guard"
        )

    # --- Gate: wind_speed_ms numeric guard ---
    try:
        wind_speed_ms = float(raw.get("wind_speed_ms", ""))
    except (TypeError, ValueError):
        errors = [
            f"raw_payload.wind_speed_ms='{raw.get('wind_speed_ms')}' is not numeric. "
            "Producer must always emit a float (raw m/s from OWM wind.speed, Section 3.3)."
        ]
        logger.error(
            "[silver/openweather] Non-numeric wind_speed_ms for hotspot='%s': %s",
            raw.get("hotspot_name", "unknown"), errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, errors, "wind_speed_guard"
        )

    # --- Gate: weather_id in CONDITION_ID_TO_SEVERITY ---
    try:
        weather_id = int(raw.get("weather_id", ""))
    except (TypeError, ValueError):
        errors = [
            f"raw_payload.weather_id='{raw.get('weather_id')}' is not an integer. "
            "Producer must emit the raw OWM weather[0].id integer (Section 3.3)."
        ]
        logger.error(
            "[silver/openweather] Non-integer weather_id for hotspot='%s': %s",
            raw.get("hotspot_name", "unknown"), errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, errors, "weather_id_type_guard"
        )

    if weather_id not in CONDITION_ID_TO_SEVERITY:
        errors = [
            f"raw_payload.weather_id={weather_id} is not in CONDITION_ID_TO_SEVERITY. "
            "Either OWM added a new condition code (update the mapping) or the "
            "producer sent corrupt data. Route to DLQ — never silently default "
            "(Section 3.5)."
        ]
        logger.error(
            "[silver/openweather] Unknown weather_id=%d for hotspot='%s': %s",
            weather_id, raw.get("hotspot_name", "unknown"), errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, errors, "weather_id_mapping_guard"
        )

    # --- Map to Silver ---
    silver = map_openweather_to_silver(raw, envelope)

    # --- Gate: Silver schema ---
    result = validate_silver_structured_metric(silver)
    if not result.is_valid:
        logger.error(
            "[silver/openweather] Silver metric validation failed for hotspot='%s': %s",
            raw.get("hotspot_name", "unknown"), result.errors,
        )
        return DEAD_LETTER_QUEUE, _openweather_dlq_record(
            envelope, result.errors, "silver_structured_metric"
        )

    return SILVER_STRUCTURED_METRICS, silver


def _openweather_dlq_record(
    envelope: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for a failed OpenWeather Bronze message.

    Tags source_topic=BRONZE_OPENWEATHER so DLQ operators can filter by
    source when diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_OPENWEATHER,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# OpenSky — Silver Mapping & Processing (Section B.7, C.2)
# ==========================================================


def map_opensky_to_silver(raw: dict, envelope: dict) -> dict:
    """
    Map an OpenSky raw_payload to the Silver Structured Metric schema
    (Section C.2 — OpenSky row in the metadata_extension table).

    Why current_value = aircraft_density_count:
        The Aerial_Escalation_Risk trigger fires when density is >30% above
        the 30-day average. The density count is therefore the primary metric
        for momentum tracking and the natural candidate for current_value —
        same reasoning as temperature for OpenWeather and interest score for
        GoogleTrends (task_plan.md design decision 2026-04-04).

    Why external_reference_id = bounding_box_id:
        The Gold Job's keyed-state momentum operator keys by
        (source_name, external_reference_id). Using the bounding_box_id
        ensures per-box state isolation — "taiwan_strait" history is never
        mixed with "strait_of_hormuz" history (Section 4.2B). Same role
        as hotspot_name for OpenWeather.

    Why transponder_silence_events computed here (not in producer):
        The filtering rule (lat is None AND lon is None AND on_ground == False)
        interprets the semantic meaning of on_ground — domain logic, not
        structural extraction. The producer passes states_compact (5 fields
        per aircraft) so Silver can apply the rule without needing the full
        17-field state vector (Section 3.3 Service Isolation).

    Why anomaly_score is stubbed to 0.0:
        anomaly_score = max(0.0, min(1.0, change_30d)). Computing it requires
        the per-box 30-day history, which only exists in the Gold Job's Flink
        ValueState. Silver stubs to 0.0; Gold fills in the real value after
        momentum computation (Section 4.2B).

    Why momentum_block is stubbed to 0.0:
        Same reasoning as OpenWeather — prior box observations live in Flink
        keyed state. Gold Job computes the real deltas.

    Args:
        raw:      The raw_payload dict from the Bronze envelope, as produced
                  by OpenSkyProducer._build_raw_payload().
        envelope: The full Bronze Message Envelope (provides canonical event_id).

    Returns:
        Silver Structured Metric dict conforming to Section C.2.
    """
    bounding_box_id = raw.get("bounding_box_id", "")
    aircraft_count  = int(raw.get("aircraft_count", 0))
    states_compact  = raw.get("states_compact", [])
    strategic_tag   = raw.get("strategic_tag", "")
    fetch_timestamp = raw.get("fetch_timestamp", envelope.get("producer_timestamp", ""))
    mode            = raw.get("mode", "static")

    # Domain logic: transponder silence = airborne aircraft with null position.
    # on_ground is False (not None) check is intentional — None means "unknown",
    # which is ambiguous and should not count as a confirmed silence event.
    transponder_silence_events: int = sum(
        1
        for s in states_compact
        if s.get("lat") is None
        and s.get("lon") is None
        and s.get("on_ground") is False
    )

    silver: dict = {
        "schema_version": "2.0",
        "layer":          "Silver",
        "entity_type":    "Structured_Metric",
        "core_identity": {
            "metric_id":             str(uuid.uuid4()),
            "canonical_event_id":    envelope.get("event_id", ""),
            "parent_id":             "",           # No parent hierarchy for boxes
            "source_name":           "opensky",
            "external_reference_id": bounding_box_id,  # keyed-state key in Gold Job
        },
        "data_point": {
            "current_value": float(aircraft_count),   # aircraft_density_count
            "unit":          "aircraft_count",
            "status":        mode,                    # "static" | "reactive"
            "timestamp_utc": fetch_timestamp,
        },
        # Stubs — Gold Job computes actual deltas via keyed state (Section 4.2B)
        "momentum_block": {
            "change_24h":    0.0,
            "change_7d":     0.0,
            "change_30d":    0.0,
            "is_new_market": False,
        },
        # OpenSky metadata_extension (Section C.2 — OpenSky row)
        "metadata_extension": {
            "bounding_box_id":           bounding_box_id,
            "aircraft_density_count":    aircraft_count,
            "transponder_silence_events": transponder_silence_events,
            "anomaly_score":             0.0,        # stub — Gold fills in
            "strategic_tag":             strategic_tag,
            "bounding_box_coords": {
                "lat_min": raw.get("lat_min"),
                "lat_max": raw.get("lat_max"),
                "lon_min": raw.get("lon_min"),
                "lon_max": raw.get("lon_max"),
            },
        },
    }
    return silver


def process_opensky_message(
    envelope: dict,
) -> tuple[Optional[str], Optional[dict]]:
    """
    Validate and route a single OpenSky Bronze envelope to
    SILVER_STRUCTURED_METRICS.

    OpenSky has only one routing path — every valid observation is a
    structured metric. There is no social or document branch (Section 3.1:
    BRONZE_OPENSKY → SILVER_STRUCTURED_METRICS in BRONZE_TO_SILVER_ROUTING).

    Decision tree:
        1. Envelope fails validate_envelope()                  → (DEAD_LETTER_QUEUE, dlq)
        2. Payload fails validate_bronze_payload()             → (DEAD_LETTER_QUEUE, dlq)
        3. raw_payload.bounding_box_id missing or empty        → (DEAD_LETTER_QUEUE, dlq)
        4. raw_payload.aircraft_count missing or not an int    → (DEAD_LETTER_QUEUE, dlq)
        5. raw_payload lat/lon bounding coords missing         → (DEAD_LETTER_QUEUE, dlq)
        6. map_opensky_to_silver() succeeds
           + validate_silver_structured_metric() passes        → (SILVER_STRUCTURED_METRICS, silver)
        7. validate_silver_structured_metric() fails           → (DEAD_LETTER_QUEUE, dlq)

    Why aircraft_count = 0 is valid (not a DLQ condition):
        An empty bounding box (zero aircraft currently detected) is a real
        measurement — it means no aircraft are in the box at this moment.
        It should produce a Silver record with current_value=0.0, contributing
        to the 30-day baseline and potentially triggering the inverse of
        Aerial_Escalation_Risk in future analysis. Treating zero as a failure
        would corrupt the density baseline by creating gaps.

    Why bounding_box_id is a DLQ guard:
        bounding_box_id is the external_reference_id — the keyed-state key in
        the Gold Job. A record without it cannot be attributed to any box and
        would corrupt Flink's per-box history. DLQ routing makes the defect
        visible (Section 4.2B).

    Why aircraft_count integer check is a DLQ guard:
        current_value (aircraft_density_count) drives the Aerial_Escalation_Risk
        trigger via change_30d. A non-integer or None count would propagate a
        nonsense value into the momentum calculation — DLQ routing prevents
        silent data quality failures (Section 3.5).

    Why bounding box coordinates are DLQ guards:
        lat_min/lon_min/lat_max/lon_max identify the geographic extent of the
        observation. Without them, the record cannot be reconstructed for
        audit or replay, and operators cannot verify which API call produced
        the data.

    Args:
        envelope: Full Bronze Message Envelope dict as produced by
                  build_bronze_message() in kafka_utils.py.

    Returns:
        (topic, record) — topic is the Kafka topic string to emit to.
                          record is the Silver dict to publish.
    """
    set_trace_id(envelope.get("trace_id", ""))  # bind trace_id to this message's log context (Section 7.2)
    # --- Gate 1: envelope structure ---
    env_result = validate_envelope(envelope)
    if not env_result.is_valid:
        logger.warning(
            "[silver/opensky] Envelope validation failed for event_id=%s: %s",
            envelope.get("event_id", "unknown"), env_result.errors,
        )
        return DEAD_LETTER_QUEUE, _opensky_dlq_record(
            envelope, env_result.errors, "envelope"
        )

    payload = envelope.get("payload", {})

    # --- Gate 2: Bronze payload structure ---
    bronze_result = validate_bronze_payload(payload)
    if not bronze_result.is_valid:
        logger.warning(
            "[silver/opensky] Bronze payload validation failed: %s",
            bronze_result.errors,
        )
        return DEAD_LETTER_QUEUE, _opensky_dlq_record(
            envelope, bronze_result.errors, "bronze_payload"
        )

    raw = payload.get("raw_payload", {})

    # --- Gate 3: bounding_box_id presence ---
    bounding_box_id = raw.get("bounding_box_id", "")
    if not bounding_box_id:
        errors = [
            "raw_payload.bounding_box_id is required — it is the Gold Job's "
            "keyed-state key (external_reference_id) for per-box momentum tracking "
            "(Section 4.2B)."
        ]
        logger.error(
            "[silver/opensky] Missing bounding_box_id: %s", errors,
        )
        return DEAD_LETTER_QUEUE, _opensky_dlq_record(
            envelope, errors, "bounding_box_id_guard"
        )

    # --- Gate 4: aircraft_count integer guard ---
    # aircraft_count=0 is valid (empty box). Only reject non-integer or absent.
    raw_count = raw.get("aircraft_count")
    if raw_count is None or not isinstance(raw_count, int):
        errors = [
            f"raw_payload.aircraft_count='{raw_count}' is missing or not an integer. "
            "Producer must always emit an integer count (0 = empty box is valid, "
            "Section 3.3 Service Isolation, task_plan.md design decision 2026-04-04)."
        ]
        logger.error(
            "[silver/opensky] Invalid aircraft_count for box='%s': %s",
            bounding_box_id, errors,
        )
        return DEAD_LETTER_QUEUE, _opensky_dlq_record(
            envelope, errors, "aircraft_count_guard"
        )

    # --- Gate 5: bounding box coordinate presence ---
    missing_coords = [
        f for f in ("lat_min", "lat_max", "lon_min", "lon_max")
        if raw.get(f) is None
    ]
    if missing_coords:
        errors = [
            f"raw_payload is missing bounding box coordinates: {missing_coords}. "
            "These fields are required for audit, replay, and DLQ investigation "
            "(Section C.1, C.2)."
        ]
        logger.error(
            "[silver/opensky] Missing bounding box coordinates for box='%s': %s",
            bounding_box_id, errors,
        )
        return DEAD_LETTER_QUEUE, _opensky_dlq_record(
            envelope, errors, "bounding_box_coords_guard"
        )

    # --- Map to Silver ---
    silver = map_opensky_to_silver(raw, envelope)

    # --- Gate 6: Silver schema validation ---
    result = validate_silver_structured_metric(silver)
    if not result.is_valid:
        logger.error(
            "[silver/opensky] Silver metric validation failed for box='%s': %s",
            bounding_box_id, result.errors,
        )
        return DEAD_LETTER_QUEUE, _opensky_dlq_record(
            envelope, result.errors, "silver_structured_metric"
        )

    return SILVER_STRUCTURED_METRICS, silver


def _opensky_dlq_record(
    envelope: dict, errors: list[str], failed_stage: str
) -> dict:
    """
    Build a DLQ record for a failed OpenSky Bronze message.

    Tags source_topic=BRONZE_OPENSKY so DLQ operators can filter by
    source when diagnosing schema failures (Section 3.5).
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      "Silver",
        "failed_stage":      failed_stage,
        "source_topic":      BRONZE_OPENSKY,
        "validation_errors": errors,
        "original_message":  envelope,
    }


# ==========================================================
# Flink Pipeline — requires PyFlink (Docker container only)
# ==========================================================

if PYFLINK_AVAILABLE:

    class GenericSilverProcessFunction(ProcessFunction):
        """
        Single-output Silver ProcessFunction for all sources except Polymarket.

        Polymarket Bronze messages carry two payload types (price_update and
        comment) that route to two different Silver topics — it uses the
        dedicated PolymarketSilverProcessFunction below.  Every other source
        maps exactly one Bronze topic to exactly one Silver topic, so a single
        reusable class suffices.

        The process_fn argument must follow the signature:
            (envelope_dict) -> (topic_str | None, record_dict | None)
        where topic_str is the target Silver topic name or DEAD_LETTER_QUEUE.
        """

        def __init__(self, process_fn):
            self._process_fn = process_fn

        def process_element(self, value: str, _ctx: ProcessFunction.Context):
            try:
                envelope = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                yield (DLQ_TAG, json.dumps({"parse_error": str(exc)}))
                return

            topic, record = self._process_fn(envelope)

            if topic is None:
                return  # intentional skip (empty batch, low-signal guard, etc.)
            if topic == DEAD_LETTER_QUEUE:
                yield (DLQ_TAG, json.dumps(record))
            else:
                yield ("main", json.dumps(record))

    class PolymarketSilverProcessFunction(ProcessFunction):
        """
        Flink ProcessFunction that dispatches each Polymarket Bronze message
        to the correct Silver output or DLQ side output.

        Output routing via tagged tuples — PyFlink 1.19 Python ProcessFunction
        context has no ctx.output() / side-output API (Java-only).
        Each element is yielded as (tag, json_str):
            ("main",        json) → SILVER_STRUCTURED_METRICS (price_update records)
            (SOCIAL_TAG,    json) → SILVER_SOCIAL_PULSE        (comment records)
            (DLQ_TAG,       json) → DEAD_LETTER_QUEUE          (validation failures)
        Downstream filter+map in build_pipeline() splits the stream before sinking.
        """

        def process_element(self, value: str, _ctx: ProcessFunction.Context):
            try:
                envelope = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                # Malformed bytes from Kafka — route straight to DLQ
                dlq = _dlq_record({}, [f"JSON parse error: {exc}"], "deserialise")
                yield (DLQ_TAG, json.dumps(dlq))
                return

            topic, record = process_polymarket_message(envelope)

            if topic is None:
                # Intentional skip (empty comment batch) — no output
                return
            if topic == DEAD_LETTER_QUEUE:
                yield (DLQ_TAG, json.dumps(record))
            elif topic == SILVER_SOCIAL_PULSE:
                yield (SOCIAL_TAG, json.dumps(record))
            else:
                # Main output → SILVER_STRUCTURED_METRICS
                yield ("main", json.dumps(record))

    def build_pipeline(env: "StreamExecutionEnvironment") -> None:
        """
        Wire the Silver Flink pipeline for ALL nine Bronze sources.

        Nine independent sub-pipelines share the same environment:
            1. BRONZE_FRED          → SILVER_STRUCTURED_METRICS (+ DLQ)
            2. BRONZE_NEWSAPI       → SILVER_GLOBAL_NEWS        (+ DLQ)
            3. BRONZE_ARXIV         → SILVER_GLOBAL_NEWS        (+ DLQ)
            4. BRONZE_TELEGRAM      → SILVER_GLOBAL_NEWS        (+ DLQ)
            5. BRONZE_HACKERNEWS    → SILVER_SOCIAL_PULSE       (+ DLQ)
            6. BRONZE_GOOGLETRENDS  → SILVER_STRUCTURED_METRICS (+ DLQ)
            7. BRONZE_OPENWEATHER   → SILVER_STRUCTURED_METRICS (+ DLQ)
            8. BRONZE_OPENSKY       → SILVER_STRUCTURED_METRICS (+ DLQ)
            9. BRONZE_POLYMARKET    → SILVER_STRUCTURED_METRICS + SILVER_SOCIAL_PULSE (+ DLQ)

        Sources 1–8 use GenericSilverProcessFunction (single main output + DLQ).
        Source 9 (Polymarket) uses PolymarketSilverProcessFunction (dual-output:
        price_update → structured_metrics, comment → social_pulse, invalid → DLQ).

        Checkpointing (Section 4.3):
            - EXACTLY_ONCE, 60-second interval.
            - Configured via docker-compose.yml FLINK_PROPERTIES.

        Args:
            env: StreamExecutionEnvironment (caller is responsible for
                 configuring bootstrap servers via pipeline properties or
                 env vars injected by Docker Compose).
        """
        from config.settings import KAFKA_BOOTSTRAP_SERVERS

        _tuple_type = Types.TUPLE([Types.STRING(), Types.STRING()])

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

        def _tag_split(stream, tag: str):
            """Filter by tag then strip the tag field, leaving a plain JSON string."""
            return (
                stream
                .filter(lambda x: x[0] == tag)
                .map(lambda x: x[1], output_type=Types.STRING())
            )

        def _wire_simple(
            bronze_topic: str,
            group_id: str,
            operator_name: str,
            process_fn,
            silver_topic: str,
        ) -> None:
            """
            Wire a single-output Silver branch: bronze_topic → silver_topic + DLQ.

            Used for all sources except Polymarket, which needs the dual-output
            path (structured_metrics + social_pulse) in PolymarketSilverProcessFunction.

            Args:
                bronze_topic:   Kafka topic to consume from (Bronze layer).
                group_id:       Consumer group ID for this branch.
                operator_name:  Human-readable Flink operator label.
                process_fn:     Silver process function (envelope → (topic, record)).
                silver_topic:   Silver Kafka topic to sink valid records to.
            """
            stream = env.from_source(
                _kafka_source(bronze_topic, group_id),
                WatermarkStrategy.no_watermarks(),
                operator_name,
            )
            processed = stream.process(
                GenericSilverProcessFunction(process_fn),
                output_type=_tuple_type,
            )
            _tag_split(processed, "main").sink_to(_kafka_sink(silver_topic))
            _tag_split(processed, DLQ_TAG).sink_to(_kafka_sink(DEAD_LETTER_QUEUE))

        # --- Sources 1–8: single-output branches ---
        _wire_simple(BRONZE_FRED,         "flink-silver-fred",         "fred-bronze-source",         process_fred_message,         SILVER_STRUCTURED_METRICS)
        _wire_simple(BRONZE_NEWSAPI,      "flink-silver-newsapi",      "newsapi-bronze-source",      process_newsapi_message,      SILVER_GLOBAL_NEWS)
        _wire_simple(BRONZE_ARXIV,        "flink-silver-arxiv",        "arxiv-bronze-source",        process_arxiv_message,        SILVER_GLOBAL_NEWS)
        _wire_simple(BRONZE_TELEGRAM,     "flink-silver-telegram",     "telegram-bronze-source",     process_telegram_message,     SILVER_GLOBAL_NEWS)
        _wire_simple(BRONZE_HACKERNEWS,   "flink-silver-hackernews",   "hackernews-bronze-source",   process_hackernews_message,   SILVER_SOCIAL_PULSE)
        _wire_simple(BRONZE_GOOGLETRENDS, "flink-silver-googletrends", "googletrends-bronze-source", process_googletrends_message, SILVER_STRUCTURED_METRICS)
        _wire_simple(BRONZE_OPENWEATHER,  "flink-silver-openweather",  "openweather-bronze-source",  process_openweather_message,  SILVER_STRUCTURED_METRICS)
        _wire_simple(BRONZE_OPENSKY,      "flink-silver-opensky",      "opensky-bronze-source",      process_opensky_message,      SILVER_STRUCTURED_METRICS)

        # --- Source 9: Polymarket dual-output branch ---
        # Polymarket Bronze contains both price_update (→ SILVER_STRUCTURED_METRICS)
        # and comment (→ SILVER_SOCIAL_PULSE) payloads in the same topic. The
        # PolymarketSilverProcessFunction dispatches via three tags:
        #   "main"     → SILVER_STRUCTURED_METRICS
        #   SOCIAL_TAG → SILVER_SOCIAL_PULSE
        #   DLQ_TAG    → DEAD_LETTER_QUEUE
        poly_stream = env.from_source(
            _kafka_source(BRONZE_POLYMARKET, "flink-silver-polymarket"),
            WatermarkStrategy.no_watermarks(),
            "polymarket-bronze-source",
        )
        poly_processed = poly_stream.process(
            PolymarketSilverProcessFunction(),
            output_type=_tuple_type,
        )
        _tag_split(poly_processed, "main")     .sink_to(_kafka_sink(SILVER_STRUCTURED_METRICS))
        _tag_split(poly_processed, SOCIAL_TAG) .sink_to(_kafka_sink(SILVER_SOCIAL_PULSE))
        _tag_split(poly_processed, DLQ_TAG)    .sink_to(_kafka_sink(DEAD_LETTER_QUEUE))


def main() -> None:
    """
    Entry point for Flink job submission inside the Docker container.

    Submit via Flink CLI:
        flink run -py processing/silver_job.py

    Requires PyFlink to be available (apache/flink:1.19-java11 image).
    Running this locally without PyFlink will print an informative error.
    """
    if not PYFLINK_AVAILABLE:
        print(
            "[silver_job] PyFlink is not installed in this environment.\n"
            "This job must be submitted to the Flink JobManager container.\n"
            "See docker-compose.yml — flink-jobmanager service (Section 8.2)."
        )
        return

    env = StreamExecutionEnvironment.get_execution_environment()
    env.set_parallelism(2)
    build_pipeline(env)
    env.execute("anizai-silver-polymarket")


if __name__ == "__main__":
    main()
