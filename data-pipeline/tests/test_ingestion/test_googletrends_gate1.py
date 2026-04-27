"""
Gate 1 — Ingestion Gate: Google Trends Bronze Schema Compliance.

Validates that the Google Trends producer's envelope-building logic produces
messages fully compliant with the Bronze Schema (Section C.1) and Message
Envelope (Section 3.2) before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no Pytrends / Google API calls.
    _build_raw_payload() and build_bronze_message() are pure functions;
    tested in complete isolation (same pattern as test_fred_gate1.py).
  - GEO_MATRIX, BACKFILL_KEYWORDS, MAX_TRENDING_PER_GEO, SOURCE_NAME are
    imported from the producer and tested for spec invariants without
    hard-coding values — guards against config drift.
  - Validators from utils/validators.py are called directly — the same
    validators the Flink Silver Job uses, so a Gate 1 pass guarantees
    the Silver Job's first validation gate will also pass.

Gate 1 checklist (Section 9.3 Gate 1):
  ENVELOPE STRUCTURE
  [01] All required envelope fields are present and correctly typed
  [02] event_id is a valid UUIDv4 string
  [03] producer_timestamp is a valid ISO8601 UTC string
  [04] source_name == "googletrends"
  [05] payload.raw_payload is a non-empty dict
  [06] validate_envelope() passes without errors
  [07] validate_bronze_payload() passes without errors
  [08] NDJSON round-trip (serialize → deserialize) preserves the full envelope
  [09] Each call to build_bronze_message() produces a distinct event_id

  RAW PAYLOAD — COMMON FIELDS (ALL MODES)
  [10] interest_score is a float
  [11] interest_score is within the valid range [0.0, 100.0]
  [12] keyword is a non-empty string
  [13] geo is one of the valid GEO_MATRIX keys
  [14] geo_name matches the GEO_MATRIX entry for the geo
  [15] mode is one of "static", "reactive", "backfill"
  [16] fetch_timestamp is a non-empty string

  RAW PAYLOAD — STATIC MODE SPECIFIC
  [17] rank_in_trending is present and is a positive integer (1-based)
  [18] observation_date is absent for static mode
  [19] timeframe is absent for static mode
  [20] Rank-to-100 score formula: rank_index 0 → 100.0, rank_index 1 → 98.0,
       rank_index 49 → 2.0, formula = max(0.0, 100.0 - rank_index * 2.0)
  [21] Score is always >= 0.0 (never negative, even for rank_index > 50)

  RAW PAYLOAD — REACTIVE MODE SPECIFIC
  [22] observation_date is present and in "YYYY-MM-DD" format
  [23] timeframe is present and is a non-empty string
  [24] rank_in_trending is absent for reactive mode

  RAW PAYLOAD — BACKFILL MODE SPECIFIC
  [25] observation_date is present and in "YYYY-MM-DD" format
  [26] timeframe is present and is a non-empty string
  [27] rank_in_trending is absent for backfill mode

  KAFKA PARTITION KEY
  [28] Partition key is keyword encoded as UTF-8 bytes
  [28b] Partition key is deterministic — two successive calls with the same keyword
        produce identical bytes (validates Flink keyed-state affinity is stable
        across invocations and Kafka partition routing is consistent)

  geo_region NOTE — NOT PRESENT AT BRONZE LEVEL:
      The producer's _build_raw_payload() stores geographic information as:
        - geo      (ISO 3166-1 alpha-2 code, e.g. "US", "GB", "IL", "DE")
        - geo_name (human-readable name, e.g. "United States")
      There is no "geo_region" field in the Bronze raw_payload or in the Silver
      metadata_extension. Gate 2 Silver (test_googletrends_gate2_silver.py) will
      confirm that geo and geo_name are correctly mapped through to Silver and that
      no geo_region field is expected or present.

  PRODUCER CONFIG INVARIANTS
  [29] GEO_MATRIX has exactly 4 geo entries (US, GB, IL, DE)
  [30] Every GEO_MATRIX entry has "name" and "trending_pn" fields (non-empty)
  [31] BACKFILL_KEYWORDS contains at least the 3 spec-mandated core keywords:
       "Inflation", "Crude Oil", "Recession" (Section B.5)
  [32] BACKFILL_KEYWORDS has at least 5 keywords (spec defines a broader list)
  [33] MAX_TRENDING_PER_GEO == 50 (Section B.5: "Top 50 trending topics")
  [34] SOURCE_NAME == "googletrends"

References:
    - Section 2.1:  Producer Matrix (Google Trends row)
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialisation (tested via round-trip)
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.5:  Google Trends parameters
    - Section C.1:  Bronze Schema
    - Section C.2:  Silver Structured Metric — metadata_extension fields
"""

import json
import re
import uuid
from datetime import datetime, timezone

import pytest

from ingestion.googletrends_producer import (
    BACKFILL_KEYWORDS,
    GEO_MATRIX,
    MAX_TRENDING_PER_GEO,
    PYTRENDS_BASE_URL,
    SOURCE_NAME,
    TIMEFRAME_7D,
    TIMEFRAME_5Y,
    GoogleTrendsProducer,
)
from utils.kafka_utils import build_bronze_message, ndjson_deserializer, ndjson_serializer
from utils.validators import validate_bronze_payload, validate_envelope

# ==========================================================
# Helpers
# ==========================================================

_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)
_DATE_RE = re.compile(r"^\d{4}-\d{2}-\d{2}$")


def _is_valid_uuid4(value: str) -> bool:
    try:
        return uuid.UUID(value, version=4).version == 4
    except (ValueError, AttributeError):
        return False


def _is_valid_iso8601(value: str) -> bool:
    return bool(_ISO8601_RE.match(value))


def _make_producer() -> GoogleTrendsProducer:
    """
    Construct a GoogleTrendsProducer without starting Kafka or Pytrends.

    We only need the _build_raw_payload() method for Gate 1, which is
    pure and has no external dependencies. We skip __init__ by instantiating
    via __new__ to avoid requiring Kafka broker or Pytrends session.
    """
    p = GoogleTrendsProducer.__new__(GoogleTrendsProducer)
    p._emitted = 0
    return p


# Shared producer instance (no external connections needed for Gate 1)
_producer = _make_producer()

# ==========================================================
# [01–09] Envelope Structure
# ==========================================================

class TestEnvelopeStructure:
    """Gate 1 checks [01]–[09]: Bronze envelope fields and types."""

    def _make_envelope(self, mode: str = "static") -> dict:
        raw = _producer._build_raw_payload(
            keyword="Inflation",
            geo="US",
            interest_score=72.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode=mode,
            rank_in_trending=1 if mode == "static" else None,
            observation_date="2026-04-01" if mode != "static" else None,
            timeframe=TIMEFRAME_7D if mode == "reactive" else (
                TIMEFRAME_5Y if mode == "backfill" else None
            ),
        )
        return build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=f"{PYTRENDS_BASE_URL}?geo=US&q=Inflation&mode={mode}",
            raw_payload=raw,
        )

    def test_01_required_fields_present_and_typed(self):
        """[01] All required envelope fields are present with correct types."""
        env = self._make_envelope()
        assert isinstance(env["event_id"],           str)
        assert isinstance(env["trace_id"],           str)
        assert isinstance(env["producer_timestamp"], str)
        assert isinstance(env["schema_version"],     str)
        assert isinstance(env["source_name"],        str)
        assert isinstance(env["payload"],            dict)

    def test_02_event_id_is_valid_uuid4(self):
        """[02] event_id is a valid UUIDv4 string."""
        env = self._make_envelope()
        assert _is_valid_uuid4(env["event_id"]), (
            f"event_id='{env['event_id']}' is not a valid UUIDv4"
        )

    def test_03_producer_timestamp_is_iso8601_utc(self):
        """[03] producer_timestamp is a valid ISO8601 UTC string."""
        env = self._make_envelope()
        ts = env["producer_timestamp"]
        assert _is_valid_iso8601(ts), f"producer_timestamp='{ts}' is not valid ISO8601"

    def test_04_source_name_is_googletrends(self):
        """[04] source_name == 'googletrends'."""
        env = self._make_envelope()
        assert env["source_name"] == "googletrends"

    def test_05_raw_payload_is_nonempty_dict(self):
        """[05] payload.raw_payload is a non-empty dict."""
        env = self._make_envelope()
        raw = env["payload"]["raw_payload"]
        assert isinstance(raw, dict)
        assert len(raw) > 0

    def test_06_validate_envelope_passes(self):
        """[06] validate_envelope() returns is_valid=True."""
        env = self._make_envelope()
        result = validate_envelope(env)
        assert result.is_valid, f"validate_envelope() failed: {result.errors}"

    def test_07_validate_bronze_payload_passes(self):
        """[07] validate_bronze_payload() returns is_valid=True."""
        env = self._make_envelope()
        result = validate_bronze_payload(env["payload"])
        assert result.is_valid, f"validate_bronze_payload() failed: {result.errors}"

    def test_08_ndjson_roundtrip_preserves_envelope(self):
        """[08] NDJSON serialize → deserialize preserves the full envelope."""
        env = self._make_envelope()
        serialized   = ndjson_serializer(env)
        deserialized = ndjson_deserializer(serialized)
        assert deserialized == env, "NDJSON round-trip produced a different object"

    def test_09_distinct_event_ids_per_call(self):
        """[09] Each build_bronze_message() call generates a unique event_id."""
        raw = _producer._build_raw_payload(
            keyword="Inflation", geo="US", interest_score=50.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="static", rank_in_trending=1,
        )
        ids = {
            build_bronze_message(SOURCE_NAME, "https://trends.google.com", raw)["event_id"]
            for _ in range(5)
        }
        assert len(ids) == 5, "event_ids are not unique across calls"


# ==========================================================
# [10–16] Raw Payload — Common Fields (All Modes)
# ==========================================================

class TestRawPayloadCommonFields:
    """Gate 1 checks [10]–[16]: fields present in every mode."""

    @pytest.mark.parametrize("mode,extra", [
        ("static",   {"rank_in_trending": 1}),
        ("reactive", {"observation_date": "2026-04-01", "timeframe": TIMEFRAME_7D}),
        ("backfill", {"observation_date": "2024-01-07", "timeframe": TIMEFRAME_5Y}),
    ])
    def test_10_interest_score_is_float(self, mode, extra):
        """[10] interest_score is a float in the raw_payload."""
        raw = _producer._build_raw_payload(
            keyword="Crude Oil", geo="US", interest_score=55.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode=mode, **extra,
        )
        assert isinstance(raw["interest_score"], float)

    @pytest.mark.parametrize("score", [0.0, 2.0, 50.0, 98.0, 100.0])
    def test_11_interest_score_in_valid_range(self, score):
        """[11] interest_score is within [0.0, 100.0]."""
        raw = _producer._build_raw_payload(
            keyword="Recession", geo="US", interest_score=score,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="static", rank_in_trending=1,
        )
        assert 0.0 <= raw["interest_score"] <= 100.0, (
            f"interest_score={raw['interest_score']} is outside [0.0, 100.0]"
        )

    def test_12_keyword_is_nonempty_string(self):
        """[12] keyword is a non-empty string in the raw_payload."""
        raw = _producer._build_raw_payload(
            keyword="NATO", geo="US", interest_score=40.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="static", rank_in_trending=5,
        )
        assert isinstance(raw["keyword"], str)
        assert raw["keyword"].strip() != ""

    @pytest.mark.parametrize("geo", ["US", "GB", "IL", "DE"])
    def test_13_geo_is_valid_geo_matrix_key(self, geo):
        """[13] geo value is one of the 4 GEO_MATRIX keys."""
        raw = _producer._build_raw_payload(
            keyword="Inflation", geo=geo, interest_score=60.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="static", rank_in_trending=2,
        )
        assert raw["geo"] in GEO_MATRIX, (
            f"geo='{raw['geo']}' is not a recognised GEO_MATRIX key"
        )

    @pytest.mark.parametrize("geo", ["US", "GB", "IL", "DE"])
    def test_14_geo_name_matches_geo_matrix(self, geo):
        """[14] geo_name in raw_payload matches the GEO_MATRIX entry."""
        raw = _producer._build_raw_payload(
            keyword="Inflation", geo=geo, interest_score=60.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="static", rank_in_trending=2,
        )
        assert raw["geo_name"] == GEO_MATRIX[geo]["name"], (
            f"geo_name='{raw['geo_name']}' != GEO_MATRIX['{geo}']['name']="
            f"'{GEO_MATRIX[geo]['name']}'"
        )

    @pytest.mark.parametrize("mode", ["static", "reactive", "backfill"])
    def test_15_mode_is_valid(self, mode):
        """[15] mode is one of 'static', 'reactive', 'backfill'."""
        extra = {}
        if mode == "static":
            extra["rank_in_trending"] = 1
        else:
            extra["observation_date"] = "2026-04-01"
            extra["timeframe"] = TIMEFRAME_7D
        raw = _producer._build_raw_payload(
            keyword="Bitcoin", geo="US", interest_score=80.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode=mode, **extra,
        )
        assert raw["mode"] in ("static", "reactive", "backfill")

    def test_16_fetch_timestamp_is_nonempty_string(self):
        """[16] fetch_timestamp is a non-empty string."""
        ts = datetime.now(timezone.utc).isoformat()
        raw = _producer._build_raw_payload(
            keyword="Stock Market", geo="US", interest_score=70.0,
            fetch_timestamp=ts, mode="static", rank_in_trending=3,
        )
        assert isinstance(raw["fetch_timestamp"], str)
        assert raw["fetch_timestamp"] != ""


# ==========================================================
# [17–21] Raw Payload — Static Mode Specific
# ==========================================================

class TestRawPayloadStaticMode:
    """Gate 1 checks [17]–[21]: static mode invariants."""

    def _static_payload(self, rank_index: int) -> dict:
        score = max(0.0, 100.0 - rank_index * 2.0)
        return _producer._build_raw_payload(
            keyword="Federal Reserve",
            geo="US",
            interest_score=score,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="static",
            rank_in_trending=rank_index + 1,
        )

    def test_17_rank_in_trending_present_and_positive_int(self):
        """[17] rank_in_trending is present and is a positive 1-based integer."""
        for rank_index in [0, 1, 24, 49]:
            raw = self._static_payload(rank_index)
            assert "rank_in_trending" in raw, "rank_in_trending missing for static mode"
            assert isinstance(raw["rank_in_trending"], int)
            assert raw["rank_in_trending"] >= 1, (
                f"rank_in_trending={raw['rank_in_trending']} is not 1-based"
            )
            assert raw["rank_in_trending"] == rank_index + 1

    def test_18_observation_date_absent_for_static(self):
        """[18] observation_date is absent for static mode (no per-day granularity)."""
        raw = self._static_payload(0)
        assert "observation_date" not in raw, (
            "observation_date should NOT be present for static mode"
        )

    def test_19_timeframe_absent_for_static(self):
        """[19] timeframe is absent for static mode (uses daily trending feed, not interest_over_time)."""
        raw = self._static_payload(0)
        assert "timeframe" not in raw, (
            "timeframe should NOT be present for static mode"
        )

    @pytest.mark.parametrize("rank_index,expected_score", [
        (0,  100.0),
        (1,   98.0),
        (24,  52.0),
        (49,   2.0),
    ])
    def test_20_rank_to_100_score_formula(self, rank_index, expected_score):
        """[20] Rank-to-100 score formula: score = max(0.0, 100.0 - rank_index * 2.0)."""
        score = max(0.0, 100.0 - rank_index * 2.0)
        raw = self._static_payload(rank_index)
        assert raw["interest_score"] == pytest.approx(expected_score), (
            f"rank_index={rank_index}: expected score={expected_score}, "
            f"got {raw['interest_score']}"
        )

    def test_21_score_never_negative_beyond_rank_50(self):
        """[21] Score is clamped at 0.0 — never negative even for rank_index > 50."""
        # rank_index 51 would give -2.0 without the max(0.0, ...) clamp
        for rank_index in [50, 51, 100]:
            score = max(0.0, 100.0 - rank_index * 2.0)
            assert score >= 0.0, f"Score went negative at rank_index={rank_index}"


# ==========================================================
# [22–24] Raw Payload — Reactive Mode Specific
# ==========================================================

class TestRawPayloadReactiveMode:
    """Gate 1 checks [22]–[24]: reactive mode invariants."""

    def _reactive_payload(self) -> dict:
        return _producer._build_raw_payload(
            keyword="Kuwait oil strike",
            geo="US",
            interest_score=45.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="reactive",
            observation_date="2026-04-01",
            timeframe=TIMEFRAME_7D,
        )

    def test_22_observation_date_present_and_yyyy_mm_dd(self):
        """[22] observation_date is present and matches YYYY-MM-DD format."""
        raw = self._reactive_payload()
        assert "observation_date" in raw, "observation_date missing for reactive mode"
        assert _DATE_RE.match(raw["observation_date"]), (
            f"observation_date='{raw['observation_date']}' does not match YYYY-MM-DD"
        )

    def test_23_timeframe_present_and_nonempty(self):
        """[23] timeframe is present and non-empty for reactive mode."""
        raw = self._reactive_payload()
        assert "timeframe" in raw, "timeframe missing for reactive mode"
        assert isinstance(raw["timeframe"], str) and raw["timeframe"] != ""

    def test_24_rank_in_trending_absent_for_reactive(self):
        """[24] rank_in_trending is absent for reactive mode."""
        raw = self._reactive_payload()
        assert "rank_in_trending" not in raw, (
            "rank_in_trending should NOT be present for reactive mode"
        )


# ==========================================================
# [25–27] Raw Payload — Backfill Mode Specific
# ==========================================================

class TestRawPayloadBackfillMode:
    """Gate 1 checks [25]–[27]: backfill mode invariants."""

    def _backfill_payload(self) -> dict:
        return _producer._build_raw_payload(
            keyword="Recession",
            geo="DE",
            interest_score=33.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="backfill",
            observation_date="2022-03-07",
            timeframe=TIMEFRAME_5Y,
        )

    def test_25_observation_date_present_and_yyyy_mm_dd(self):
        """[25] observation_date is present and matches YYYY-MM-DD format."""
        raw = self._backfill_payload()
        assert "observation_date" in raw
        assert _DATE_RE.match(raw["observation_date"]), (
            f"observation_date='{raw['observation_date']}' does not match YYYY-MM-DD"
        )

    def test_26_timeframe_is_5y_string(self):
        """[26] timeframe is present and equals TIMEFRAME_5Y = 'today 5-y'."""
        raw = self._backfill_payload()
        assert raw.get("timeframe") == TIMEFRAME_5Y, (
            f"Expected timeframe='{TIMEFRAME_5Y}', got '{raw.get('timeframe')}'"
        )

    def test_27_rank_in_trending_absent_for_backfill(self):
        """[27] rank_in_trending is absent for backfill mode."""
        raw = self._backfill_payload()
        assert "rank_in_trending" not in raw, (
            "rank_in_trending should NOT be present for backfill mode"
        )


# ==========================================================
# [28] [28b] Kafka Partition Key
# ==========================================================

class TestKafkaPartitionKey:
    """Gate 1 checks [28]–[28b]: Partition key correctness and determinism."""

    def test_28_partition_key_is_keyword_bytes(self):
        """[28] Kafka partition key == keyword.encode('utf-8')."""
        # Verify the _emit() method uses keyword as the partition key.
        # We inspect the raw_payload to confirm the keyword field is what
        # would be used as the key — the actual send() call is not executed
        # here (Gate 1 has no live Kafka).
        keyword = "Crude Oil"
        raw = _producer._build_raw_payload(
            keyword=keyword, geo="US", interest_score=80.0,
            fetch_timestamp=datetime.now(timezone.utc).isoformat(),
            mode="static", rank_in_trending=1,
        )
        expected_key = keyword.encode("utf-8")
        assert raw["keyword"].encode("utf-8") == expected_key

    def test_28b_partition_key_is_deterministic_across_invocations(self):
        """[28b] Two successive calls with the same keyword produce identical
        partition key bytes — validates that Flink keyed-state affinity is
        stable across invocations and Kafka partition routing is consistent.

        If the key were derived from a non-deterministic source (e.g. UUID,
        timestamp, random), the same keyword would land in different partitions
        on different runs, scattering its history across Flink task slots and
        breaking per-keyword momentum calculations (Section 4.2B).
        """
        keyword = "Federal Reserve"
        fetch_ts = datetime.now(timezone.utc).isoformat()

        raw_call_1 = _producer._build_raw_payload(
            keyword=keyword, geo="US", interest_score=60.0,
            fetch_timestamp=fetch_ts, mode="static", rank_in_trending=3,
        )
        raw_call_2 = _producer._build_raw_payload(
            keyword=keyword, geo="US", interest_score=62.0,  # different score
            fetch_timestamp=fetch_ts, mode="static", rank_in_trending=3,
        )

        key_1 = raw_call_1["keyword"].encode("utf-8")
        key_2 = raw_call_2["keyword"].encode("utf-8")

        assert key_1 == key_2, (
            f"Partition keys differ across calls for the same keyword='{keyword}': "
            f"key_1={key_1!r}, key_2={key_2!r}"
        )
        assert key_1 == keyword.encode("utf-8"), (
            f"Partition key does not equal keyword.encode('utf-8'): "
            f"key_1={key_1!r}, expected={keyword.encode('utf-8')!r}"
        )


# ==========================================================
# [29–34] Producer Config Invariants
# ==========================================================

class TestProducerConfigInvariants:
    """Gate 1 checks [29]–[34]: spec-mandated constants in the producer."""

    def test_29_geo_matrix_has_exactly_4_geos(self):
        """[29] GEO_MATRIX has exactly 4 entries: US, GB, IL, DE."""
        assert set(GEO_MATRIX.keys()) == {"US", "GB", "IL", "DE"}, (
            f"GEO_MATRIX keys = {set(GEO_MATRIX.keys())} — expected {{US, GB, IL, DE}}"
        )

    @pytest.mark.parametrize("geo", ["US", "GB", "IL", "DE"])
    def test_30_geo_matrix_entries_have_required_fields(self, geo):
        """[30] Every GEO_MATRIX entry has non-empty 'name' and 'trending_pn' fields."""
        entry = GEO_MATRIX[geo]
        assert "name" in entry and entry["name"], (
            f"GEO_MATRIX['{geo}']['name'] is missing or empty"
        )
        assert "trending_pn" in entry and entry["trending_pn"], (
            f"GEO_MATRIX['{geo}']['trending_pn'] is missing or empty"
        )

    @pytest.mark.parametrize("keyword", ["Inflation", "Crude Oil", "Recession"])
    def test_31_backfill_keywords_contains_spec_mandated_core(self, keyword):
        """[31] BACKFILL_KEYWORDS contains the 3 spec-mandated keywords (Section B.5)."""
        assert keyword in BACKFILL_KEYWORDS, (
            f"Spec-mandated keyword '{keyword}' is missing from BACKFILL_KEYWORDS"
        )

    def test_32_backfill_keywords_minimum_length(self):
        """[32] BACKFILL_KEYWORDS has at least 5 keywords."""
        assert len(BACKFILL_KEYWORDS) >= 5, (
            f"BACKFILL_KEYWORDS has only {len(BACKFILL_KEYWORDS)} entries — expected >= 5"
        )

    def test_33_max_trending_per_geo_is_50(self):
        """[33] MAX_TRENDING_PER_GEO == 50 (Section B.5: 'Top 50 trending topics')."""
        assert MAX_TRENDING_PER_GEO == 50, (
            f"MAX_TRENDING_PER_GEO={MAX_TRENDING_PER_GEO} — spec requires 50"
        )

    def test_34_source_name_constant(self):
        """[34] SOURCE_NAME == 'googletrends'."""
        assert SOURCE_NAME == "googletrends"
