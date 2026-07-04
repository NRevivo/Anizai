"""
Gate 2 — NewsAPI Error-Envelope Guard (Phase 7B.5-I diagnostic stint, 2026-07-02).

Background: on smoke day the cloud pulse runs made 15 provider-counted
requests that all returned zero articles, and NOTHING surfaced — the DAG
reported success. Root gap: newsapi.ai can answer HTTP 200 with an
{"error": "..."} envelope (rejected query, plan restriction, origin block),
and `_fetch_articles` parsed that as `articles=[]`, indistinguishable from a
quiet no-news window.

The stint fix under test:
  [1] `_fetch_articles` raises ValueError on a 200 + error envelope, carrying
      the provider's message.
  [2] `run_pulse` routes that into its (KeyError, ValueError) handler — ERROR
      log with the envelope text, category skipped, run continues, no crash.
  [3] `run_pulse` logs a WARNING for a well-formed but zero-article category
      (anomalous for continuously-publishing pulse categories) — the T8
      first-hour NO-GO signal.
  [4] `_backfill_date_range` stops pagination on the envelope (new
      (KeyError, ValueError) branch) instead of crashing the operator run.
  [5] Falsy `error` keys and normal envelopes stay non-raising (no false
      positives).

References:
    - docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md §7 (T8 NO-GO)
    - ingestion/newsapi_producer.py `_fetch_articles` / `run_pulse` /
      `_backfill_date_range`
"""

from __future__ import annotations

import logging
from datetime import date
from unittest.mock import MagicMock, patch

import pytest

from ingestion.newsapi_producer import NewsAPIProducer, PULSE_CATEGORIES


# ==========================================================
# Helpers
# ==========================================================

def _producer() -> NewsAPIProducer:
    """Kafka-free producer instance (same __new__ pattern as the E2E runners)."""
    p = NewsAPIProducer.__new__(NewsAPIProducer)
    p._emitted            = 0
    p._filtered_whitelist = 0
    p._producer           = MagicMock()   # Kafka producer stub (flush() in run_pulse)
    return p


def _response(payload: dict, status: int = 200) -> MagicMock:
    resp = MagicMock()
    resp.status_code = status
    resp.json.return_value = payload
    resp.raise_for_status.return_value = None
    return resp


_ERROR_ENVELOPE = {"error": "Invalid request: your plan does not permit this query"}
_EMPTY_ENVELOPE = {"articles": {"results": [], "totalResults": 0}}
_ONE_ARTICLE_ENVELOPE = {
    "articles": {
        "results": [{
            "url":    "https://www.reuters.com/x",
            "title":  "Test article",
            "body":   "Body.",
            "source": {"uri": "reuters.com", "title": "Reuters"},
        }],
        "totalResults": 1,
    }
}


# ==========================================================
# [1] _fetch_articles — envelope detection
# ==========================================================

class TestFetchArticlesEnvelopeGuard:

    def test_error_envelope_raises_value_error_with_provider_message(self):
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(_ERROR_ENVELOPE)):
            with pytest.raises(ValueError, match="does not permit this query"):
                _producer()._fetch_articles("news/Business")

    def test_normal_empty_envelope_returns_empty_list_without_raising(self):
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(_EMPTY_ENVELOPE)):
            articles, total, _ = _producer()._fetch_articles("news/Business")
        assert articles == []
        assert total == 0

    def test_normal_article_envelope_unaffected(self):
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(_ONE_ARTICLE_ENVELOPE)):
            articles, total, _ = _producer()._fetch_articles("news/Business")
        assert len(articles) == 1
        assert total == 1

    @pytest.mark.parametrize("falsy", ["", None])
    def test_falsy_error_key_does_not_raise(self, falsy):
        """data.get('error') truthiness guard — no false positives."""
        payload = {"error": falsy, **_EMPTY_ENVELOPE}
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(payload)):
            articles, total, _ = _producer()._fetch_articles("news/Business")
        assert articles == []

    def test_http_error_still_propagates_via_raise_for_status(self):
        """The pre-existing 4xx/5xx path is untouched by the new guard."""
        import requests as _requests
        resp = _response({}, status=429)
        resp.raise_for_status.side_effect = _requests.HTTPError("429 Too Many Requests")
        with patch("ingestion.newsapi_producer.requests.get", return_value=resp):
            with pytest.raises(_requests.HTTPError):
                _producer()._fetch_articles("news/Business")


# ==========================================================
# [2] + [3] run_pulse — loud skip + zero-article warning
# ==========================================================

class TestRunPulseVisibility:

    def test_error_envelope_logged_at_error_and_run_survives(self, caplog):
        producer = _producer()
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(_ERROR_ENVELOPE)), \
             patch("ingestion.newsapi_producer.time.sleep"):
            with caplog.at_level(logging.ERROR, logger="ingestion.newsapi_producer"):
                producer.run_pulse()   # must not raise

        error_lines = [r for r in caplog.records if r.levelno == logging.ERROR]
        assert len(error_lines) == len(PULSE_CATEGORIES), (
            "Every category's provider rejection must produce one ERROR line"
        )
        assert "does not permit this query" in caplog.text, (
            "The provider's message must reach the log — that is the whole point"
        )
        producer._producer.flush.assert_called_once()

    def test_zero_article_category_logs_warning(self, caplog):
        producer = _producer()
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(_EMPTY_ENVELOPE)), \
             patch("ingestion.newsapi_producer.time.sleep"):
            with caplog.at_level(logging.WARNING, logger="ingestion.newsapi_producer"):
                producer.run_pulse()

        warnings = [
            r for r in caplog.records
            if r.levelno == logging.WARNING and "returned 0 articles" in r.getMessage()
        ]
        assert len(warnings) == len(PULSE_CATEGORIES)

    def test_populated_category_logs_no_zero_warning(self, caplog):
        producer = _producer()
        # Emit path needs Kafka — patch _process_and_emit; the warning fires
        # BEFORE emit, so the assertion target is unaffected.
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(_ONE_ARTICLE_ENVELOPE)), \
             patch.object(producer, "_process_and_emit", return_value=1), \
             patch("ingestion.newsapi_producer.time.sleep"):
            with caplog.at_level(logging.WARNING, logger="ingestion.newsapi_producer"):
                producer.run_pulse()

        assert "returned 0 articles" not in caplog.text


# ==========================================================
# [4] _backfill_date_range — stop-pagination on envelope
# ==========================================================

class TestBackfillEnvelopeHandling:

    def test_error_envelope_stops_pagination_cleanly(self, caplog):
        producer = _producer()
        with patch("ingestion.newsapi_producer.requests.get",
                   return_value=_response(_ERROR_ENVELOPE)), \
             patch("ingestion.newsapi_producer.time.sleep"):
            with caplog.at_level(logging.ERROR, logger="ingestion.newsapi_producer"):
                emitted = producer._backfill_date_range(
                    from_date=date(2026, 6, 1),
                    to_date=date(2026, 6, 2),
                    domains=None,
                    tier_name="full",
                )
        assert emitted == 0
        assert "stopping pagination" in caplog.text
        assert "does not permit this query" in caplog.text
