"""
agent/nodes/trigger_reactive_ingestion.py — Sprint 23 producer-trigger node.

When the agent's vault is insufficient for a forecast, this node emits a
single Kafka trigger to the `ingestion_triggers` topic targeting the
NewsAPI producer's `run_reactive()` method. Trigger-and-forget: the node
does NOT wait for the producer to fetch articles or for Bronze→Silver→Gold
propagation. Fetched articles land in the vault asynchronously and are
available to the next session.

This node is **built in isolation in Sprint 23** — it is NOT wired into
`agent/graph.py` here. The graph wiring happens in Sprint 26 (T26.7) after
both Sprint 22 (foundation fixes) and Sprint 23 (this work) are complete.
See agentic_hub_implementation_phase8_revised.md §"Implementation Order
& Parallelization" for the dependency map.

Key design decisions (recorded in the revised plan's Design Rationale Log):

    D1 — One API call per keyword + URL dedup (in the producer, T23.1).
    D4 — Keyword construction: structured_intent.entities first, then
         sufficiency_checks[-1].missing_dimensions, raw_question excluded;
         case-insensitive dedup preserving first-occurrence case; capped
         at 8 keywords total.
    D5 — Counter increments on every emit attempt (success OR Kafka
         failure). Rate limit semantic is "≤ N attempts per session," not
         "≤ N successful emits."

Rate-limit gate (at node entry):

    If state["reactive_triggers_emitted"] >= AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION
    the node returns {} (no state mutation) without emitting. Sprint 26's
    graph routing function is the primary gate; this in-node check is
    belt-and-suspenders so the node behaves safely if a future caller
    forgets the gate.

Producer lifecycle (module-level lazy singleton — D7):

    `_get_producer()` lazily constructs one `KafkaProducer` the first time
    it is called and reuses it for every subsequent send within the same
    process. This reverses the initial per-call design (D5-era proposal)
    based on what T23.9 surfaced: kafka-python-ng's `send().get()` includes
    bootstrap + metadata-refresh on a cold producer, which routinely
    exceeds the 2s timeout on dev hardware. With the singleton, only the
    FIRST call in a worker's lifetime pays that cost; every session
    thereafter sees a warm producer and the 2s budget is appropriate for
    the "broker is healthy" target it was originally chosen for.

    No health check (D7 option (a)): the next send fails naturally per D5
    (counter +1, log row with status="failed"). kafka-python-ng's internal
    reconnect handles broker bounces transparently for the subsequent
    session. Adding `_producer.bootstrap_connected()` polling at every
    call would be defensive code for a rare event — YAGNI for V1; revisit
    in T26+ if initial-test data shows real broker-bounce-mid-session
    incidents.

    Test surface: `_reset_producer_for_tests()` force-closes the singleton
    so the next `_get_producer()` builds a fresh one. Bundle B / G2 tests
    patch `_get_producer` directly at the lookup site (CLAUDE.md §4.3) and
    never touch `_producer` directly.

Log-write ordering (Kafka first, log row second):

    The Postgres log row is observability-only (debugging + cost
    attribution per KG-PHASE-9.5-9). A log-write failure does NOT cause
    the node to fail — the warning is logged and the trigger is still
    considered "emitted" from the agent's perspective if Kafka accepted it.

Service Isolation (CLAUDE.md §3.3):

    Talks only to Kafka (via utils.kafka_utils.make_producer) and Postgres
    (via persistence.reactive_triggers_log). No LLM calls, no vault reads,
    no Firestore writes. State-mediated input (agent-design P2).

Spec references:
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md §Sprint 23
    - data-pipeline/docs/agentic_hub_implementation_phase8_revised.md
      §"Design Rationale Log → Sprint 23 decisions" (D1, D4, D5)
    - data-pipeline/docs/agentic_hub_spec.md §8.3.2 (revised — see "Spec
      Sections Affected" in the revised plan)
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Any

from kafka import KafkaProducer
from kafka.errors import KafkaError

from agent.config.settings import (
    AGENT_REACTIVE_DEFAULT_WINDOW_DAYS,
    AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION,
)
from config.kafka_topics import INGESTION_TRIGGERS
from persistence import reactive_triggers_log
from utils.kafka_utils import make_producer
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# Constants
# ==========================================================

# Hardcoded V1 source. Telegram / ArXiv / Polymarket-comments reactive
# follow-ups are Future Enhancement 6 in the revised plan.
_REACTIVE_SOURCE = "newsapi"

# Maximum keywords per trigger (D4 in Design Rationale Log).
_KEYWORDS_CAP = 8

# Kafka acknowledgment timeout for the warm-producer case. The first
# `_get_producer()` call in a process pays bootstrap + metadata-refresh
# cost (typically 1-3s on dev hardware); subsequent sends are <100ms
# from cached metadata. Per D7, the singleton makes this timeout
# appropriate for the steady-state path it was designed for.
_KAFKA_SEND_TIMEOUT_SEC = 2.0


# ==========================================================
# Producer Singleton (D7)
# ==========================================================
# Module-level lazy singleton. See "Producer lifecycle" in the module
# docstring for the full rationale. Cold start once per worker process;
# warm thereafter for every session in that worker's lifetime.

_producer: KafkaProducer | None = None


def _get_producer() -> KafkaProducer:
    """
    Return the module-level KafkaProducer, constructing it on first call.

    The first call in a process pays the full bootstrap + metadata-refresh
    cost (1-3s observed locally). All subsequent calls return the same
    instance — no socket reopen, no metadata refetch, no warm-up.

    Tests patch this function at the lookup site
    (`agent.nodes.trigger_reactive_ingestion._get_producer`) to inject a
    MagicMock without touching the singleton.
    """
    global _producer
    if _producer is None:
        _producer = make_producer()
    return _producer


def _reset_producer_for_tests() -> None:
    """
    Force-close the singleton so the next `_get_producer()` builds a fresh
    one. Intended for Gate 3 tests that need cold-start determinism or for
    integration tests that swap in a controlled producer.

    Production code never calls this. The function name signals intent.
    """
    global _producer
    if _producer is not None:
        try:
            _producer.close(timeout=2.0)
        except Exception:  # noqa: BLE001
            # Best-effort close; ignore any teardown error.
            pass
    _producer = None


# ==========================================================
# Keyword Construction (D4)
# ==========================================================

def _build_keywords(state: dict) -> list[str]:
    """
    Merge `structured_intent.entities` + `sufficiency_checks[-1].missing_dimensions`
    into a deduped, capped keyword list.

    Algorithm (D4):
        1. Take entities from `state.structured_intent.entities` in order.
        2. Append `missing_dimensions` from the latest sufficiency check.
        3. Dedup case-insensitively (preserving original case of first
           occurrence). "Iran" and "iran" collapse to one slot.
        4. Cap at _KEYWORDS_CAP (8). Excess silently dropped — entities-
           first means the lower-rank missing_dimensions are the ones cut.
        5. raw_question is intentionally NOT split into keywords.

    Args:
        state: ForecastState dict. Required fields read defensively via
               .get() — absent fields treated as empty.

    Returns:
        Deduped, ordered, capped list of keyword strings. May be empty if
        no usable signal exists in state (caller should treat empty as
        "skip emit").
    """
    intent = state.get("structured_intent") or {}
    entities: list[str] = intent.get("entities") or []

    sufficiency = state.get("sufficiency_checks") or []
    missing_dimensions: list[str] = (
        sufficiency[-1].get("missing_dimensions") if sufficiency else []
    ) or []

    seen_lower: set[str] = set()
    keywords: list[str] = []
    for term in list(entities) + list(missing_dimensions):
        if not isinstance(term, str):
            continue
        cleaned = term.strip()
        if not cleaned:
            continue
        key = cleaned.lower()
        if key in seen_lower:
            continue
        seen_lower.add(key)
        keywords.append(cleaned)
        if len(keywords) >= _KEYWORDS_CAP:
            break

    return keywords


# ==========================================================
# Node Entry Point
# ==========================================================

def trigger_reactive_ingestion(state: dict) -> dict:
    """
    Emit one ingestion_triggers message to the NewsAPI producer, log the
    attempt, and return the state delta containing the incremented counter.

    Rate-limit gate (D5 semantic — "≤ N attempts per session"):
        Checked at node entry. If `state["reactive_triggers_emitted"]` is
        already at the limit, the node returns {} (no Kafka send, no log
        row written, no state mutation). LangGraph merges {} into running
        state without effect.

    Increment timing (D5):
        The counter is incremented on the state delta returned by the node
        whenever a send was *attempted* — regardless of whether Kafka
        actually accepted the message. A KafkaError still counts toward
        the session budget. Rationale: a Kafka outage shouldn't permit
        an unlimited retry storm.

    Trigger-and-forget:
        Once the Kafka send returns (success OR failure), the node
        returns. No polling, no waiting for articles to appear in the
        vault. The next session sees the newly-ingested articles.

    Returns:
        State delta dict. The counter is ALWAYS present in the return:
          - {"reactive_triggers_emitted": <prior + 1>} when an attempt
            was made (success OR Kafka failure, per D5)
          - {"reactive_triggers_emitted": <prior>}     on the rate-limit-
            blocked path (semantic no-op — same value back)
          - {"reactive_triggers_emitted": <prior>}     on the no-keywords
            path (semantic no-op — nothing to rate-limit)

        Why always write the counter (even on no-op paths):
            LangGraph's `StateGraph` rejects empty `{}` returns with
            `InvalidUpdateError: Must write to at least one of [...]`
            because every node must contribute at least one channel write
            per step. Writing the counter back at its current value is the
            canonical idiom for a node that needs to be a no-op — it
            satisfies the channel-write requirement while leaving observable
            state unchanged. Defensive readers (`int(state.get(field) or 0)`)
            see the same value either way. Surfaced by T23.8 G2.2.
    """
    emitted_so_far = int(state.get("reactive_triggers_emitted") or 0)
    if emitted_so_far >= AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION:
        logger.info(
            "[trigger_reactive_ingestion] Session at per-session limit "
            "(%d); skipping emit. session_id=%s",
            AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION,
            state.get("session_id"),
        )
        return {"reactive_triggers_emitted": emitted_so_far}

    keywords = _build_keywords(state)
    if not keywords:
        # No usable signal. Emitting an empty-keywords trigger would be
        # rejected by the consumer's validate_trigger anyway. Log and
        # skip without counting against the budget — there's nothing
        # to be rate-limited for in the absence of any input signal.
        # Write the counter back unchanged (see "Why always write the
        # counter" above).
        logger.warning(
            "[trigger_reactive_ingestion] No keywords could be built from "
            "structured_intent.entities + sufficiency_checks[-1]."
            "missing_dimensions; skipping emit. session_id=%s",
            state.get("session_id"),
        )
        return {"reactive_triggers_emitted": emitted_so_far}

    session_id = state.get("session_id", "")
    payload = {
        "source":           _REACTIVE_SOURCE,                   # consumer: required, ∈ VALID_SOURCES
        "keywords":         keywords,                            # consumer: required, non-empty
        "time_window_days": AGENT_REACTIVE_DEFAULT_WINDOW_DAYS,  # consumer: optional, forwarded
        # The following two fields are IGNORED by the consumer's
        # validate_trigger / dispatch. They're carried for forensic
        # correlation between this Kafka message and the
        # reactive_triggers_log Postgres row.
        "session_id":       session_id,
        "trigger_time":     datetime.now(timezone.utc).isoformat(),
    }

    kafka_offset, status = _send_to_kafka(payload, session_id)
    _log_attempt(session_id, keywords, kafka_offset, status)

    return {
        "reactive_triggers_emitted": emitted_so_far + 1,
    }


# ==========================================================
# Kafka Emission (D5 — counts as an attempt regardless of outcome)
# ==========================================================

def _send_to_kafka(
    payload: dict, session_id: str
) -> tuple[int | None, str]:
    """
    Synchronously emit `payload` to INGESTION_TRIGGERS with a short ack
    timeout, returning (kafka_offset, status).

    Uses the module-level singleton producer (D7) so cold-start cost is
    paid once per worker process. The 2s `send().get()` timeout is
    appropriate for the warm-producer case.

    No flush/close after the send: `.get()` already blocks for the broker
    ack, which guarantees the message is durably in the broker (acks=all,
    per make_producer's defaults). flush would be redundant; close would
    defeat the singleton.

    Args:
        payload:    The complete trigger payload, NDJSON-serialized by
                    the producer's value_serializer.
        session_id: Forensic context for the log line if send fails.

    Returns:
        (kafka_offset, status) — kafka_offset is the broker-assigned
        offset on success or None on any failure; status is "emitted"
        or "failed".
    """
    producer = _get_producer()
    try:
        metadata = producer.send(INGESTION_TRIGGERS, value=payload).get(
            timeout=_KAFKA_SEND_TIMEOUT_SEC
        )
        logger.info(
            "[trigger_reactive_ingestion] Trigger emitted — "
            "session_id=%s offset=%d partition=%d keywords=%d",
            session_id, metadata.offset, metadata.partition,
            len(payload["keywords"]),
        )
        return metadata.offset, "emitted"
    except KafkaError as exc:
        # Kafka-level failure (timeout, broker down, ack-not-received).
        # Counts as an attempt per D5; log row records status="failed".
        # No producer close — kafka-python-ng's reconnect logic will
        # transparently rebuild the connection on the next call.
        logger.error(
            "[trigger_reactive_ingestion] Kafka send failed — "
            "session_id=%s err=%s",
            session_id, exc,
        )
        return None, "failed"


# ==========================================================
# Postgres Audit Log (observability — non-fatal)
# ==========================================================

def _log_attempt(
    session_id: str,
    keywords: list[str],
    kafka_offset: int | None,
    status: str,
) -> None:
    """
    Insert a row into reactive_triggers_log capturing the attempt.

    Log-write failures are observability concerns — they do NOT cause the
    node to fail. The Kafka message has already been emitted (or already
    failed) by the time we get here; a Postgres outage can't un-emit a
    Kafka message or magically deliver one. Swallowing the exception
    avoids cascading a downstream-table problem into the forecast path.
    """
    try:
        reactive_triggers_log.insert(
            session_id=session_id,
            source=_REACTIVE_SOURCE,
            keywords=keywords,
            kafka_offset=kafka_offset,
            status=status,
        )
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "[trigger_reactive_ingestion] reactive_triggers_log.insert "
            "failed (non-fatal) — session_id=%s status=%s err=%s",
            session_id, status, exc,
        )
