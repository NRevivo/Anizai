"""
Schema Validators — Validation logic & Dead-Letter Queue routing.

Why centralized here: schema contracts are the backbone of the Medallion
Architecture. Any drift between what a producer emits and what a downstream
consumer expects will corrupt Silver/Gold layers. One authoritative place for
all schema rules makes it impossible for validators to diverge (Section 3.2).

Architecture enforcement:
  - Objects failing Silver validation → dead-letter-queue (Section 3.5)
  - Silent drops and DB corruption are strictly prohibited (Section 9.5)

Usage pattern (Flink Silver Job):
    result = validate_envelope(msg)
    if not result.is_valid:
        route_to_dlq(producer, msg, result.errors, source_topic="ingest.bronze.newsapi")
        return

References:
    - Section 3.2: DRY — Schema validators in utils/validators.py
    - Section 3.5: Schema Compliance & DLQ routing
    - Section C.1: Bronze Schema
    - Section C.2: Silver Structured Metric Schema
    - Section C.3: Silver Document & Social Discourse Stores
    - Section C.5: Gold Global Signal Schema
    - Section C.6: Gold Social Pulse Schema
    - Section C.7: Gold Divergence Alert Schema
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from config.kafka_topics import DEAD_LETTER_QUEUE


# ==========================================================
# Validation Result
# ==========================================================

@dataclass
class ValidationResult:
    """
    Returned by every validator.

    is_valid: False means the object must NOT proceed to the next layer.
    errors:   Human-readable list of what failed — written to the DLQ
              envelope so operators can diagnose without re-fetching.
    """
    is_valid: bool
    errors: list[str] = field(default_factory=list)

    def __bool__(self) -> bool:
        return self.is_valid


# ==========================================================
# Internal Helpers
# ==========================================================

def _check_fields(obj: dict, required: dict[str, type | tuple]) -> list[str]:
    """
    Check that all required keys are present and have the expected type.

    Args:
        obj:      The dict to inspect.
        required: Mapping of key → expected type (or tuple of types).

    Returns:
        List of error strings (empty = all checks passed).
    """
    errors = []
    for key, expected_type in required.items():
        if key not in obj:
            errors.append(f"Missing required field: '{key}'")
        elif obj[key] is None:
            errors.append(f"Field '{key}' must not be None")
        elif not isinstance(obj[key], expected_type):
            actual = type(obj[key]).__name__
            if isinstance(expected_type, tuple):
                expected_str = " | ".join(t.__name__ for t in expected_type)
            else:
                expected_str = expected_type.__name__
            errors.append(
                f"Field '{key}' expected {expected_str}, got {actual}"
            )
    return errors


def _check_nested(obj: dict, parent_key: str, required: dict[str, type | tuple]) -> list[str]:
    """
    Validate a nested dict within obj[parent_key].

    Returns errors prefixed with the parent key for clarity.
    """
    if parent_key not in obj or not isinstance(obj[parent_key], dict):
        return [f"Missing or invalid nested object: '{parent_key}'"]
    return [
        f"[{parent_key}] {err}"
        for err in _check_fields(obj[parent_key], required)
    ]


def _is_iso8601(value: str) -> bool:
    """
    Lightweight ISO8601 UTC check — tries fromisoformat() parsing.
    Accepts strings with or without timezone designators.
    """
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
        return True
    except (ValueError, AttributeError):
        return False


# ==========================================================
# Envelope Validator (Section 3.2)
# ==========================================================

def validate_envelope(msg: Any) -> ValidationResult:
    """
    Validate the outer Message Envelope (Section 3.2).

    Checks: event_id, trace_id, producer_timestamp, schema_version,
            source_name, payload (non-null dict).

    This is the first gate in the Flink Silver Job — a message
    failing envelope validation cannot proceed to Silver processing.
    """
    if not isinstance(msg, dict):
        return ValidationResult(False, ["Message is not a dict"])

    errors = _check_fields(msg, {
        "event_id":           str,
        "trace_id":           str,
        "producer_timestamp": str,
        "schema_version":     str,
        "source_name":        str,
        "payload":            dict,
    })

    if "producer_timestamp" in msg and isinstance(msg["producer_timestamp"], str):
        if not _is_iso8601(msg["producer_timestamp"]):
            errors.append("Field 'producer_timestamp' is not a valid ISO8601 string")

    return ValidationResult(not errors, errors)


# ==========================================================
# Bronze Schema Validator (Section C.1)
# ==========================================================

def validate_bronze_payload(payload: Any) -> ValidationResult:
    """
    Validate the Bronze Schema object inside envelope["payload"] (Section C.1).

    Bronze is the immutable raw layer — we validate structure only.
    We do NOT validate the contents of raw_payload itself; that is
    the job of the Silver Job's domain-specific validators.
    """
    if not isinstance(payload, dict):
        return ValidationResult(False, ["Bronze payload is not a dict"])

    errors = _check_fields(payload, {
        "ingestion_id":        str,
        "source_name":         str,
        "source_endpoint":     str,
        "ingestion_timestamp": str,
        "producer_version":    str,
        "raw_payload":         (dict, list, str, int, float),
        "metadata":            dict,
    })

    errors += _check_nested(payload, "metadata", {
        "http_status_code":    int,
        "request_duration_ms": (int, float),
    })

    if "ingestion_timestamp" in payload and isinstance(payload["ingestion_timestamp"], str):
        if not _is_iso8601(payload["ingestion_timestamp"]):
            errors.append("Field 'ingestion_timestamp' is not a valid ISO8601 string")

    return ValidationResult(not errors, errors)


# ==========================================================
# Silver Schema Validators
# ==========================================================

def validate_silver_structured_metric(obj: Any) -> ValidationResult:
    """
    Validate Silver Structured Metric schema (Section C.2).

    Covers: Polymarket prices, PredictIt, FRED indicators,
            OpenWeather readings, OpenSky aircraft states.
    """
    if not isinstance(obj, dict):
        return ValidationResult(False, ["Silver structured metric is not a dict"])

    errors = _check_fields(obj, {
        "schema_version":     str,
        "layer":              str,
        "entity_type":        str,
        "core_identity":      dict,
        "data_point":         dict,
        "momentum_block":     dict,
        "metadata_extension": dict,
    })

    errors += _check_nested(obj, "core_identity", {
        "metric_id":            str,
        "canonical_event_id":   str,
        "source_name":          str,
        "external_reference_id": str,
    })

    errors += _check_nested(obj, "data_point", {
        "current_value": (int, float),
        "unit":          str,
        "status":        str,
        "timestamp_utc": str,
    })

    errors += _check_nested(obj, "momentum_block", {
        "change_24h":    (int, float),
        "change_7d":     (int, float),
        "change_30d":    (int, float),
        "is_new_market": bool,
    })

    if "layer" in obj and obj["layer"] != "Silver":
        errors.append(f"Field 'layer' must be 'Silver', got '{obj['layer']}'")

    if "entity_type" in obj and obj["entity_type"] != "Structured_Metric":
        errors.append(
            f"Field 'entity_type' must be 'Structured_Metric', got '{obj['entity_type']}'"
        )

    return ValidationResult(not errors, errors)


def validate_silver_document(obj: Any) -> ValidationResult:
    """
    Validate Silver Full-Text Document Store schema (Section C.3).

    Covers: NewsAPI articles, ArXiv papers.

    Why document_hash is required: the SHA-256 deduplication check
    (Section 4.1C) depends on this field being present before writing
    to the Knowledge Vault.
    """
    if not isinstance(obj, dict):
        return ValidationResult(False, ["Silver document is not a dict"])

    errors = _check_fields(obj, {
        "doc_id":                str,
        "document_hash":         str,   # SHA-256 hex digest
        "canonical_event_id":    str,
        "full_text_raw":         str,
        "inverted_pyramid_lead": str,
        "source_name":           str,
        "original_url":          str,
        "publish_date":          str,
        "detected_entities":     list,
        "relevance_score":       float,
    })

    if "relevance_score" in obj and isinstance(obj["relevance_score"], float):
        if not 0.0 <= obj["relevance_score"] <= 1.0:
            errors.append(
                f"Field 'relevance_score' must be in [0.0, 1.0], got {obj['relevance_score']}"
            )

    if "document_hash" in obj and isinstance(obj["document_hash"], str):
        if len(obj["document_hash"]) != 64:
            errors.append(
                "Field 'document_hash' must be a 64-char SHA-256 hex digest"
            )

    return ValidationResult(not errors, errors)


def validate_silver_social(obj: Any) -> ValidationResult:
    """
    Validate Silver Social Discourse Store schema (Section C.3).

    Covers four platform patterns:
      - Reddit (post-centric):       post_body + full_comment_archive + upvote_ratio
      - Polymarket (volume-centric): raw_comments grouped by market_id
      - Telegram (news-centric):     message_text + extracted_links + channel_source
      - HackerNews (story-centric):  title + url + points + top_comments

    Why platform-specific branching: the four social patterns have
    structurally different storage logic (Section C.3 table), so
    validation must be aware of which pattern is being checked.

    Note — HackerNews branch added at project foundation (Phase 0)
    after identifying it is routed to process.silver.social_pulse via
    BRONZE_TO_SILVER_ROUTING and has a Gold representation in Section C.6.
    The validator does NOT need to be revisited during the HackerNews
    vertical slice (Phase 4).
    """
    if not isinstance(obj, dict):
        return ValidationResult(False, ["Silver social object is not a dict"])

    errors = _check_fields(obj, {
        "source_name": str,
        "ingested_at": str,
    })

    source = obj.get("source_name", "").lower()

    if source == "reddit":
        errors += _check_fields(obj, {
            "post_id":              str,
            "subreddit":            str,
            "post_body":            str,
            "upvote_ratio":         float,
            "full_comment_archive": list,
        })
    elif source == "polymarket":
        errors += _check_fields(obj, {
            "market_id":    str,
            "raw_comments": list,
        })
    elif source == "telegram":
        errors += _check_fields(obj, {
            "message_id":      (str, int),
            "message_text":    str,
            "channel_source":  str,
            "extracted_links": list,
        })
    elif source == "hackernews":
        # Story-centric pattern: preserves the full story + top comments
        # so the Gold Job can extract top_technical_insights and
        # community_sentiment (Section C.6 Gold Social Pulse schema).
        errors += _check_fields(obj, {
            "story_id":    (str, int),
            "title":       str,
            "url":         str,
            "points":      int,
            "author":      str,
            "published_at": str,
            "top_comments": list,   # up to 10 first-level comments (Section B.2)
        })
    else:
        errors.append(
            f"Unknown social source_name '{source}'. "
            "Expected one of: reddit, polymarket, telegram, hackernews"
        )

    return ValidationResult(not errors, errors)


# ==========================================================
# Gold Schema Validators
# ==========================================================

def _validate_gold_common(obj: dict) -> list[str]:
    """
    Validate the common skeleton shared by Gold Global Signal (C.5)
    and Gold Social Pulse (C.6) schemas.
    """
    errors = _check_nested(obj, "metadata", {
        "signal_id":        str,
        "canonical_event_id": str,
        "source_platform":  str,
        "published_at":     str,
        "silver_data_ref":  str,
        "raw_data_ref":     str,
    })

    errors += _check_nested(obj, "content_vitals", {
        "title":               str,
        "description_snippet": str,
    })

    errors += _check_nested(obj, "enrichment_ai", {
        "executive_summary":   str,
        "key_findings":        list,
        "impact_level":        int,
        "urgency_level":       int,
        "reliability_score":   float,
        "sentiment_score":     float,
        "extracted_entities":  list,
        "topic_classification": str,
        "fact_check_flag":     bool,
    })

    if "enrichment_ai" in obj and isinstance(obj["enrichment_ai"], dict):
        ai = obj["enrichment_ai"]
        for field_name in ("impact_level", "urgency_level"):
            val = ai.get(field_name)
            if isinstance(val, int) and not 1 <= val <= 5:
                errors.append(
                    f"[enrichment_ai] '{field_name}' must be in [1, 5], got {val}"
                )
        for field_name in ("reliability_score", "sentiment_score"):
            val = ai.get(field_name)
            if field_name == "reliability_score" and isinstance(val, float):
                if not 0.0 <= val <= 1.0:
                    errors.append(
                        f"[enrichment_ai] '{field_name}' must be in [0.0, 1.0], got {val}"
                    )
            elif field_name == "sentiment_score" and isinstance(val, float):
                if not -1.0 <= val <= 1.0:
                    errors.append(
                        f"[enrichment_ai] '{field_name}' must be in [-1.0, 1.0], got {val}"
                    )

    return errors


def validate_gold_signal(obj: Any) -> ValidationResult:
    """
    Validate Gold Global Signal Schema (Section C.5).

    Covers: NewsAPI articles, ArXiv papers — after AI enrichment.
    """
    if not isinstance(obj, dict):
        return ValidationResult(False, ["Gold signal object is not a dict"])

    errors = _validate_gold_common(obj)
    return ValidationResult(not errors, errors)


def validate_gold_social_pulse(obj: Any) -> ValidationResult:
    """
    Validate Gold Social Pulse Schema (Section C.6).

    Covers: Telegram, Reddit, HackerNews, Polymarket — after
    Consensus Bundling and Cognitive Metadata enrichment.
    """
    if not isinstance(obj, dict):
        return ValidationResult(False, ["Gold social pulse object is not a dict"])

    errors = _validate_gold_common(obj)

    errors += _check_nested(obj, "social_context", {
        "community_name":           str,
        "primary_engagement_score": (int, float),
    })

    return ValidationResult(not errors, errors)


def validate_gold_divergence_alert(obj: Any) -> ValidationResult:
    """
    Validate Gold Market Divergence Alert Schema (Section C.7).

    Generated by Flink (non-AI) when cross-platform price discrepancy
    exceeds 3%. urgency_level rises to 4 when spread >5%.
    """
    if not isinstance(obj, dict):
        return ValidationResult(False, ["Gold divergence alert is not a dict"])

    errors = _check_fields(obj, {
        "schema_version": str,
        "layer":          str,
        "entity_type":    str,
        "identity":       dict,
        "divergence_data": dict,
        "alert_metrics":  dict,
        "ai_trigger_logic": dict,
    })

    errors += _check_nested(obj, "identity", {
        "alert_id":            str,
        "canonical_event_id":  str,
        "timestamp_generated": str,
    })

    errors += _check_nested(obj, "divergence_data", {
        "source_a":                   dict,
        "source_b":                   dict,
        "spread_delta":               (int, float),
        "is_statistically_significant": bool,
    })

    errors += _check_nested(obj, "alert_metrics", {
        "urgency_level":    int,
        "confidence_score": float,
        "impact_area":      str,
    })

    errors += _check_nested(obj, "ai_trigger_logic", {
        "divergence_summary": str,
        "anomaly_type":       str,
        "suggested_action":   str,
    })

    if "layer" in obj and obj["layer"] != "Gold":
        errors.append(f"Field 'layer' must be 'Gold', got '{obj['layer']}'")

    if "entity_type" in obj and obj["entity_type"] != "Divergence_Alert":
        errors.append(
            f"Field 'entity_type' must be 'Divergence_Alert', got '{obj['entity_type']}'"
        )

    return ValidationResult(not errors, errors)


# ==========================================================
# Dead-Letter Queue Router (Section 3.5)
# ==========================================================

def route_to_dlq(
    producer,
    original_message: dict,
    errors: list[str],
    source_topic: str,
    failed_layer: str = "Silver",
) -> None:
    """
    Route a schema-invalid message to the dead-letter-queue (Section 3.5).

    Why a separate DLQ message: the original message is preserved intact
    inside 'original_message' so it can be replayed once the schema issue
    is fixed. The error context tells operators exactly what failed without
    requiring them to re-run the pipeline.

    Args:
        producer:         A kafka_utils.make_producer() instance.
        original_message: The full envelope dict that failed validation.
        errors:           List of validation error strings from the validator.
        source_topic:     The Bronze topic this message came from.
        failed_layer:     Which layer's validation failed (default: "Silver").

    This function MUST NOT raise — a DLQ failure should be logged but
    must not crash the Flink job or drop the error silently. If the DLQ
    send fails, the error is printed for operator review.
    """
    dlq_record = {
        "dlq_id":            str(uuid.uuid4()),
        "failed_at":         datetime.now(timezone.utc).isoformat(),
        "failed_layer":      failed_layer,
        "source_topic":      source_topic,
        "validation_errors": errors,
        "original_message":  original_message,
    }
    try:
        producer.send(DEAD_LETTER_QUEUE, value=dlq_record)
    except Exception as exc:
        # Last-resort log — never let a DLQ failure silently swallow the error.
        print(
            f"[CRITICAL] Failed to send to dead-letter-queue: {exc}\n"
            f"Original errors: {errors}\n"
            f"Original message event_id: {original_message.get('event_id', 'unknown')}"
        )
