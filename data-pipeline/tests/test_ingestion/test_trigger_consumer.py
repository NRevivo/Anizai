"""
Unit tests for orchestration/ingestion_trigger_consumer.py (Section 2.4).

No live Kafka connection required — consumer and DLQ producer are mocked.
Producer run_reactive() methods are patched at dispatch() call sites.

Test index:

validate_trigger() — schema validation [1–14]:
    [1]  non-dict payload returns error (type guard)
    [2]  missing 'source' returns error
    [3]  empty 'source' returns error
    [4]  unknown source returns error listing valid sources
    [5]  googletrends valid payload — no errors
    [6]  openweather valid payload — no errors
    [7]  opensky valid payload — no errors
    [8]  googletrends missing 'keywords' returns error
    [9]  openweather missing 'hotspot_names' returns error
    [10] opensky missing 'target_callsigns' returns error
    [11] googletrends keywords=[] (empty list) returns error
    [12] openweather hotspot_names=[] (empty list) returns error
    [13] opensky target_callsigns=[] (empty list) returns error
    [14] keywords is a string (not a list) returns error

dispatch() — producer routing [15–20]:
    [15] googletrends trigger calls GoogleTrendsProducer.run_reactive(keywords, geo)
    [16] openweather trigger calls OpenWeatherProducer.run_reactive(hotspot_names)
    [17] opensky trigger calls OpenSkyProducer.run_reactive(target_callsigns)
    [18] optional fields forwarded — googletrends geo + timeframe
    [19] optional fields forwarded — openweather window_minutes + poll_interval_sec
    [20] optional fields forwarded — opensky window_minutes + poll_interval_sec

run() — consumer loop routing [21–25]:
    [21] valid googletrends trigger → dispatch called, offset committed, DLQ not called
    [22] invalid trigger (missing source) → DLQ emitted, dispatch NOT called
    [23] invalid trigger (unknown source) → DLQ emitted with error text in record
    [24] malformed payload (non-dict raw bytes) → DLQ emitted, dispatch NOT called
    [25] multiple messages: valid then invalid → each routed correctly

References:
    - Section 2.4:  Reactive Ingestion — ingestion_triggers Kafka topic
    - Section 3.5:  Dead-Letter Queue — invalid triggers must not be silently dropped
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1 (schema) / Gate 2 (routing)
"""

import threading
from unittest.mock import MagicMock, call, patch

import pytest

from orchestration.ingestion_trigger_consumer import (
    VALID_SOURCES,
    dispatch,
    run,
    validate_trigger,
)


# ==========================================================
# Helpers
# ==========================================================

def _make_message(value: dict) -> MagicMock:
    """Return a mock Kafka message with .value set."""
    msg = MagicMock()
    msg.value = value
    return msg


def _make_consumer(*messages) -> MagicMock:
    """Return a mock KafkaConsumer that yields the given messages once."""
    consumer = MagicMock()
    consumer.__iter__ = MagicMock(return_value=iter(messages))
    return consumer


def _make_dlq_producer() -> MagicMock:
    """Return a mock KafkaProducer that records send() calls."""
    return MagicMock()


# ==========================================================
# validate_trigger() [1–14]
# ==========================================================

class TestValidateTrigger:

    def test_non_dict_payload_returns_error(self):
        # [1]
        errors = validate_trigger("not a dict")
        assert len(errors) == 1
        assert "dict" in errors[0]

    def test_missing_source_returns_error(self):
        # [2]
        errors = validate_trigger({"keywords": ["oil"]})
        assert any("source" in e for e in errors)

    def test_empty_source_returns_error(self):
        # [3]
        errors = validate_trigger({"source": "", "keywords": ["oil"]})
        assert any("source" in e for e in errors)

    def test_unknown_source_returns_error(self):
        # [4]
        errors = validate_trigger({"source": "reddit", "keywords": ["oil"]})
        assert len(errors) == 1
        assert "reddit" in errors[0]
        assert "Supported sources" in errors[0]

    def test_googletrends_valid(self):
        # [5]
        errors = validate_trigger({"source": "googletrends", "keywords": ["oil", "OPEC"]})
        assert errors == []

    def test_openweather_valid(self):
        # [6]
        errors = validate_trigger({"source": "openweather", "hotspot_names": ["Strait_of_Hormuz"]})
        assert errors == []

    def test_opensky_valid(self):
        # [7]
        errors = validate_trigger({"source": "opensky", "target_callsigns": ["SWR100"]})
        assert errors == []

    def test_googletrends_missing_keywords(self):
        # [8]
        errors = validate_trigger({"source": "googletrends"})
        assert any("keywords" in e for e in errors)

    def test_openweather_missing_hotspot_names(self):
        # [9]
        errors = validate_trigger({"source": "openweather"})
        assert any("hotspot_names" in e for e in errors)

    def test_opensky_missing_target_callsigns(self):
        # [10]
        errors = validate_trigger({"source": "opensky"})
        assert any("target_callsigns" in e for e in errors)

    def test_googletrends_empty_keywords_list(self):
        # [11]
        errors = validate_trigger({"source": "googletrends", "keywords": []})
        assert any("keywords" in e for e in errors)

    def test_openweather_empty_hotspot_names_list(self):
        # [12]
        errors = validate_trigger({"source": "openweather", "hotspot_names": []})
        assert any("hotspot_names" in e for e in errors)

    def test_opensky_empty_target_callsigns_list(self):
        # [13]
        errors = validate_trigger({"source": "opensky", "target_callsigns": []})
        assert any("target_callsigns" in e for e in errors)

    def test_keywords_not_a_list_returns_error(self):
        # [14]
        errors = validate_trigger({"source": "googletrends", "keywords": "oil price"})
        assert any("list" in e for e in errors)


# ==========================================================
# dispatch() — producer routing [15–20]
# ==========================================================

class TestDispatch:

    def _run_and_join(self, thread: threading.Thread, timeout: float = 2.0) -> None:
        thread.join(timeout=timeout)

    @patch("orchestration.ingestion_trigger_consumer._build_producer")
    def test_googletrends_calls_run_reactive_with_keywords(self, mock_build):
        # [15]
        mock_producer = MagicMock()
        mock_producer.run_reactive.return_value = 5
        mock_build.return_value = mock_producer

        trigger = {"source": "googletrends", "keywords": ["oil", "OPEC"], "geo": "US"}
        thread = dispatch(trigger)
        self._run_and_join(thread)

        mock_build.assert_called_once_with("googletrends")
        mock_producer.run_reactive.assert_called_once_with(keywords=["oil", "OPEC"], geo="US")

    @patch("orchestration.ingestion_trigger_consumer._build_producer")
    def test_openweather_calls_run_reactive_with_hotspot_names(self, mock_build):
        # [16]
        mock_producer = MagicMock()
        mock_producer.run_reactive.return_value = 3
        mock_build.return_value = mock_producer

        trigger = {"source": "openweather", "hotspot_names": ["Strait_of_Hormuz"]}
        thread = dispatch(trigger)
        self._run_and_join(thread)

        mock_build.assert_called_once_with("openweather")
        mock_producer.run_reactive.assert_called_once_with(
            hotspot_names=["Strait_of_Hormuz"]
        )

    @patch("orchestration.ingestion_trigger_consumer._build_producer")
    def test_opensky_calls_run_reactive_with_callsigns(self, mock_build):
        # [17]
        mock_producer = MagicMock()
        mock_producer.run_reactive.return_value = 2
        mock_build.return_value = mock_producer

        trigger = {"source": "opensky", "target_callsigns": ["SWR100", "AFR447"]}
        thread = dispatch(trigger)
        self._run_and_join(thread)

        mock_build.assert_called_once_with("opensky")
        mock_producer.run_reactive.assert_called_once_with(
            target_callsigns=["SWR100", "AFR447"]
        )

    @patch("orchestration.ingestion_trigger_consumer._build_producer")
    def test_googletrends_optional_geo_and_timeframe_forwarded(self, mock_build):
        # [18]
        mock_producer = MagicMock()
        mock_producer.run_reactive.return_value = 7
        mock_build.return_value = mock_producer

        trigger = {
            "source": "googletrends",
            "keywords": ["oil"],
            "geo": "IL",
            "timeframe": "today 7-d",
        }
        thread = dispatch(trigger)
        self._run_and_join(thread)

        mock_producer.run_reactive.assert_called_once_with(
            keywords=["oil"], geo="IL", timeframe="today 7-d"
        )

    @patch("orchestration.ingestion_trigger_consumer._build_producer")
    def test_openweather_optional_window_and_interval_forwarded(self, mock_build):
        # [19]
        mock_producer = MagicMock()
        mock_producer.run_reactive.return_value = 10
        mock_build.return_value = mock_producer

        trigger = {
            "source": "openweather",
            "hotspot_names": ["Suez_Canal"],
            "window_minutes": 30,
            "poll_interval_sec": 60,
        }
        thread = dispatch(trigger)
        self._run_and_join(thread)

        mock_producer.run_reactive.assert_called_once_with(
            hotspot_names=["Suez_Canal"], window_minutes=30, poll_interval_sec=60
        )

    @patch("orchestration.ingestion_trigger_consumer._build_producer")
    def test_opensky_optional_window_and_interval_forwarded(self, mock_build):
        # [20]
        mock_producer = MagicMock()
        mock_producer.run_reactive.return_value = 4
        mock_build.return_value = mock_producer

        trigger = {
            "source": "opensky",
            "target_callsigns": ["RFF123"],
            "window_minutes": 15,
            "poll_interval_sec": 30,
        }
        thread = dispatch(trigger)
        self._run_and_join(thread)

        mock_producer.run_reactive.assert_called_once_with(
            target_callsigns=["RFF123"], window_minutes=15, poll_interval_sec=30
        )


# ==========================================================
# run() — consumer loop routing [21–25]
# ==========================================================

class TestRun:

    @patch("orchestration.ingestion_trigger_consumer.dispatch")
    def test_valid_trigger_dispatched_offset_committed_dlq_not_called(self, mock_dispatch):
        # [21]
        mock_dispatch.return_value = MagicMock()
        consumer = _make_consumer(
            _make_message({"source": "googletrends", "keywords": ["oil"]})
        )
        dlq = _make_dlq_producer()

        run(consumer, dlq)

        mock_dispatch.assert_called_once()
        consumer.commit.assert_called_once()
        dlq.send.assert_not_called()

    @patch("orchestration.ingestion_trigger_consumer.dispatch")
    def test_invalid_trigger_missing_source_routes_to_dlq(self, mock_dispatch):
        # [22] Missing 'source' → DLQ, dispatch NOT called
        consumer = _make_consumer(
            _make_message({"keywords": ["oil"]})  # no 'source' key
        )
        dlq = _make_dlq_producer()

        run(consumer, dlq)

        mock_dispatch.assert_not_called()
        dlq.send.assert_called_once()
        dlq.flush.assert_called_once()
        consumer.commit.assert_called_once()

    @patch("orchestration.ingestion_trigger_consumer.dispatch")
    def test_invalid_trigger_unknown_source_dlq_contains_error(self, mock_dispatch):
        # [23] Unknown source → DLQ record has error text
        consumer = _make_consumer(
            _make_message({"source": "reddit", "posts": ["xyz"]})
        )
        dlq = _make_dlq_producer()

        run(consumer, dlq)

        mock_dispatch.assert_not_called()
        dlq.send.assert_called_once()

        # Extract the DLQ record from the send() call
        send_args = dlq.send.call_args
        dlq_record = send_args[1]["value"]  # keyword arg
        assert any("reddit" in e for e in dlq_record["errors"])
        assert dlq_record["failed_stage"] == "trigger_validation"

    @patch("orchestration.ingestion_trigger_consumer.dispatch")
    def test_non_dict_payload_routes_to_dlq(self, mock_dispatch):
        # [24] Non-dict payload (e.g. malformed JSON decoded as a string)
        consumer = _make_consumer(
            _make_message("this is not a dict")
        )
        dlq = _make_dlq_producer()

        run(consumer, dlq)

        mock_dispatch.assert_not_called()
        dlq.send.assert_called_once()

    @patch("orchestration.ingestion_trigger_consumer.dispatch")
    def test_mixed_valid_and_invalid_messages_routed_correctly(self, mock_dispatch):
        # [25] Two messages: valid googletrends then invalid (missing source)
        mock_dispatch.return_value = MagicMock()
        consumer = _make_consumer(
            _make_message({"source": "googletrends", "keywords": ["oil"]}),
            _make_message({"source": "unknown_source", "data": []}),
        )
        dlq = _make_dlq_producer()

        run(consumer, dlq)

        assert mock_dispatch.call_count == 1    # only the valid trigger dispatched
        assert dlq.send.call_count == 1         # only the invalid trigger DLQ'd
        assert consumer.commit.call_count == 2  # offset committed for both
