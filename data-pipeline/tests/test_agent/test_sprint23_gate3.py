"""
Gate 3 integration test for Sprint 23 — Producer-trigger Infrastructure (T23.9).

Real Kafka + real Postgres. The newsapi.ai HTTP boundary is mocked at
`NewsAPIProducer._fetch_articles` — Sprint 23 introduces zero new HTTP
code, and the producer's API client was tested by Phase 7A's Gate 1.

End-to-end flow exercised:

    trigger_reactive_ingestion(state)
        ├─ Real Kafka write → ingestion_triggers
        └─ Real Postgres insert → reactive_triggers_log

    <test reads the trigger back from Kafka and dispatches it manually>

    dispatch(trigger)  → NewsAPIProducer instance
        └─ run_reactive(keywords, time_window_days)
            ├─ _fetch_articles(...) — MOCKED with fixture
            ├─ whitelist filter, dedup, raw_payload build — REAL
            └─ Kafka write → BRONZE_NEWSAPI (real)

What this verifies (and why it's worth a Gate 3 row):
    ✓ NDJSON round-trip through the real broker (envelope serialization
      lands and is recoverable verbatim)
    ✓ T23.5 ↔ T23.2 schema agreement under real Kafka deserialization
    ✓ T23.2's dispatch correctly routes to the NewsAPIProducer for
      `source: "newsapi"`
    ✓ Postgres reactive_triggers_log row is persisted with a real
      kafka_offset (not None — the broker assigns it)
    ✓ Bronze emission flow is intact end-to-end (whitelist, dedup,
      raw_payload shape preserved through the producer's real path)

What is NOT verified here:
    - The newsapi.ai HTTP client itself (Phase 7A scope; not new in Sprint 23)
    - Live newsapi.ai availability or quota (non-deterministic, costs budget)
    - Full Bronze→Silver→Gold pipeline propagation (T23.10's scope)

Mock strategy (D1 from CLAUDE.md §4.3 — patch at lookup site):
    `NewsAPIProducer._fetch_articles` is patched at the class level so the
    instance dispatch() creates inherits the mock. Return shape matches the
    real method: (articles_list, total_results, duration_ms).

Why the run() consumer loop is NOT exercised:
    The consumer's polling loop is tested in T23.2's mocked tests. Here we
    consume the trigger message ourselves (one poll, filtered by session_id)
    and call dispatch() directly. This keeps the test single-threaded for
    the wait-and-verify step and avoids needing a stop signal for the
    infinite-loop consumer.

Pre-warm pattern (D7 module-level singleton):
    The trigger node's KafkaProducer is a lazy module-level singleton.
    The FIRST call in any process pays the bootstrap + metadata-refresh
    cost (1-3s observed locally); subsequent calls are warm-producer fast.
    In production, the agent worker is long-lived and absorbs cold-start
    once per process lifetime. This test mimics that warm regime by
    pre-warming the singleton before the timed `send().get(timeout=2.0)`
    assertion — otherwise we'd be measuring cold-start metadata cost
    against a budget that was designed for warm-producer round-trips.

Requires:
  - Postgres reachable (db_available fixture). Section 7 of init.sql must
    be applied to the running container — see task_plan.md Sprint 23
    Progress Notes for the one-line `docker exec ... psql` command.
  - Kafka reachable (kafka_available fixture) with the ingestion_triggers
    and BRONZE_NEWSAPI topics.
"""

from __future__ import annotations

import sys
import time
from unittest.mock import patch

import pytest

from agent.nodes import trigger_reactive_ingestion as node_module
from config.kafka_topics import BRONZE_NEWSAPI, INGESTION_TRIGGERS
from ingestion.newsapi_producer import NewsAPIProducer
from orchestration.ingestion_trigger_consumer import dispatch, validate_trigger
from persistence.reactive_triggers_log import list_by_session
from utils.kafka_utils import make_consumer

pytestmark = pytest.mark.usefixtures("db_available", "kafka_available")


# ==========================================================
# Helpers
# ==========================================================

# Per-test deadlines for Kafka polling. Generous enough to absorb broker
# latency on a loaded dev box; short enough to fail fast if a message
# never arrives. Two separate deadlines so a slow trigger-write doesn't
# eat into the Bronze-poll budget.
_TRIGGER_POLL_DEADLINE_SEC = 15.0
_BRONZE_POLL_DEADLINE_SEC = 15.0
_DISPATCH_THREAD_JOIN_TIMEOUT_SEC = 10.0


def _make_fixture_article(run_id: str, idx: int, domain: str = "reuters.com") -> dict:
    """
    Build a newsapi.ai-shaped article carrying a unique URL marker so the
    consumer of BRONZE_NEWSAPI can filter our test fixture articles out of
    whatever residue other E2E runs may have left in the topic.
    """
    url = f"https://{domain}/test-g3-{run_id}-{idx}"
    return {
        "url":         url,
        "title":       f"G3 fixture article {idx}",
        "description": "Sprint 23 Gate 3 fixture article body.",
        "body":        "Full body text for G3 fixture article.",
        "image":       "",
        "dateTime":    "2026-05-24T10:00:00Z",
        "source":      {"uri": domain, "title": "Reuters"},
        "authors":     [],
    }


def _poll_for_trigger_message(
    consumer, session_id: str, deadline_sec: float
) -> dict | None:
    """Poll until a trigger with the given session_id arrives, or until deadline."""
    deadline = time.time() + deadline_sec
    while time.time() < deadline:
        records = consumer.poll(timeout_ms=1_000, max_records=50)
        for _tp, msgs in records.items():
            for msg in msgs:
                if isinstance(msg.value, dict) and msg.value.get("session_id") == session_id:
                    return msg.value
    return None


def _wait_for_assignment(consumer, timeout_sec: float = 10.0) -> None:
    """
    Wait until the consumer has been assigned partitions by the group coordinator.

    Single-shot `poll()` typically returns before group join completes
    (group join takes ~3s on a loaded broker; default 2s `poll` returns
    empty-handed first). `seek_to_end()` called against a consumer with
    an empty assignment is a silent no-op — the consumer then falls back
    to `auto_offset_reset='earliest'` on the next poll and replays the
    entire topic history. For BRONZE_NEWSAPI with thousands of historical
    messages, the test's 15s deadline exhausts before reaching the
    fresh fixture articles at the end.

    This helper polls in a tight loop until `consumer.assignment()` is
    non-empty. `make_consumer(*topics, ...)` already subscribed the
    consumer at construction time, so `poll()` calls drive
    `ConsumerCoordinator.poll()` → `ensure_active_group()` →
    JoinGroup/SyncGroup as needed.

    On timeout, calls `pytest.skip(...)` rather than asserting — a slow
    group join is a broker-side environment issue (cold start, load),
    not a test logic failure. Surfacing it as a skip keeps the test
    report classifying correctly.

    Surfaced by Sprint 23 T23.9 Gate 3 (2026-05-26).
    """
    deadline = time.time() + timeout_sec
    while not consumer.assignment() and time.time() < deadline:
        consumer.poll(timeout_ms=500)
    if not consumer.assignment():
        pytest.skip(
            f"Consumer never got partition assignment within {timeout_sec}s "
            "— broker may be cold or under load. Re-run after broker warmup."
        )


def _poll_for_bronze_articles(
    consumer, run_id: str, expected_count: int, deadline_sec: float
) -> list[dict]:
    """Poll Bronze for envelopes whose raw_payload.url carries our test marker."""
    marker = f"test-g3-{run_id}"
    collected: list[dict] = []
    deadline = time.time() + deadline_sec
    while time.time() < deadline and len(collected) < expected_count:
        records = consumer.poll(timeout_ms=1_000, max_records=100)
        for _tp, msgs in records.items():
            for msg in msgs:
                raw = (msg.value or {}).get("payload", {}).get("raw_payload", {})
                if marker in (raw.get("url") or ""):
                    collected.append(msg.value)
    return collected


# ==========================================================
# Bundle G3: T23.9 — real Kafka + real Postgres
# ==========================================================


@pytest.fixture
def warm_trigger_producer():
    """
    Pre-warm the module-level singleton producer (D7) for this test, then
    reset it on teardown so subsequent tests start from a clean slate.

    Why warm: the first `_get_producer()` call in any process pays
    bootstrap + metadata-refresh (1-3s on dev). In production, a
    long-lived worker absorbs that cost once per process lifetime.
    Tests need to mirror that warm regime — otherwise the 2s
    `send().get(timeout=...)` budget gets eaten by metadata cost it was
    never designed to cover.

    The fixture force-resets at setup so we know we're constructing
    fresh, then yields. Teardown closes the warm singleton so test
    isolation holds.
    """
    node_module._reset_producer_for_tests()
    producer = node_module._get_producer()
    # Force metadata refresh for the topics this test will use, so the
    # actual `send().get()` round-trip in step 2 sees cached metadata.
    producer.partitions_for(INGESTION_TRIGGERS)
    producer.partitions_for(BRONZE_NEWSAPI)
    yield producer
    node_module._reset_producer_for_tests()


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "kafka-python-ng selector unregister race during bootstrap→coordinator "
        "handoff. Race exists on all platforms but reliably triggers on Windows "
        "local-dev (test processes create fresh consumers per run; Docker Desktop "
        "WSL2 layer + Windows scheduler make the closed-socket-in-selector window "
        "observable). Long-lived Linux/GKE consumers complete the handoff once at "
        "startup, never re-trigger. See KG-PHASE8-25 for full diagnosis."
    ),
)
class TestSprint23Gate3:
    """Gate 3 — integration against real Kafka + real Postgres.

    Skipped on Windows local-dev — see class-level @skipif. Test code is
    correct and ready for Linux; verification deferred to Linux CI / the
    cloud initial-test runtime where the long-lived agent worker pattern
    matches production conditions.
    """

    def test_trigger_round_trip_to_bronze_with_log_row(
        self,
        test_run_id,
        cleanup_reactive_triggers_log,
        warm_trigger_producer,
    ):
        """[G3.1] Single end-to-end integration:
          1. Pre-warm the singleton via `warm_trigger_producer` fixture.
          2. trigger_reactive_ingestion writes to real Kafka + real Postgres.
          3. We poll the trigger back from ingestion_triggers, validate it,
             dispatch it (with _fetch_articles patched at the class level).
          4. We poll BRONZE_NEWSAPI for the fixture articles to confirm the
             producer's real Bronze-emit path ran end-to-end.
          5. We confirm the Postgres log row's kafka_offset is populated
             from the real broker assignment.
        """
        session_id = f"test_{test_run_id}_g3"
        fixture_articles = [
            _make_fixture_article(test_run_id, 1, domain="reuters.com"),
            _make_fixture_article(test_run_id, 2, domain="apnews.com"),
        ]

        # -------- Step 2: agent node emits trigger + writes log row --------
        state = {
            "session_id":        session_id,
            "structured_intent": {"entities": [f"iran-g3-{test_run_id}"]},
        }
        delta = node_module.trigger_reactive_ingestion(state)
        assert delta == {"reactive_triggers_emitted": 1}

        # -------- Step 2b: Postgres log row written with real offset -------
        log_rows = list_by_session(session_id)
        assert len(log_rows) == 1, (
            "Expected exactly one reactive_triggers_log row after one emit."
        )
        row = log_rows[0]
        assert row["status"] == "emitted"
        assert row["source"] == "newsapi"
        assert row["keywords"] == [f"iran-g3-{test_run_id}"]
        assert row["kafka_offset"] is not None, (
            "Real Kafka broker must have returned an offset on send.get(). "
            "None means the send raised KafkaError — check broker health."
        )
        assert isinstance(row["kafka_offset"], int)

        # -------- Step 3: poll the trigger back from real Kafka ---------
        trigger_consumer = make_consumer(
            INGESTION_TRIGGERS,
            group_id=f"gate3-trigger-{test_run_id}",
        )
        try:
            trigger_payload = _poll_for_trigger_message(
                trigger_consumer, session_id, _TRIGGER_POLL_DEADLINE_SEC,
            )
        finally:
            trigger_consumer.close()

        assert trigger_payload is not None, (
            f"Trigger with session_id={session_id} never appeared in "
            f"{INGESTION_TRIGGERS} within {_TRIGGER_POLL_DEADLINE_SEC}s."
        )

        # Confirm the round-tripped payload shape is what T23.2 expects.
        assert validate_trigger(trigger_payload) == []
        assert trigger_payload["source"] == "newsapi"
        assert trigger_payload["keywords"] == [f"iran-g3-{test_run_id}"]
        # Forensic field — IGNORED by validate_trigger but carried for
        # correlation between the Kafka message and the PG log row.
        assert trigger_payload["session_id"] == session_id

        # -------- Step 4: stage Bronze consumer at topic end --------
        # Stage the Bronze consumer at topic end using auto_offset_reset='latest'.
        # Combined with _wait_for_assignment + a position-resolution poll, this
        # ensures the consumer's position lands at the current high-water mark
        # BEFORE dispatch starts emitting fixture articles. Original design used
        # seek_to_end() but that races with kafka-python-ng's deferred
        # position-reset machinery — see KG-PHASE8-26 in task_plan.md for the
        # full root-cause analysis. The two-stage SubscriptionState (assignment
        # received vs. positions resolved) means seek_to_end() can be silently
        # overridden by update_fetch_positions() on the next poll if positions
        # are still in `position_pending_reset` state.
        #
        # `auto_offset_reset='latest'` flips the position-reset machinery to
        # the safe direction: when positions ARE resolved, they land at the
        # high-water mark. Drop seek_to_end() entirely.
        bronze_consumer = make_consumer(
            BRONZE_NEWSAPI,
            group_id=f"gate3-bronze-{test_run_id}",
            auto_offset_reset="latest",
        )
        try:
            _wait_for_assignment(bronze_consumer)
            # Position resolution: assignment exists, but partitions may still
            # be in `position_pending_reset` state. A poll here drives
            # update_fetch_positions which applies auto_offset_reset='latest' →
            # all positions land at the current high-water mark. Without this,
            # dispatch could emit articles between assignment-established and
            # positions-resolved, and 'latest' would resolve to AFTER those
            # articles, dropping them. Cost: ~500ms guaranteed wait.
            bronze_consumer.poll(timeout_ms=500)

            # -------- Step 5: dispatch with mocked HTTP boundary --------
            # NewsAPIProducer._fetch_articles is patched at the class level
            # so whatever instance dispatch() creates uses the mock.
            # Everything else in the producer's flow (whitelist, dedup,
            # _build_raw_payload, _emit → real Kafka send) runs unmocked.
            with patch.object(
                NewsAPIProducer,
                "_fetch_articles",
                return_value=(fixture_articles, len(fixture_articles), 50),
            ):
                thread = dispatch(trigger_payload)
                thread.join(timeout=_DISPATCH_THREAD_JOIN_TIMEOUT_SEC)

            assert not thread.is_alive(), (
                "Dispatch thread did not complete within "
                f"{_DISPATCH_THREAD_JOIN_TIMEOUT_SEC}s — producer may be stuck."
            )

            # -------- Step 6: poll Bronze for our newly-emitted articles --
            bronze_messages = _poll_for_bronze_articles(
                bronze_consumer,
                test_run_id,
                expected_count=len(fixture_articles),
                deadline_sec=_BRONZE_POLL_DEADLINE_SEC,
            )
        finally:
            bronze_consumer.close()

        assert len(bronze_messages) == len(fixture_articles), (
            f"Expected {len(fixture_articles)} fixture articles in "
            f"{BRONZE_NEWSAPI} (marker test-g3-{test_run_id}); "
            f"got {len(bronze_messages)}."
        )

        # Spot-check the raw_payload shape — fetch_mode="reactive" is the
        # only field that distinguishes this run from a pulse/backfill run.
        for bmsg in bronze_messages:
            raw = bmsg["payload"]["raw_payload"]
            assert raw["fetch_mode"] == "reactive"
            assert raw["source"]["id"] in {"reuters", "apnews"}
            assert raw["url"].startswith("https://")
            assert f"test-g3-{test_run_id}" in raw["url"]
