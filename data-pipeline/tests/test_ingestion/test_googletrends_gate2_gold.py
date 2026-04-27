"""
Gate 2 (Gold) — Logic Gate: Google Trends Gold Processing & Public_Hype_Alert.

Validates the Gold Job's Google Trends branch using mock payloads — no live
Flink cluster, no Kafka, no Pytrends, no OpenAI (fully deterministic math).

What Gate 2 Gold covers (Section 9.3 Gate 2):

  ROUTING & LAYER PROMOTION
  [01] Valid Silver metric + empty history  → (GOLD_STRUCTURED_METRICS, gold_record)
  [02] Gold record has layer == "Gold"       (Silver layer promoted)
  [03] process_googletrends_gold_message() always returns GOLD_STRUCTURED_METRICS

  MOMENTUM BLOCK — NO PRIOR HISTORY
  [04] Empty price_history → is_new_market == True
  [05] Empty price_history → change_24h == 0.0
  [06] Empty price_history → change_7d  == 0.0
  [07] Empty price_history → change_30d == 0.0

  MOMENTUM BLOCK — WITH PRIOR HISTORY  (_now injection for determinism)
  [08] Prior value at-or-before now-24h (25h ago) → change_24h computed correctly
  [09] Prior value more recent than now-24h (12h ago) → change_24h == 0.0
  [09b] Prior value at-or-before now-7d (8d ago) → change_7d computed correctly
  [10] Prior value at-or-before now-30d (31d ago) → change_30d computed correctly
  [11] is_new_market == False when price_history is non-empty

  PUBLIC_HYPE_ALERT TRIGGER (Section B.5: score delta > 50 pts in 24h)
  [12] score 20 → 80 in 24h  (+60 pts)  → public_hype_alert == True
  [13] score 40 → 85 in 24h  (+45 pts)  → public_hype_alert == False
  [14] score 25 → 75 in 24h  (exactly +50 pts) → public_hype_alert == False
       (threshold is strictly greater than 50, not >=)
  [15] score 24.9 → 75.1 in 24h (+50.2 pts)  → public_hype_alert == True
  [16] impact_level == 4 when alert fires
  [17] impact_level == 1 when alert does not fire
  [18] trigger_reason is a non-empty string when alert fires
  [19] trigger_reason is None when alert does not fire
  [20] abs_change_24h_pts is present in metadata_extension
  [21] abs_change_24h_pts == 0.0 when no prior 24h history (new keyword)
  [22] public_hype_alert == False when no prior 24h history (no score to compare against)

  IMMUTABILITY
  [23] Original silver_metric is not mutated (deep copy check)

  _now INJECTION
  [24] _now parameter allows deterministic delta assertions independent of wall-clock

  METADATA EXTENSION PRESERVATION
  [25] Original Silver metadata_extension fields are preserved after Gold enrichment
       (keyword, geo, geo_name, mode, rank_in_trending, timeframe survive the deep copy)

Test strategy:
    _make_silver_metric() builds realistic Silver records inline (same shape
    as map_googletrends_to_silver() produces). All momentum assertions use
    _now injection to prevent wall-clock-dependent test failures.

References:
    - Section 4.2B: Structural Enrichment — Momentum Block
    - Section B.5:  Google Trends automation trigger (Public_Hype_Alert)
    - Section C.2:  Silver/Gold Structured Metric schema
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section 4.4:  Mock-Driven Development (no live API calls)
"""

import copy
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from config.kafka_topics import GOLD_STRUCTURED_METRICS
from processing.gold_job import (
    _GOOGLETRENDS_HYPE_THRESHOLD,
    apply_googletrends_automation_triggers,
    build_gold_structured_metric,
    compute_momentum_block,
    process_googletrends_gold_message,
)
from processing.silver_job import map_googletrends_to_silver
from utils.kafka_utils import build_bronze_message
from ingestion.googletrends_producer import SOURCE_NAME


# ==========================================================
# Helpers
# ==========================================================

_FAKE_NOW = datetime(2026, 4, 2, 10, 0, 0, tzinfo=timezone.utc)


def _ts(delta_hours: float) -> str:
    """Return an ISO8601 UTC string offset from _FAKE_NOW by delta_hours."""
    return (_FAKE_NOW - timedelta(hours=delta_hours)).isoformat()


def _make_silver_metric(
    keyword: str = "Inflation",
    geo: str = "US",
    interest_score: float = 72.0,
    mode: str = "static",
    rank_in_trending: int = 5,
    observation_date: str = None,
    fetch_timestamp: str = "2026-04-02T10:00:00+00:00",
) -> dict:
    """
    Build a Silver Structured Metric by calling map_googletrends_to_silver()
    directly — produces exactly the same shape as the Silver Job outputs.
    """
    raw = {
        "keyword":         keyword,
        "geo":             geo,
        "geo_name":        "United States" if geo == "US" else geo,
        "interest_score":  interest_score,
        "mode":            mode,
        "fetch_timestamp": fetch_timestamp,
        "rank_in_trending": rank_in_trending if mode == "static" else None,
    }
    if observation_date:
        raw["observation_date"] = observation_date

    envelope = build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint="https://trends.google.com/trends/explore",
        raw_payload=raw,
    )
    return map_googletrends_to_silver(raw, envelope)


# ==========================================================
# [01–03] Routing & Layer Promotion
# ==========================================================

class TestRoutingAndLayerPromotion:
    """Gate 2 Gold checks [01]–[03]: return value and layer field."""

    def test_01_returns_gold_structured_metrics_tuple(self):
        """[01] (GOLD_STRUCTURED_METRICS, gold_record) returned for valid Silver input."""
        silver = _make_silver_metric()
        topic, gold = process_googletrends_gold_message(silver, [], _now=_FAKE_NOW)
        assert topic == GOLD_STRUCTURED_METRICS
        assert isinstance(gold, dict)

    def test_02_layer_promoted_to_gold(self):
        """[02] gold_record['layer'] == 'Gold' (was 'Silver' before enrichment)."""
        silver = _make_silver_metric()
        assert silver["layer"] == "Silver"  # confirm input is Silver
        _, gold = process_googletrends_gold_message(silver, [], _now=_FAKE_NOW)
        assert gold["layer"] == "Gold"

    def test_03_always_returns_gold_structured_metrics(self):
        """[03] No failure path — always GOLD_STRUCTURED_METRICS for valid Silver input."""
        for score in [0.0, 2.0, 50.0, 100.0]:
            silver = _make_silver_metric(interest_score=score)
            topic, _ = process_googletrends_gold_message(silver, [], _now=_FAKE_NOW)
            assert topic == GOLD_STRUCTURED_METRICS, (
                f"score={score}: expected GOLD_STRUCTURED_METRICS, got '{topic}'"
            )


# ==========================================================
# [04–07] Momentum Block — No Prior History
# ==========================================================

class TestMomentumNoHistory:
    """Gate 2 Gold checks [04]–[07]: empty price_history edge case."""

    def _gold_no_history(self, score: float = 72.0) -> dict:
        silver = _make_silver_metric(interest_score=score)
        _, gold = process_googletrends_gold_message(silver, [], _now=_FAKE_NOW)
        return gold

    def test_04_is_new_market_true_when_no_history(self):
        """[04] is_new_market == True when price_history is empty."""
        gold = self._gold_no_history()
        assert gold["momentum_block"]["is_new_market"] is True

    def test_05_change_24h_zero_when_no_history(self):
        """[05] change_24h == 0.0 when price_history is empty."""
        gold = self._gold_no_history()
        assert gold["momentum_block"]["change_24h"] == 0.0

    def test_06_change_7d_zero_when_no_history(self):
        """[06] change_7d == 0.0 when price_history is empty."""
        gold = self._gold_no_history()
        assert gold["momentum_block"]["change_7d"] == 0.0

    def test_07_change_30d_zero_when_no_history(self):
        """[07] change_30d == 0.0 when price_history is empty."""
        gold = self._gold_no_history()
        assert gold["momentum_block"]["change_30d"] == 0.0


# ==========================================================
# [08–11] Momentum Block — With Prior History
# ==========================================================

class TestMomentumWithHistory:
    """Gate 2 Gold checks [08]–[11]: momentum computed from price_history."""

    def test_08_change_24h_computed_correctly(self):
        """[08] Prior value 25h ago: at or before now-24h → qualifies as 24h reference."""
        ref_score = 50.0
        current   = 75.0
        history   = [(_ts(25), ref_score)]   # 25h ago — at or before now-24h cutoff

        silver = _make_silver_metric(interest_score=current)
        _, gold = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)

        expected = (current - ref_score) / ref_score  # 0.5
        assert gold["momentum_block"]["change_24h"] == pytest.approx(expected, abs=1e-4)

    def test_09_change_24h_zero_when_prior_outside_window(self):
        """[09] Prior value 12h ago: NOT at or before now-24h → no 24h reference → 0.0."""
        history = [(_ts(12), 50.0)]  # 12h ago — more recent than now-24h cutoff
        silver  = _make_silver_metric(interest_score=75.0)
        _, gold = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)
        assert gold["momentum_block"]["change_24h"] == 0.0

    def test_09b_change_7d_computed_when_prior_in_7d_window(self):
        """[09b] Prior value 8d ago: at or before now-7d → qualifies as 7d reference."""
        ref_score = 40.0
        current   = 60.0
        history   = [(_ts(8 * 24), ref_score)]   # 192h = 8 days ago — at or before now-7d cutoff

        silver = _make_silver_metric(interest_score=current)
        _, gold = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)

        expected_7d = (current - ref_score) / ref_score  # 0.5
        assert gold["momentum_block"]["change_7d"]  == pytest.approx(expected_7d, abs=1e-4)

    def test_10_change_30d_computed_when_prior_in_30d_window(self):
        """[10] Prior value 31d ago: at or before now-30d → qualifies as 30d reference."""
        ref_score = 30.0
        current   = 60.0
        history   = [(_ts(24 * 31), ref_score)]  # 744h = 31 days ago — at or before now-30d cutoff

        silver = _make_silver_metric(interest_score=current)
        _, gold = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)

        expected_30d = (current - ref_score) / ref_score  # 1.0
        assert gold["momentum_block"]["change_30d"] == pytest.approx(expected_30d, abs=1e-4)

    def test_11_is_new_market_false_when_history_exists(self):
        """[11] is_new_market == False when price_history is non-empty."""
        history = [(_ts(12), 50.0)]
        silver  = _make_silver_metric(interest_score=75.0)
        _, gold = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)
        assert gold["momentum_block"]["is_new_market"] is False


# ==========================================================
# [12–22] Public_Hype_Alert Trigger
# ==========================================================

class TestPublicHypeAlert:
    """Gate 2 Gold checks [12]–[22]: Public_Hype_Alert trigger behaviour."""

    def _gold_with_24h_prior(self, current: float, prior: float) -> dict:
        """Build a Gold record with a single prior value 25h ago (at or before now-24h cutoff)."""
        history = [(_ts(25), prior)]
        silver  = _make_silver_metric(interest_score=current)
        _, gold = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)
        return gold

    def test_12_alert_fires_on_60pt_jump(self):
        """[12] Score 20 → 80 (+60 pts in 24h) → public_hype_alert == True."""
        gold = self._gold_with_24h_prior(current=80.0, prior=20.0)
        assert gold["metadata_extension"]["public_hype_alert"] is True

    def test_13_alert_does_not_fire_on_45pt_jump(self):
        """[13] Score 40 → 85 (+45 pts in 24h) → public_hype_alert == False."""
        gold = self._gold_with_24h_prior(current=85.0, prior=40.0)
        assert gold["metadata_extension"]["public_hype_alert"] is False

    def test_14_alert_does_not_fire_at_exactly_50pts(self):
        """[14] Exactly +50 pts → public_hype_alert == False (threshold is strictly > 50)."""
        # Score 25 → 75: abs_change = |75 × 2.0 / 3.0| = 50.0 exactly
        gold = self._gold_with_24h_prior(current=75.0, prior=25.0)
        abs_pts = gold["metadata_extension"]["abs_change_24h_pts"]
        assert abs_pts == pytest.approx(50.0, abs=1e-3)
        assert gold["metadata_extension"]["public_hype_alert"] is False, (
            "Alert must NOT fire at exactly 50 pts (threshold is strictly > 50)"
        )

    def test_15_alert_fires_just_above_50pts(self):
        """[15] Score 24.9 → 75.1 (+50.2 pts) → public_hype_alert == True."""
        gold = self._gold_with_24h_prior(current=75.1, prior=24.9)
        assert gold["metadata_extension"]["public_hype_alert"] is True

    def test_16_impact_level_4_when_alert_fires(self):
        """[16] impact_level == 4 when public_hype_alert fires."""
        gold = self._gold_with_24h_prior(current=80.0, prior=20.0)
        assert gold["metadata_extension"]["impact_level"] == 4

    def test_17_impact_level_1_when_alert_does_not_fire(self):
        """[17] impact_level == 1 when public_hype_alert does not fire."""
        gold = self._gold_with_24h_prior(current=85.0, prior=40.0)
        assert gold["metadata_extension"]["impact_level"] == 1

    def test_18_trigger_reason_nonempty_when_alert_fires(self):
        """[18] trigger_reason is a non-empty string when alert fires."""
        gold = self._gold_with_24h_prior(current=80.0, prior=20.0)
        reason = gold["metadata_extension"]["trigger_reason"]
        assert isinstance(reason, str) and reason.strip() != "", (
            "trigger_reason must be a non-empty string when alert fires"
        )

    def test_19_trigger_reason_none_when_alert_does_not_fire(self):
        """[19] trigger_reason is None when alert does not fire."""
        gold = self._gold_with_24h_prior(current=85.0, prior=40.0)
        assert gold["metadata_extension"]["trigger_reason"] is None

    def test_20_abs_change_24h_pts_present_in_metadata(self):
        """[20] abs_change_24h_pts is always present in metadata_extension."""
        for current, prior in [(80.0, 20.0), (85.0, 40.0)]:
            gold = self._gold_with_24h_prior(current, prior)
            assert "abs_change_24h_pts" in gold["metadata_extension"]

    def test_21_abs_change_24h_pts_zero_for_new_keyword(self):
        """[21] abs_change_24h_pts == 0.0 when no prior 24h history (new keyword)."""
        silver = _make_silver_metric(interest_score=72.0)
        _, gold = process_googletrends_gold_message(silver, [], _now=_FAKE_NOW)
        assert gold["metadata_extension"]["abs_change_24h_pts"] == pytest.approx(0.0)

    def test_22_alert_false_for_new_keyword(self):
        """[22] public_hype_alert == False when no prior 24h history (nothing to compare)."""
        silver = _make_silver_metric(interest_score=95.0)
        _, gold = process_googletrends_gold_message(silver, [], _now=_FAKE_NOW)
        assert gold["metadata_extension"]["public_hype_alert"] is False


# ==========================================================
# [23] Immutability
# ==========================================================

class TestImmutability:
    """Gate 2 Gold check [23]: original Silver record not mutated."""

    def test_23_silver_metric_not_mutated(self):
        """[23] Original silver_metric is unchanged after process_googletrends_gold_message()."""
        silver = _make_silver_metric(interest_score=72.0)
        silver_before = copy.deepcopy(silver)

        history = [(_ts(12), 40.0)]
        process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)

        assert silver == silver_before, (
            "process_googletrends_gold_message() mutated the input silver_metric"
        )


# ==========================================================
# [24] _now Injection
# ==========================================================

class TestNowInjection:
    """Gate 2 Gold check [24]: _now parameter enables deterministic assertions."""

    def test_24_now_injection_makes_deltas_deterministic(self):
        """[24] Two calls with identical _now and history produce identical deltas."""
        history = [(_ts(12), 40.0)]
        silver  = _make_silver_metric(interest_score=72.0)

        _, gold_a = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)
        _, gold_b = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)

        assert gold_a["momentum_block"] == gold_b["momentum_block"], (
            "_now injection must produce identical momentum blocks on repeated calls"
        )


# ==========================================================
# [25] Metadata Preservation
# ==========================================================

class TestMetadataPreservation:
    """Gate 2 Gold check [25]: Silver metadata_extension fields survive Gold enrichment."""

    def test_25_silver_metadata_fields_preserved_in_gold(self):
        """[25] keyword, geo, geo_name, mode, rank_in_trending carried through to Gold."""
        silver = _make_silver_metric(
            keyword="Crude Oil", geo="US",
            interest_score=60.0, mode="static", rank_in_trending=3,
        )
        history = [(_ts(12), 40.0)]
        _, gold = process_googletrends_gold_message(silver, history, _now=_FAKE_NOW)

        meta = gold["metadata_extension"]
        assert meta["keyword"]          == "Crude Oil"
        assert meta["geo"]              == "US"
        assert meta["geo_name"]         == "United States"
        assert meta["mode"]             == "static"
        assert meta["rank_in_trending"] == 3
        # Gold-added fields also present
        assert "public_hype_alert"  in meta
        assert "abs_change_24h_pts" in meta
        assert "impact_level"       in meta
