"""
Utils Package — Shared utilities centralized per DRY principle (Section 3.2).

All shared logic lives here to prevent code duplication:
  - kafka_utils.py: Kafka helpers, NDJSON serialization, envelope builder
  - validators.py:  Schema validators & DLQ routing
  - db.py:          Database connections
"""

from .db import (
    get_connection,
    get_cursor,
    verify_extensions,
    close_pool,
)

from .validators import (
    ValidationResult,
    validate_envelope,
    validate_bronze_payload,
    validate_silver_structured_metric,
    validate_silver_document,
    validate_silver_social,
    validate_gold_signal,
    validate_gold_social_pulse,
    validate_gold_divergence_alert,
    route_to_dlq,
)

from .kafka_utils import (
    build_bronze_message,
    build_bronze_payload,
    build_envelope,
    make_producer,
    make_consumer,
    ndjson_serializer,
    ndjson_deserializer,
    timed_request,
    PRODUCER_VERSION,
)
