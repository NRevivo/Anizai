"""
Silver Job — Flink Standardization & Dual-Persistence (Section 4.1).

Consumes raw Bronze envelopes and produces validated Silver records on the
correct Silver family topic. Two source branches are implemented:

Polymarket (dual payload_type in one Bronze topic):
    price_update → map_price_update_to_silver() → process.silver.structured_metrics
    comment      → map_comment_to_silver()       → process.silver.social_pulse
    invalid      → dead-letter-queue              (never silently dropped, Section 3.5)

FRED (single payload_type — all observations are structured_metrics):
    observation  → map_fred_observation_to_silver() → process.silver.structured_metrics
    invalid      → dead-letter-queue

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
from typing import Optional

from config.kafka_topics import (
    BRONZE_FRED,
    BRONZE_POLYMARKET,
    DEAD_LETTER_QUEUE,
    SILVER_SOCIAL_PULSE,
    SILVER_STRUCTURED_METRICS,
)
from processing.deduplication import hash_social_batch
from utils.validators import (
    validate_bronze_payload,
    validate_envelope,
    validate_silver_social,
    validate_silver_structured_metric,
    route_to_dlq,
)

logger = logging.getLogger(__name__)

# ==========================================================
# PyFlink — optional import (Docker container only, Section 8.2)
# ==========================================================
# PyFlink is NOT installed in the local venv (removed from requirements.txt).
# It is pre-installed inside the apache/flink:1.19-java11 Docker image.
# All code below that touches pyflink is gated by PYFLINK_AVAILABLE so
# this module remains importable in local pytest environments.

try:
    from pyflink.common import SimpleStringSchema, WatermarkStrategy
    from pyflink.datastream import StreamExecutionEnvironment
    from pyflink.datastream.connectors.kafka import (
        KafkaOffsetsInitializer,
        KafkaRecordSerializationSchema,
        KafkaSink,
        KafkaSource,
    )
    from pyflink.datastream.functions import ProcessFunction
    from pyflink.datastream.output_tag import OutputTag
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
        # Polymarket metadata_extension fields (Section C.2 table)
        "metadata_extension": {
            "liquidity_pool_tvl": float(raw.get("liquidity_usd", 0.0)),
            "bid_ask_spread":     0.0,
            "24h_volume":         float(raw.get("volume_24h_usd", 0.0)),
            "is_divergent":       False,
            "whale_alert":        bool(raw.get("whale_alert", False)),
            "resolution_rules":   "",
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
# Flink Pipeline — requires PyFlink (Docker container only)
# ==========================================================

if PYFLINK_AVAILABLE:

    class PolymarketSilverProcessFunction(ProcessFunction):
        """
        Flink ProcessFunction that dispatches each Polymarket Bronze message
        to the correct Silver output or DLQ side output.

        Output routing via OutputTag (Section 4.3):
            Main output   → SILVER_STRUCTURED_METRICS (price_update records)
            social_pulse  → SILVER_SOCIAL_PULSE        (comment records)
            dlq           → DEAD_LETTER_QUEUE          (validation failures)

        Why ProcessFunction over MapFunction: MapFunction has a single output
        stream. ProcessFunction supports side outputs, enabling routing to
        three distinct Kafka sinks from one operator without branching the
        input stream or re-processing messages.
        """

        _social_tag = OutputTag(SOCIAL_TAG)
        _dlq_tag    = OutputTag(DLQ_TAG)

        def process_element(self, value: str, ctx: ProcessFunction.Context):
            try:
                envelope = json.loads(value)
            except (json.JSONDecodeError, TypeError) as exc:
                # Malformed bytes from Kafka — route straight to DLQ
                dlq = _dlq_record({}, [f"JSON parse error: {exc}"], "deserialise")
                ctx.output(self._dlq_tag, json.dumps(dlq))
                return

            topic, record = process_polymarket_message(envelope)

            if topic is None:
                # Intentional skip (empty comment batch) — no output
                return
            if topic == DEAD_LETTER_QUEUE:
                ctx.output(self._dlq_tag, json.dumps(record))
            elif topic == SILVER_SOCIAL_PULSE:
                ctx.output(self._social_tag, json.dumps(record))
            else:
                # Main output → SILVER_STRUCTURED_METRICS
                yield json.dumps(record)

    def build_pipeline(env: "StreamExecutionEnvironment") -> None:
        """
        Wire the Flink pipeline for the Polymarket Silver branch.

        Checkpointing (Section 4.3):
            - EXACTLY_ONCE, 60-second interval.
            - Configured via docker-compose.yml FLINK_PROPERTIES so the
              env settings here serve as in-code documentation of intent.

        Kafka sources / sinks use SimpleStringSchema (raw JSON strings).
        NDJSON deserialisation happens inside the ProcessFunction, keeping
        the Kafka connector layer schema-agnostic.

        Args:
            env: StreamExecutionEnvironment (caller is responsible for
                 configuring bootstrap servers via pipeline properties or
                 env vars injected by Docker Compose).
        """
        from config.settings import KAFKA_BOOTSTRAP_SERVERS

        # --- Source ---
        source = (
            KafkaSource.builder()
            .set_bootstrap_servers(KAFKA_BOOTSTRAP_SERVERS)
            .set_topics(BRONZE_POLYMARKET)
            .set_group_id("flink-silver-polymarket")
            .set_starting_offsets(KafkaOffsetsInitializer.earliest())
            .set_value_only_deserializer(SimpleStringSchema())
            .build()
        )

        stream = env.from_source(
            source,
            WatermarkStrategy.no_watermarks(),
            "polymarket-bronze-source",
        )

        # --- ProcessFunction with side outputs ---
        process_fn  = PolymarketSilverProcessFunction()
        social_tag  = process_fn._social_tag
        dlq_tag     = process_fn._dlq_tag

        processed   = stream.process(process_fn)
        social_side = processed.get_side_output(social_tag)
        dlq_side    = processed.get_side_output(dlq_tag)

        # --- Sinks ---
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

        processed.sink_to(_kafka_sink(SILVER_STRUCTURED_METRICS))
        social_side.sink_to(_kafka_sink(SILVER_SOCIAL_PULSE))
        dlq_side.sink_to(_kafka_sink(DEAD_LETTER_QUEUE))


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
