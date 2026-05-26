"""
Reactive Ingestion Trigger Consumer — On-Demand RAG Agent Dispatcher (Section 2.4).

Consumes messages from the `ingestion_triggers` Kafka topic (written by the RAG
agent when a knowledge gap is detected during inference) and dispatches each
trigger to the appropriate producer's run_reactive() method.

Four sources support reactive mode (Section 2.4):
    googletrends — Interest Over Time for specific keywords (Section B.5)
    openweather  — Bounded hotspot polling window (Section B.6)
    opensky      — Bounded callsign tracking window (Section B.7)
    newsapi      — On-demand keyword-driven article fetch (Sprint 23, §B.4)

Trigger payload schemas:

    GoogleTrends:
        {"source": "googletrends", "keywords": ["oil price", "OPEC"],
         "geo": "US"}
        Required: source, keywords (non-empty list)
        Optional: geo (default "US"), timeframe

    OpenWeather:
        {"source": "openweather", "hotspot_names": ["Strait_of_Hormuz"]}
        Required: source, hotspot_names (non-empty list)
        Optional: window_minutes, poll_interval_sec

    OpenSky:
        {"source": "opensky", "target_callsigns": ["SWR100", "AFR447"]}
        Required: source, target_callsigns (non-empty list)
        Optional: window_minutes, poll_interval_sec

    NewsAPI (Sprint 23):
        {"source": "newsapi", "keywords": ["iran nuclear", "OPEC"],
         "time_window_days": 7}
        Required: source, keywords (non-empty list)
        Optional: time_window_days (default 7)
        Emitted by the Agentic Hub when the vault is insufficient for a
        forecast; dispatcher invokes NewsAPIProducer.run_reactive() which
        makes one getArticles call per keyword and dedupes by URL.

Invalid payloads (unknown source, missing required fields, empty lists):
    → routed to dead-letter-queue with errors (Section 3.5).

Architecture:
    - Consumer is injected into run() for testability (same pattern as
      translate_to_english() client injection — Section 3.2 DRY).
    - Producers are lazy-imported inside _build_producer() to avoid
      importing heavy Pytrends / requests / paramiko dependencies at
      module load time.
    - run_reactive() is launched in a daemon thread per trigger so the
      consumer loop is never blocked by a 30-60 min polling window.
    - Manual offset commit after dispatch() succeeds — offset is NOT
      committed for invalid triggers routed to DLQ, so the raw trigger
      message is replayable from Kafka if the DLQ write fails.

References:
    - Section 2.4:  Reactive Ingestion — ingestion_triggers Kafka topic
    - Section B.5:  Google Trends reactive mode (Interest Over Time)
    - Section B.6:  OpenWeather reactive mode (bounded hotspot window)
    - Section B.7:  OpenSky reactive mode (callsign tracking window)
    - Section 3.5:  Dead-Letter Queue — invalid triggers must not be silently dropped
    - Section 3.2:  DRY — Kafka helpers in utils/kafka_utils.py
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import datetime, timezone
from typing import Any

from config.kafka_topics import DEAD_LETTER_QUEUE, INGESTION_TRIGGERS
from utils.kafka_utils import make_consumer, make_producer
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# Source Registry
# ==========================================================

# Sources that support reactive mode (Section 2.4).
# newsapi added in Sprint 23 — see agentic_hub_implementation_phase8_revised.md §Sprint 23.
VALID_SOURCES: frozenset[str] = frozenset({
    "googletrends",
    "openweather",
    "opensky",
    "newsapi",
})

# Per-source required payload fields (beyond "source").
# Values are validated to be non-empty lists.
_REQUIRED_LIST_FIELDS: dict[str, str] = {
    "googletrends": "keywords",
    "openweather":  "hotspot_names",
    "opensky":      "target_callsigns",
    "newsapi":      "keywords",
}


# ==========================================================
# Validation
# ==========================================================

def validate_trigger(payload: Any) -> list[str]:
    """
    Validate a trigger payload from the ingestion_triggers topic (Section 2.4).

    Returns a list of human-readable error strings. An empty list means the
    payload is valid. Called before dispatch() — invalid payloads are routed
    to the dead-letter-queue instead of dispatched.

    Rules:
        1. payload must be a dict.
        2. 'source' must be present and a known reactive source.
        3. The source-specific required list field must be present and non-empty.

    Args:
        payload: Deserialized Kafka message value (expected: dict).

    Returns:
        List of error strings. Empty = valid.
    """
    errors: list[str] = []

    if not isinstance(payload, dict):
        errors.append(
            f"Trigger payload must be a dict, got {type(payload).__name__}."
        )
        return errors  # no further checks possible

    source = payload.get("source", "")
    if not source:
        errors.append("'source' field is missing or empty.")
        return errors

    if source not in VALID_SOURCES:
        errors.append(
            f"Unknown source '{source}'. "
            f"Supported sources: {sorted(VALID_SOURCES)}."
        )
        return errors

    required_field = _REQUIRED_LIST_FIELDS[source]
    value = payload.get(required_field)
    if not value:
        errors.append(
            f"'{required_field}' is required for source '{source}' "
            f"and must be a non-empty list."
        )
    elif not isinstance(value, list):
        errors.append(
            f"'{required_field}' must be a list, got {type(value).__name__}."
        )
    elif len(value) == 0:
        errors.append(
            f"'{required_field}' must not be an empty list."
        )

    return errors


# ==========================================================
# DLQ Record Builder
# ==========================================================

def _build_dlq_record(payload: Any, errors: list[str]) -> dict:
    """
    Build a dead-letter-queue record for an invalid trigger payload (Section 3.5).

    Mirrors the DLQ envelope format from silver_job._dlq_record() so DLQ
    consumers have a consistent structure regardless of which pipeline stage
    produced the failure.

    Args:
        payload: The raw trigger payload that failed validation.
        errors:  List of validation error strings.

    Returns:
        DLQ record dict ready for Kafka emission.
    """
    return {
        "dlq_id":            str(uuid.uuid4()),
        "failed_stage":      "trigger_validation",
        "source_topic":      INGESTION_TRIGGERS,
        "original_payload":  payload,
        "errors":            errors,
        "dlq_timestamp":     datetime.now(timezone.utc).isoformat(),
    }


# ==========================================================
# Producer Factory (lazy-import to avoid heavy dependency load)
# ==========================================================

def _build_producer(source: str) -> Any:
    """
    Lazy-import and instantiate the reactive-capable producer for a source.

    Lazy import is used because GoogleTrends (Pytrends), OpenWeather (requests),
    and OpenSky (requests + auth) each pull in external dependencies that are
    not needed at module load time — only when a trigger is actually received.

    Args:
        source: One of "googletrends", "openweather", "opensky".

    Returns:
        Instantiated producer object with a run_reactive() method.

    Raises:
        ValueError: If source is not in VALID_SOURCES (should not happen —
                    validate_trigger() guards this before dispatch()).
    """
    if source == "googletrends":
        from ingestion.googletrends_producer import GoogleTrendsProducer
        return GoogleTrendsProducer()
    if source == "openweather":
        from ingestion.openweather_producer import OpenWeatherProducer
        return OpenWeatherProducer()
    if source == "opensky":
        from ingestion.opensky_producer import OpenSkyProducer
        return OpenSkyProducer()
    if source == "newsapi":
        from ingestion.newsapi_producer import NewsAPIProducer
        return NewsAPIProducer()
    raise ValueError(f"[trigger_consumer] No producer registered for source '{source}'")


# ==========================================================
# Dispatcher
# ==========================================================

def dispatch(trigger: dict) -> threading.Thread:
    """
    Dispatch a validated trigger to the appropriate producer's run_reactive().

    Runs run_reactive() in a daemon thread so the consumer loop continues
    accepting new triggers while a long-running reactive window is in progress.
    Daemon threads are automatically killed when the main process exits —
    this is intentional: an in-progress reactive window should not prevent
    clean consumer shutdown.

    Args:
        trigger: Validated trigger dict (already passed validate_trigger()).

    Returns:
        The running Thread. Callers can join() it if synchronous completion
        is needed (e.g., in tests with a mock producer).

    Why a new thread per trigger (not a thread pool): reactive windows are
    bounded (30-60 min maximum). The RAG agent rate-limits its trigger
    emissions — concurrent triggers for the same source are rare. A simple
    thread-per-trigger is correct for the expected load; a pool would add
    complexity without benefit (Section 3.2 YAGNI).
    """
    source = trigger["source"]
    producer = _build_producer(source)

    def _run() -> None:
        try:
            if source == "googletrends":
                kwargs: dict[str, Any] = {"keywords": trigger["keywords"]}
                if "geo" in trigger:
                    kwargs["geo"] = trigger["geo"]
                if "timeframe" in trigger:
                    kwargs["timeframe"] = trigger["timeframe"]
                emitted = producer.run_reactive(**kwargs)

            elif source == "openweather":
                kwargs = {"hotspot_names": trigger["hotspot_names"]}
                if "window_minutes" in trigger:
                    kwargs["window_minutes"] = trigger["window_minutes"]
                if "poll_interval_sec" in trigger:
                    kwargs["poll_interval_sec"] = trigger["poll_interval_sec"]
                emitted = producer.run_reactive(**kwargs)

            elif source == "opensky":
                kwargs = {"target_callsigns": trigger["target_callsigns"]}
                if "window_minutes" in trigger:
                    kwargs["window_minutes"] = trigger["window_minutes"]
                if "poll_interval_sec" in trigger:
                    kwargs["poll_interval_sec"] = trigger["poll_interval_sec"]
                emitted = producer.run_reactive(**kwargs)

            elif source == "newsapi":
                kwargs = {"keywords": trigger["keywords"]}
                if "time_window_days" in trigger:
                    kwargs["time_window_days"] = trigger["time_window_days"]
                emitted = producer.run_reactive(**kwargs)

            else:
                logger.error(
                    "[trigger_consumer] dispatch() called for unknown source '%s'", source
                )
                return

            logger.info(
                "[trigger_consumer] Reactive run complete — source=%s emitted=%d",
                source, emitted,
            )

        except Exception as exc:  # noqa: BLE001
            # Non-fatal: a failed reactive run should not crash the consumer loop.
            # The RAG agent can re-emit the trigger if needed.
            logger.error(
                "[trigger_consumer] Reactive run failed for source=%s: %s",
                source, exc,
            )

    thread = threading.Thread(target=_run, daemon=True, name=f"reactive-{source}")
    thread.start()
    logger.info(
        "[trigger_consumer] Dispatched reactive thread for source=%s", source
    )
    return thread


# ==========================================================
# Main Consumer Loop
# ==========================================================

def run(consumer: Any, dlq_producer: Any) -> None:
    """
    Blocking consumer loop for the ingestion_triggers topic (Section 2.4).

    Consumer and DLQ producer are injected so they can be replaced with
    mocks in unit tests without needing a live Kafka cluster.

    Decision tree for each message:
        1. Deserialize NDJSON → dict (handled by make_consumer() deserializer).
        2. validate_trigger(payload) → errors list.
        3. Errors non-empty → emit DLQ record, log warning, commit offset.
        4. Errors empty     → dispatch(payload) in background thread, commit offset.

    Why commit offset after DLQ emit (not just for success): an invalid trigger
    that re-enters the queue on replay would loop indefinitely. Committing after
    DLQ routing ensures each bad trigger is processed exactly once.

    Args:
        consumer:     KafkaConsumer subscribed to INGESTION_TRIGGERS.
        dlq_producer: KafkaProducer for emitting DLQ records.
    """
    logger.info("[trigger_consumer] Starting — listening on '%s'", INGESTION_TRIGGERS)

    for message in consumer:
        payload = message.value

        errors = validate_trigger(payload)

        if errors:
            logger.warning(
                "[trigger_consumer] Invalid trigger payload — routing to DLQ. "
                "errors=%s payload=%r",
                errors, payload,
            )
            dlq_record = _build_dlq_record(payload, errors)
            dlq_producer.send(DEAD_LETTER_QUEUE, value=dlq_record)
            dlq_producer.flush()
            consumer.commit()
            continue

        source = payload.get("source", "unknown")
        logger.info(
            "[trigger_consumer] Valid trigger received — source=%s", source
        )
        dispatch(payload)
        consumer.commit()


# ==========================================================
# Entrypoint
# ==========================================================

if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
    )
    _consumer = make_consumer(
        INGESTION_TRIGGERS,
        group_id="trigger-consumer",
    )
    _dlq_producer = make_producer()
    run(_consumer, _dlq_producer)
