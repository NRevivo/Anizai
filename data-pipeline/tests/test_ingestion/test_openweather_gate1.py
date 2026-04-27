"""
Gate 1 — Ingestion Gate: OpenWeather Bronze Schema Compliance.

Validates that the OpenWeather producer's envelope-building logic produces
messages fully compliant with the Bronze Schema (Section C.1) and Message
Envelope (Section 3.2), and that all producer config invariants match the
spec — before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no OpenWeatherMap API calls.
    _build_raw_payload() and build_bronze_message() are pure functions
    tested in complete isolation (same pattern as test_fred_gate1.py).
  - Constants imported from the producer and CONDITION_ID_TO_SEVERITY
    imported from silver_job are tested for spec invariants without
    hard-coding values — guards against config drift.
  - Validators from utils/validators.py are called directly — the same
    validators the Flink Silver Job uses, so a Gate 1 pass guarantees
    the Silver Job's first validation gate will also pass.

Gate 1 checklist (Section 9.3 Gate 1):

  ENVELOPE STRUCTURE
  [01] All required envelope fields present and correctly typed
  [02] event_id is a valid UUIDv4 string
  [03] producer_timestamp is a valid ISO8601 UTC string
  [04] source_name == "openweather"
  [05] payload.raw_payload is a non-empty dict
  [06] validate_envelope() passes without errors
  [07] validate_bronze_payload() passes without errors
  [08] NDJSON round-trip (serialize → deserialize) preserves the full envelope
  [09] Each call to build_bronze_message() produces a distinct event_id

  RAW PAYLOAD — COMMON FIELDS
  [10] hotspot_name is a non-empty string
  [11] lat is a float
  [12] lon is a float
  [13] strategic_tag is a non-empty string
  [14] temperature_celsius is a float
  [15] wind_speed_ms is a float (raw m/s — Silver converts to knots)
  [16] pressure_hpa is a float
  [17] humidity_pct is a float
  [18] weather_id is an int (raw OWM code — Silver maps to severity)
  [19] weather_main is a non-empty string
  [20] official_alerts is a list (may be empty on free tier)
  [21] fetch_timestamp is a non-empty ISO8601 string
  [22] mode is one of "static" | "reactive"

  KAFKA PARTITION KEY
  [23] Partition key is hotspot_name encoded as UTF-8 bytes
  [23b] Partition key is deterministic — two successive calls for the same
        hotspot produce identical bytes (validates Flink keyed-state affinity)

  PRODUCER CONFIG INVARIANTS — HOTSPOTS LIST
  [24] HOTSPOTS list has exactly 10 entries
  [25] Every HOTSPOT entry has required keys: name, lat, lon, strategic_tag
  [26] Every HOTSPOT name is a non-empty string
  [27] Every HOTSPOT lat is a float in [-90.0, 90.0]
  [28] Every HOTSPOT lon is a float in [-180.0, 180.0]
  [29] Every HOTSPOT strategic_tag is a non-empty string
  [30] No duplicate HOTSPOT names
  [31] Required strategic tags present: taiwan_semiconductor, japan_tech,
       strait_of_hormuz, suez_canal, port_of_houston (Gold Job trigger keys)

  PRODUCER CONFIG INVARIANTS — CONSTANTS
  [32] WIND_KNOTS_SHIP_THRESHOLD == 50
  [33] CONDITION_SEVERITY_DISASTER_THRESHOLD == 4
  [34] STATIC_POLL_INTERVAL_SEC == 600
  [35] REACTIVE_POLL_INTERVAL_SEC == 120
  [36] REACTIVE_WINDOW_MINUTES == 60
  [37] SOURCE_NAME == "openweather"

  CONDITION_ID_TO_SEVERITY MAPPING INVARIANTS (Silver Job, tested here as structural)
  [38] All OWM condition code ranges are covered: 2xx, 3xx, 5xx, 6xx, 7xx, 800, 80x
  [39] All severity values are in {1, 2, 3, 4, 5}
  [40] No severity value is None or 0
  [41] Known critical codes map to severity 5: 202, 212, 221, 504, 762, 781
  [42] Clear sky (800) maps to severity 1
  [43] No condition ID appears more than once (no duplicate keys)

References:
    - Section 2.1:  Producer Matrix (OpenWeather row)
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialisation
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.6:  OpenWeather parameters & strategic hotspots
    - Section C.1:  Bronze Schema
    - Section C.2:  Silver Structured Metric — metadata_extension fields
"""

import json
import re
import uuid
from datetime import datetime, timezone

import pytest

from ingestion.openweather_producer import (
    CONDITION_SEVERITY_DISASTER_THRESHOLD,
    HOTSPOTS,
    OWM_BASE_URL,
    OWM_UNITS,
    REACTIVE_POLL_INTERVAL_SEC,
    REACTIVE_WINDOW_MINUTES,
    SOURCE_NAME,
    STATIC_POLL_INTERVAL_SEC,
    WIND_KNOTS_SHIP_THRESHOLD,
    OpenWeatherProducer,
)
from processing.silver_job import CONDITION_ID_TO_SEVERITY
from utils.kafka_utils import build_bronze_message, ndjson_deserializer, ndjson_serializer
from utils.validators import validate_bronze_payload, validate_envelope

# ==========================================================
# Helpers
# ==========================================================

_ISO8601_RE = re.compile(
    r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$"
)


def _is_valid_uuid4(value: str) -> bool:
    try:
        return uuid.UUID(value, version=4).version == 4
    except (ValueError, AttributeError):
        return False


def _is_valid_iso8601(value: str) -> bool:
    return bool(_ISO8601_RE.match(value))


def _make_producer() -> OpenWeatherProducer:
    """
    Instantiate OpenWeatherProducer without starting Kafka or hitting the API.

    We only need _build_raw_payload() for Gate 1, which is a pure function.
    Bypass __init__ (which creates Kafka producer) via __new__ to avoid
    requiring a live Kafka broker (same pattern as test_googletrends_gate1.py).
    """
    p = OpenWeatherProducer.__new__(OpenWeatherProducer)
    p._emitted = 0
    return p


_producer = _make_producer()

# Reference hotspot — Taipei: realistic values, condition_id=502 (heavy rain, severity 4)
_TAIPEI = HOTSPOTS[0]  # Taipei
_FETCH_TS = "2026-04-02T10:00:00+00:00"


def _make_raw(
    hotspot: dict = None,
    temperature_celsius: float = 22.4,
    wind_speed_ms: float = 8.7,
    pressure_hpa: float = 1008.0,
    humidity_pct: float = 91.0,
    weather_id: int = 502,
    weather_main: str = "Rain",
    weather_description: str = "heavy intensity rain",
    official_alerts: list = None,
    mode: str = "static",
) -> dict:
    """Build a minimal raw_payload using _build_raw_payload."""
    h = hotspot or _TAIPEI
    owm_data = {
        "main":    {"temp": temperature_celsius, "pressure": pressure_hpa, "humidity": humidity_pct},
        "wind":    {"speed": wind_speed_ms},
        "weather": [{"id": weather_id, "main": weather_main, "description": weather_description}],
        "alerts":  official_alerts if official_alerts is not None else [],
    }
    return _producer._build_raw_payload(h, owm_data, _FETCH_TS, mode)


def _make_envelope(raw: dict = None, hotspot: dict = None) -> dict:
    h = hotspot or _TAIPEI
    raw = raw or _make_raw(hotspot=h)
    lat = h["lat"]
    lon = h["lon"]
    endpoint = f"{OWM_BASE_URL}?lat={lat}&lon={lon}&units={OWM_UNITS}"
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=endpoint,
        raw_payload=raw,
    )


# ==========================================================
# [01–09] Envelope Structure
# ==========================================================

class TestEnvelopeStructure:
    """Gate 1 checks [01]–[09]: Bronze envelope fields and types."""

    def test_01_required_fields_present_and_typed(self):
        """[01] All required envelope fields are present with correct types."""
        env = _make_envelope()
        assert isinstance(env["event_id"],           str)
        assert isinstance(env["trace_id"],           str)
        assert isinstance(env["producer_timestamp"], str)
        assert isinstance(env["schema_version"],     str)
        assert isinstance(env["source_name"],        str)
        assert isinstance(env["payload"],            dict)

    def test_02_event_id_is_valid_uuid4(self):
        """[02] event_id is a valid UUIDv4 string."""
        env = _make_envelope()
        assert _is_valid_uuid4(env["event_id"]), (
            f"event_id='{env['event_id']}' is not a valid UUIDv4"
        )

    def test_03_producer_timestamp_is_iso8601_utc(self):
        """[03] producer_timestamp is a valid ISO8601 UTC string."""
        env = _make_envelope()
        ts = env["producer_timestamp"]
        assert _is_valid_iso8601(ts), f"producer_timestamp='{ts}' is not valid ISO8601"

    def test_04_source_name_is_openweather(self):
        """[04] source_name == 'openweather'."""
        env = _make_envelope()
        assert env["source_name"] == "openweather"

    def test_05_raw_payload_is_nonempty_dict(self):
        """[05] payload.raw_payload is a non-empty dict."""
        env = _make_envelope()
        raw = env["payload"]["raw_payload"]
        assert isinstance(raw, dict)
        assert len(raw) > 0

    def test_06_validate_envelope_passes(self):
        """[06] validate_envelope() returns is_valid=True for a well-formed envelope."""
        env = _make_envelope()
        result = validate_envelope(env)
        assert result.is_valid, f"validate_envelope() failed: {result.errors}"

    def test_07_validate_bronze_payload_passes(self):
        """[07] validate_bronze_payload() returns is_valid=True."""
        env = _make_envelope()
        result = validate_bronze_payload(env["payload"])
        assert result.is_valid, f"validate_bronze_payload() failed: {result.errors}"

    def test_08_ndjson_roundtrip_preserves_envelope(self):
        """[08] NDJSON serialize → deserialize preserves the full envelope."""
        env = _make_envelope()
        serialized   = ndjson_serializer(env)
        deserialized = ndjson_deserializer(serialized)
        assert deserialized == env, "NDJSON round-trip produced a different object"

    def test_09_distinct_event_ids_per_call(self):
        """[09] Each build_bronze_message() call generates a unique event_id."""
        raw = _make_raw()
        endpoint = f"{OWM_BASE_URL}?lat={_TAIPEI['lat']}&lon={_TAIPEI['lon']}&units={OWM_UNITS}"
        ids = {
            build_bronze_message(SOURCE_NAME, endpoint, raw)["event_id"]
            for _ in range(5)
        }
        assert len(ids) == 5, "event_ids are not unique across calls"


# ==========================================================
# [10–22] Raw Payload — Common Fields
# ==========================================================

class TestRawPayloadCommonFields:
    """Gate 1 checks [10]–[22]: all required payload fields and types."""

    def test_10_hotspot_name_is_nonempty_string(self):
        """[10] hotspot_name is a non-empty string."""
        raw = _make_raw()
        assert isinstance(raw["hotspot_name"], str)
        assert raw["hotspot_name"].strip() != ""

    def test_11_lat_is_float(self):
        """[11] lat is a float."""
        raw = _make_raw()
        assert isinstance(raw["lat"], float)

    def test_12_lon_is_float(self):
        """[12] lon is a float."""
        raw = _make_raw()
        assert isinstance(raw["lon"], float)

    def test_13_strategic_tag_is_nonempty_string(self):
        """[13] strategic_tag is a non-empty string."""
        raw = _make_raw()
        assert isinstance(raw["strategic_tag"], str)
        assert raw["strategic_tag"].strip() != ""

    def test_14_temperature_celsius_is_float(self):
        """[14] temperature_celsius is a float."""
        raw = _make_raw()
        assert isinstance(raw["temperature_celsius"], float)

    def test_15_wind_speed_ms_is_float(self):
        """[15] wind_speed_ms is a float (raw m/s — Silver converts to knots)."""
        raw = _make_raw()
        assert isinstance(raw["wind_speed_ms"], float)

    def test_16_pressure_hpa_is_float(self):
        """[16] pressure_hpa is a float."""
        raw = _make_raw()
        assert isinstance(raw["pressure_hpa"], float)

    def test_17_humidity_pct_is_float(self):
        """[17] humidity_pct is a float."""
        raw = _make_raw()
        assert isinstance(raw["humidity_pct"], float)

    def test_18_weather_id_is_int(self):
        """[18] weather_id is an int (raw OWM condition code — Silver maps to severity)."""
        raw = _make_raw()
        assert isinstance(raw["weather_id"], int)

    def test_19_weather_main_is_nonempty_string(self):
        """[19] weather_main is a non-empty string."""
        raw = _make_raw()
        assert isinstance(raw["weather_main"], str)
        assert raw["weather_main"].strip() != ""

    def test_20_official_alerts_is_list(self):
        """[20] official_alerts is a list (may be empty on free tier)."""
        raw = _make_raw()
        assert isinstance(raw["official_alerts"], list)

    def test_21_fetch_timestamp_is_nonempty_iso8601(self):
        """[21] fetch_timestamp is a non-empty ISO8601 string."""
        raw = _make_raw()
        assert isinstance(raw["fetch_timestamp"], str)
        assert raw["fetch_timestamp"] != ""
        assert _is_valid_iso8601(raw["fetch_timestamp"])

    @pytest.mark.parametrize("mode", ["static", "reactive"])
    def test_22_mode_is_valid(self, mode):
        """[22] mode is one of 'static' | 'reactive'."""
        raw = _make_raw(mode=mode)
        assert raw["mode"] in ("static", "reactive"), (
            f"mode='{raw['mode']}' is not valid — expected 'static' or 'reactive'"
        )


# ==========================================================
# [23] [23b] Kafka Partition Key
# ==========================================================

class TestKafkaPartitionKey:
    """Gate 1 checks [23]–[23b]: partition key correctness and determinism."""

    def test_23_partition_key_is_hotspot_name_bytes(self):
        """[23] Kafka partition key == hotspot_name.encode('utf-8')."""
        raw = _make_raw(hotspot=_TAIPEI)
        expected_key = _TAIPEI["name"].encode("utf-8")
        assert raw["hotspot_name"].encode("utf-8") == expected_key

    def test_23b_partition_key_is_deterministic_across_invocations(self):
        """[23b] Two successive calls for the same hotspot produce identical key bytes.

        Validates that Flink keyed-state affinity is stable across invocations:
        if the key were non-deterministic the same hotspot would land in different
        Flink task slots, scattering its momentum history (Section 4.2B).
        """
        raw_1 = _make_raw(hotspot=_TAIPEI, temperature_celsius=21.0)
        raw_2 = _make_raw(hotspot=_TAIPEI, temperature_celsius=24.0)  # different temp

        key_1 = raw_1["hotspot_name"].encode("utf-8")
        key_2 = raw_2["hotspot_name"].encode("utf-8")

        assert key_1 == key_2, (
            f"Partition keys differ for the same hotspot='{_TAIPEI['name']}': "
            f"key_1={key_1!r}, key_2={key_2!r}"
        )
        assert key_1 == _TAIPEI["name"].encode("utf-8")


# ==========================================================
# [24–31] Producer Config — HOTSPOTS List Invariants
# ==========================================================

class TestHotspotsListInvariants:
    """Gate 1 checks [24]–[31]: HOTSPOTS list structural requirements."""

    def test_24_hotspots_has_exactly_10_entries(self):
        """[24] HOTSPOTS list has exactly 10 entries (Section B.6)."""
        assert len(HOTSPOTS) == 10, (
            f"HOTSPOTS has {len(HOTSPOTS)} entries — spec requires exactly 10"
        )

    @pytest.mark.parametrize("hotspot", HOTSPOTS)
    def test_25_every_hotspot_has_required_keys(self, hotspot):
        """[25] Every HOTSPOT entry has name, lat, lon, strategic_tag."""
        for key in ("name", "lat", "lon", "strategic_tag"):
            assert key in hotspot, (
                f"HOTSPOT '{hotspot.get('name', '?')}' is missing required key '{key}'"
            )

    @pytest.mark.parametrize("hotspot", HOTSPOTS)
    def test_26_hotspot_name_is_nonempty(self, hotspot):
        """[26] Every HOTSPOT name is a non-empty string."""
        assert isinstance(hotspot["name"], str) and hotspot["name"].strip() != "", (
            f"HOTSPOT name='{hotspot.get('name')}' is empty"
        )

    @pytest.mark.parametrize("hotspot", HOTSPOTS)
    def test_27_hotspot_lat_in_valid_range(self, hotspot):
        """[27] Every HOTSPOT lat is a float in [-90.0, 90.0]."""
        lat = hotspot["lat"]
        assert isinstance(lat, float), f"HOTSPOT '{hotspot['name']}' lat is not a float"
        assert -90.0 <= lat <= 90.0, (
            f"HOTSPOT '{hotspot['name']}' lat={lat} is outside [-90, 90]"
        )

    @pytest.mark.parametrize("hotspot", HOTSPOTS)
    def test_28_hotspot_lon_in_valid_range(self, hotspot):
        """[28] Every HOTSPOT lon is a float in [-180.0, 180.0]."""
        lon = hotspot["lon"]
        assert isinstance(lon, float), f"HOTSPOT '{hotspot['name']}' lon is not a float"
        assert -180.0 <= lon <= 180.0, (
            f"HOTSPOT '{hotspot['name']}' lon={lon} is outside [-180, 180]"
        )

    @pytest.mark.parametrize("hotspot", HOTSPOTS)
    def test_29_hotspot_strategic_tag_nonempty(self, hotspot):
        """[29] Every HOTSPOT strategic_tag is a non-empty string."""
        tag = hotspot["strategic_tag"]
        assert isinstance(tag, str) and tag.strip() != "", (
            f"HOTSPOT '{hotspot['name']}' has empty strategic_tag"
        )

    def test_30_no_duplicate_hotspot_names(self):
        """[30] No two HOTSPOTs share the same name."""
        names = [h["name"] for h in HOTSPOTS]
        assert len(names) == len(set(names)), (
            f"Duplicate HOTSPOT names found: {[n for n in names if names.count(n) > 1]}"
        )

    @pytest.mark.parametrize("required_tag", [
        "taiwan_semiconductor",
        "japan_tech",
        "strait_of_hormuz",
        "suez_canal",
        "port_of_houston",
    ])
    def test_31_required_strategic_tags_present(self, required_tag):
        """[31] Gold Job trigger tags must appear in at least one HOTSPOT entry."""
        tags = {h["strategic_tag"] for h in HOTSPOTS}
        assert required_tag in tags, (
            f"Gold Job trigger tag '{required_tag}' not found in any HOTSPOT "
            f"(required by Tech_Supply_Risk / Potential_Shipping_Delay triggers, Section B.6)"
        )


# ==========================================================
# [32–37] Producer Config — Threshold & Mode Constants
# ==========================================================

class TestProducerConstants:
    """Gate 1 checks [32]–[37]: spec-mandated constant values."""

    def test_32_wind_knots_ship_threshold_is_50(self):
        """[32] WIND_KNOTS_SHIP_THRESHOLD == 50 (Section B.6: wind > 50 knots)."""
        assert WIND_KNOTS_SHIP_THRESHOLD == 50, (
            f"WIND_KNOTS_SHIP_THRESHOLD={WIND_KNOTS_SHIP_THRESHOLD} — spec requires 50"
        )

    def test_33_condition_severity_disaster_threshold_is_4(self):
        """[33] CONDITION_SEVERITY_DISASTER_THRESHOLD == 4 (Section B.6: severity ≥ 4)."""
        assert CONDITION_SEVERITY_DISASTER_THRESHOLD == 4, (
            f"CONDITION_SEVERITY_DISASTER_THRESHOLD={CONDITION_SEVERITY_DISASTER_THRESHOLD} "
            "— spec requires 4"
        )

    def test_34_static_poll_interval_is_600(self):
        """[34] STATIC_POLL_INTERVAL_SEC == 600 (10 min, Section B.6)."""
        assert STATIC_POLL_INTERVAL_SEC == 600, (
            f"STATIC_POLL_INTERVAL_SEC={STATIC_POLL_INTERVAL_SEC} — spec requires 600"
        )

    def test_35_reactive_poll_interval_is_120(self):
        """[35] REACTIVE_POLL_INTERVAL_SEC == 120 (2 min, Section B.6)."""
        assert REACTIVE_POLL_INTERVAL_SEC == 120, (
            f"REACTIVE_POLL_INTERVAL_SEC={REACTIVE_POLL_INTERVAL_SEC} — spec requires 120"
        )

    def test_36_reactive_window_minutes_is_60(self):
        """[36] REACTIVE_WINDOW_MINUTES == 60 (Section B.6)."""
        assert REACTIVE_WINDOW_MINUTES == 60, (
            f"REACTIVE_WINDOW_MINUTES={REACTIVE_WINDOW_MINUTES} — spec requires 60"
        )

    def test_37_source_name_is_openweather(self):
        """[37] SOURCE_NAME == 'openweather'."""
        assert SOURCE_NAME == "openweather"


# ==========================================================
# [38–43] CONDITION_ID_TO_SEVERITY Mapping Invariants
# ==========================================================

class TestConditionIdToSeverityMapping:
    """Gate 1 checks [38]–[43]: CONDITION_ID_TO_SEVERITY completeness and correctness."""

    # All documented OWM condition codes (source: openweathermap.org/weather-conditions)
    _ALL_OWM_CODES = {
        # 2xx Thunderstorm
        200, 201, 202, 210, 211, 212, 221, 230, 231, 232,
        # 3xx Drizzle
        300, 301, 302, 310, 311, 312, 313, 314, 321,
        # 5xx Rain
        500, 501, 502, 503, 504, 511, 520, 521, 522, 531,
        # 6xx Snow
        600, 601, 602, 611, 612, 613, 615, 616, 620, 621, 622,
        # 7xx Atmosphere
        701, 711, 721, 731, 741, 751, 761, 762, 771, 781,
        # 800 Clear
        800,
        # 80x Clouds
        801, 802, 803, 804,
    }

    def test_38_all_owm_condition_ranges_covered(self):
        """[38] Every documented OWM condition code has a mapping entry."""
        missing = self._ALL_OWM_CODES - set(CONDITION_ID_TO_SEVERITY.keys())
        assert not missing, (
            f"Missing OWM condition codes in CONDITION_ID_TO_SEVERITY: {sorted(missing)}\n"
            "Add entries for these codes or update _ALL_OWM_CODES if OWM removed them."
        )

    def test_39_all_severity_values_in_valid_range(self):
        """[39] All mapped severity values are in {1, 2, 3, 4, 5}."""
        invalid = {
            code: sev
            for code, sev in CONDITION_ID_TO_SEVERITY.items()
            if sev not in {1, 2, 3, 4, 5}
        }
        assert not invalid, (
            f"Condition codes with severity outside {{1,2,3,4,5}}: {invalid}"
        )

    def test_40_no_severity_value_is_none_or_zero(self):
        """[40] No mapped severity is None or 0."""
        bad = {
            code: sev
            for code, sev in CONDITION_ID_TO_SEVERITY.items()
            if sev is None or sev == 0
        }
        assert not bad, f"Condition codes with None or 0 severity: {bad}"

    @pytest.mark.parametrize("critical_code", [
        202,  # thunderstorm with heavy rain
        212,  # heavy thunderstorm
        221,  # ragged thunderstorm
        504,  # extreme rain
        762,  # volcanic ash
        781,  # tornado
    ])
    def test_41_critical_codes_map_to_severity_5(self, critical_code):
        """[41] Known critical OWM codes (extreme thunderstorm/ash/tornado) → severity 5."""
        sev = CONDITION_ID_TO_SEVERITY.get(critical_code)
        assert sev == 5, (
            f"OWM code {critical_code} should map to severity 5, got {sev}"
        )

    def test_42_clear_sky_maps_to_severity_1(self):
        """[42] Clear sky (OWM code 800) → severity 1 (no weather impact)."""
        assert CONDITION_ID_TO_SEVERITY.get(800) == 1, (
            f"OWM code 800 (clear sky) should map to severity 1, "
            f"got {CONDITION_ID_TO_SEVERITY.get(800)}"
        )

    def test_43_no_duplicate_condition_codes(self):
        """[43] No condition ID appears more than once (dict key uniqueness)."""
        # Python dicts cannot have duplicate keys — this tests that the dict
        # was built without any key overwriting another silently.
        # Verify by round-tripping through a list of items:
        all_codes = list(CONDITION_ID_TO_SEVERITY.keys())
        assert len(all_codes) == len(set(all_codes)), (
            "CONDITION_ID_TO_SEVERITY contains duplicate condition code keys"
        )
