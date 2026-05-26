"""
Kafka Utilities — Producer/Consumer factories & NDJSON serialization.

Why centralized here: prevents duplicated Kafka connection setup across
11 producer modules and Flink jobs. Single point of change for all
transport-layer concerns (Section 3.2).

Why NDJSON: mandated by Section 3.4. NDJSON (Newline Delimited JSON)
serializes each message as a single JSON line terminated by \\n.
This format is compatible with streaming log parsers, GCS Bronze Data
Lake storage, and Flink's StreamingFileSink for cold replay.

Message anatomy (Section 3.2):
    Kafka message = NDJSON({ envelope } + { bronze_payload as "payload" })

References:
    - Section 3.2: DRY — Kafka helpers must live in utils/kafka_utils.py
    - Section 3.4: NDJSON Serialization mandate
    - Section 3.2: Message Envelope & Metadata (event_id, trace_id, etc.)
    - Section C.1: Bronze Schema (ingestion_id, source_name, raw_payload, …)
    - Section 7.2: Distributed tracing via trace_id
"""

import json
import time
import uuid
from datetime import datetime, timezone
from typing import Any

from kafka import KafkaConsumer, KafkaProducer
from kafka.errors import KafkaError

from config.settings import KAFKA_BOOTSTRAP_SERVERS
from utils.logging_config import set_trace_id

# ==========================================================
# Version sentinel — embed in every Bronze message so issues
# can be traced back to the exact producer code release.
# Bump this string when making breaking changes to the envelope.
# ==========================================================
PRODUCER_VERSION = "1.0.0"
ENVELOPE_SCHEMA_VERSION = "1.0"


# ==========================================================
# NDJSON Wire Format (Section 3.4)
# ==========================================================

def ndjson_serializer(data: dict) -> bytes:
    """
    Serialize a dict to NDJSON bytes (JSON line + newline).

    Why \\n suffix: NDJSON spec requires each record to be terminated
    by a newline, making files streamable and GCS-appendable without
    loading the entire object into memory.
    """
    return (json.dumps(data, ensure_ascii=False, default=str) + "\n").encode("utf-8")


def ndjson_deserializer(data: bytes) -> dict:
    """
    Deserialize NDJSON bytes back to a dict.

    Strips the trailing newline before parsing — safe even if the
    consumer receives bytes without one (defensive strip).
    """
    return json.loads(data.decode("utf-8").strip())


# ==========================================================
# Message Envelope (Section 3.2)
# ==========================================================

def build_envelope(payload: dict, source_name: str) -> dict:
    """
    Wrap a Bronze payload in the standard Message Envelope.

    The envelope provides a full "Paper Trail" from Producer to Gold Layer:
        - event_id:           UUIDv4 shared across Bronze → Silver → Gold
                              for the same logical event.
        - trace_id:           Correlation ID for end-to-end debugging
                              (Section 7.2). Generated once at the Producer
                              and propagated unchanged through all layers.
        - producer_timestamp: ISO8601 UTC capture time — immutable audit field.
        - schema_version:     Enables forward-compatible schema evolution
                              without breaking existing consumers.
        - source_name:        Identifies which producer emitted this message.
        - payload:            The Bronze Schema object (Section C.1).

    Args:
        payload:     A Bronze Schema dict (see build_bronze_payload()).
        source_name: The canonical source identifier (e.g., "polymarket").

    Returns:
        Complete envelope dict ready for NDJSON serialization.
    """
    now      = datetime.now(timezone.utc).isoformat()
    trace_id = str(uuid.uuid4())
    set_trace_id(trace_id)   # bind trace_id to context so the first producer log line carries it (Section 7.2)
    return {
        "event_id":           str(uuid.uuid4()),
        "trace_id":           trace_id,
        "producer_timestamp": now,
        "schema_version":     ENVELOPE_SCHEMA_VERSION,
        "source_name":        source_name,
        "payload":            payload,
    }


# ==========================================================
# Bronze Schema Builder (Section C.1)
# ==========================================================

def build_bronze_payload(
    source_name: str,
    source_endpoint: str,
    raw_payload: Any,
    http_status_code: int = 200,
    request_duration_ms: int = 0,
) -> dict:
    """
    Construct a Bronze Schema object (Section C.1).

    Every Producer calls this before calling build_envelope().
    This enforces the Bronze contract: raw data must be preserved
    exactly as received — no transformation allowed at this layer
    (Section 3.3 Service Isolation).

    Args:
        source_name:         Human-readable source (e.g., "NewsAPI").
        source_endpoint:     Specific URL or channel fetched.
        raw_payload:         Raw object as received from the API — never modified.
        http_status_code:    HTTP response code for audit purposes.
        request_duration_ms: Round-trip fetch duration for latency tracking.

    Returns:
        Bronze Schema dict.
    """
    return {
        "ingestion_id":        str(uuid.uuid4()),
        "source_name":         source_name,
        "source_endpoint":     source_endpoint,
        "ingestion_timestamp": datetime.now(timezone.utc).isoformat(),
        "producer_version":    PRODUCER_VERSION,
        "raw_payload":         raw_payload,
        "metadata": {
            "http_status_code":    http_status_code,
            "request_duration_ms": request_duration_ms,
        },
    }


def build_bronze_message(
    source_name: str,
    source_endpoint: str,
    raw_payload: Any,
    http_status_code: int = 200,
    request_duration_ms: int = 0,
) -> dict:
    """
    Convenience function: build Bronze payload + wrap in envelope in one call.

    This is the single function most producers will call. Returns the
    complete dict ready to pass to producer.send(..., value=msg).

    Example:
        msg = build_bronze_message(
            source_name="newsapi",
            source_endpoint="https://newsapi.org/v2/top-headlines?category=business",
            raw_payload=article_dict,
            http_status_code=200,
            request_duration_ms=342,
        )
        producer.send(BRONZE_NEWSAPI, value=msg)
    """
    bronze_payload = build_bronze_payload(
        source_name=source_name,
        source_endpoint=source_endpoint,
        raw_payload=raw_payload,
        http_status_code=http_status_code,
        request_duration_ms=request_duration_ms,
    )
    return build_envelope(bronze_payload, source_name)


# ==========================================================
# Bootstrap-Servers Resolver — Windows IPv4 Guard (KG-PHASE8-25)
# ==========================================================
# localhost on Windows resolves dual-stack (::1 first, then 127.0.0.1).
# kafka-python-ng's bootstrap-to-coordinator transition triggers a
# selector unregister race when the IPv6 bootstrap socket closes
# mid-poll — `ValueError: Invalid file descriptor: -1` raised inside
# `_selector.unregister(key.fileobj)` because the fileobj is the
# already-closed IPv6 socket (fd=-1).
#
# Resolving `localhost` → `127.0.0.1` at the factory boundary forces
# kafka-python-ng onto a single-stack IPv4 path and avoids the race
# entirely. Linux containers use the docker-internal name `kafka:29092`,
# so this guard has zero effect on production / cloud deploys.
#
# Surfaced by T23.9 Gate 3 (Sprint 23), 2026-05-26. Race likely
# present since Gate 3 first ran but masked by earlier failure modes
# (cold-start metadata timeout, then __consumer_offsets hang) that
# prevented the consumer from reaching the bootstrap-to-coordinator
# transition. See KG-PHASE8-25 in task_plan.md.
#
# Defensive consistency: applied to both make_consumer (where the race
# was observed) and make_producer (where the producer's connection
# lifecycle currently does not expose the race, but kafka-python-ng
# internals could shift in a future upgrade).

def _resolve_bootstrap_servers(value: str) -> str:
    """
    Resolve a `localhost`-based single-host bootstrap_servers value to
    its IPv4 equivalent, leaving every other input unchanged.

    Mapping:
        "localhost"           → "127.0.0.1:9092"  (default port appended)
        "localhost:9092"      → "127.0.0.1:9092"
        "localhost:29092"     → "127.0.0.1:29092"  (preserves explicit port)
        anything else         → unchanged
            (includes "kafka:29092", IP literals, and any CSV multi-host
            string like "localhost:9092,kafka:29092" which is passed
            through verbatim — caller is responsible for those shapes)

    Why a helper, not an inline `if`: the single-port literal `9092`
    would rot the first time someone runs local-dev on a custom port.
    Parsing the port keeps the guard correct under reconfiguration.
    """
    if value == "localhost":
        return "127.0.0.1:9092"
    # Only rewrite when the input is a single `localhost:<port>` host;
    # CSV multi-host strings (which contain ',') and any input that
    # doesn't begin with the literal "localhost:" prefix pass through.
    if (
        value.startswith("localhost:")
        and "," not in value
        and value.count(":") == 1
    ):
        port = value.split(":", 1)[1]
        if port.isdigit():
            return f"127.0.0.1:{port}"
    return value


# ==========================================================
# Producer Factory (Section 3.2)
# ==========================================================

def make_producer(
    bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS,
    acks: str = "all",
    retries: int = 5,
    request_timeout_ms: int = 30_000,
) -> KafkaProducer:
    """
    Create a KafkaProducer pre-configured for NDJSON serialization.

    Why acks='all': ensures the broker leader and all in-sync replicas
    acknowledge the write before returning — prevents data loss on
    broker failure. Required for Bronze Layer integrity (Section 2.3).

    Why retries=5: transient network glitches during long-running
    producer loops should not drop messages. The idempotent producer
    setting prevents duplicates on retry.

    Args:
        bootstrap_servers: Kafka broker address (from settings, overridable
                           for tests or Docker-internal consumers).
        acks:              Acknowledgement policy ('all' for durability).
        retries:           Retry count on transient failures.
        request_timeout_ms: Per-request timeout in milliseconds.

    Returns:
        Configured KafkaProducer instance.

    Raises:
        KafkaError: If the broker is unreachable at call time.
    """
    return KafkaProducer(
        bootstrap_servers=_resolve_bootstrap_servers(bootstrap_servers),
        value_serializer=ndjson_serializer,
        key_serializer=lambda k: k.encode("utf-8") if isinstance(k, str) else k,
        acks=acks,
        retries=retries,
        request_timeout_ms=request_timeout_ms,
        compression_type="gzip",    # reduces network + storage overhead
    )


# ==========================================================
# Consumer Factory (Section 3.2)
# ==========================================================

def make_consumer(
    *topics: str,
    group_id: str,
    bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS,
    auto_offset_reset: str = "earliest",
    enable_auto_commit: bool = False,
) -> KafkaConsumer:
    """
    Create a KafkaConsumer pre-configured for NDJSON deserialization.

    Why enable_auto_commit=False: Flink manages offsets explicitly via
    checkpointing (Section 4.3). Manual commit ensures exactly-once
    semantics — an offset is only committed after the message has been
    successfully processed and persisted.

    Args:
        *topics:           One or more topic names to subscribe to.
        group_id:          Consumer group ID (use descriptive names like
                           "flink-silver-job" or "flink-gold-job").
        bootstrap_servers: Kafka broker address.
        auto_offset_reset: Where to start consuming if no committed offset
                           exists ('earliest' = replay from beginning,
                           useful for Bronze 7-day replay window).
        enable_auto_commit: Disabled — offsets committed manually after
                            processing confirmation.

    Returns:
        Configured KafkaConsumer subscribed to the given topics.
    """
    consumer = KafkaConsumer(
        *topics,
        bootstrap_servers=_resolve_bootstrap_servers(bootstrap_servers),
        group_id=group_id,
        value_deserializer=ndjson_deserializer,
        key_deserializer=lambda k: k.decode("utf-8") if k else None,
        auto_offset_reset=auto_offset_reset,
        enable_auto_commit=enable_auto_commit,
    )
    return consumer


# ==========================================================
# Timing Helper
# ==========================================================

def timed_request(request_fn):
    """
    Execute a callable and return (result, duration_ms).

    Producers use this to capture request_duration_ms for the Bronze
    metadata block (Section C.1) without cluttering fetch logic.

    Example:
        result, duration_ms = timed_request(lambda: requests.get(url))
    """
    start = time.monotonic()
    result = request_fn()
    duration_ms = int((time.monotonic() - start) * 1000)
    return result, duration_ms
