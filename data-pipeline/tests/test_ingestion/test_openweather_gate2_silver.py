"""
Gate 2 (Silver) — Logic Gate: OpenWeather Silver Processing Validation.

Validates that the Silver Job's OpenWeather branch transforms and routes
correctly using mock payloads — no live Flink cluster, no Kafka, no OWM API.

What Gate 2 Silver covers (Section 9.3 Gate 2):

  ROUTING — HAPPY PATHS
  [01] Valid static mode payload       → SILVER_STRUCTURED_METRICS
  [02] Valid reactive mode payload     → SILVER_STRUCTURED_METRICS

  ROUTING — DLQ PATHS
  [03] Missing envelope fields         → DEAD_LETTER_QUEUE
  [04] Invalid bronze payload          → DEAD_LETTER_QUEUE
  [05] Missing lat in raw_payload      → DEAD_LETTER_QUEUE
  [06] Missing lon in raw_payload      → DEAD_LETTER_QUEUE
  [07] Missing strategic_tag           → DEAD_LETTER_QUEUE
  [08] Empty strategic_tag             → DEAD_LETTER_QUEUE
  [09] Missing wind_speed_ms           → DEAD_LETTER_QUEUE
  [10] Non-numeric wind_speed_ms       → DEAD_LETTER_QUEUE
  [11] weather_id not in CONDITION_ID_TO_SEVERITY  → DEAD_LETTER_QUEUE
  [12] DLQ record carries source_topic == BRONZE_OPENWEATHER
  [13] DLQ record carries original_message intact for replay

  SILVER SCHEMA COMPLIANCE
  [14] Silver output passes validate_silver_structured_metric()
  [15] schema_version == "2.0", layer == "Silver", entity_type == "Structured_Metric"
  [16] core_identity.source_name == "openweather"
  [17] core_identity.external_reference_id == hotspot_name
  [18] core_identity.metric_id is a valid UUID
  [19] core_identity.canonical_event_id == Bronze envelope event_id
  [20] data_point.current_value == temperature_celsius
  [21] data_point.unit == "celsius"
  [22] data_point.status == "static" for static mode
  [23] data_point.status == "reactive" for reactive mode

  WIND CONVERSION & SEVERITY MAPPING
  [24] wind_speed_knots == round(wind_speed_ms × 1.94384, 4) (Silver converts m/s → knots)
  [25] condition_severity == CONDITION_ID_TO_SEVERITY[weather_id] (502 → 4)

  METADATA EXTENSION FIELDS
  [26] wind_speed_knots present in metadata_extension
  [27] pressure_hpa present
  [28] humidity_pct present
  [29] condition_code == raw weather_id
  [30] condition_severity present
  [31] strategic_tag present
  [32] coordinates dict with lat and lon
  [33] official_alerts list present

  MOMENTUM BLOCK STUBS
  [34] momentum_block.change_24h == 0.0 (Gold Job computes real deltas, Section 4.2B)
  [35] momentum_block.change_7d  == 0.0
  [36] momentum_block.change_30d == 0.0
  [37] momentum_block.is_new_market == False

  END-TO-END MOCK
  [38] Mock payload from openweather_bronze_payload.json round-trips correctly

Test strategy:
    Fixtures build full Bronze envelopes via build_bronze_message() (same as
    the producer), then pass directly to process_openweather_message() — the
    pure dispatch function in silver_job.py. No Flink, no Kafka, no OWM API.

References:
    - Section 4.1:  Silver Job specification
    - Section 3.3:  Service Isolation — wind conversion and severity mapping are Silver responsibilities
    - Section 3.5:  Dead-Letter Queue routing — never silently drop
    - Section 4.2B: Gold Job keyed state (why momentum stubs are 0.0)
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section B.6:  OpenWeather parameters and condition severity mapping
    - Section C.2:  Silver Structured Metric schema
"""

import copy
import json
import uuid
from pathlib import Path

import pytest

from config.kafka_topics import (
    BRONZE_OPENWEATHER,
    DEAD_LETTER_QUEUE,
    SILVER_STRUCTURED_METRICS,
)
from ingestion.openweather_producer import SOURCE_NAME, OWM_BASE_URL
from processing.silver_job import (
    CONDITION_ID_TO_SEVERITY,
    _MS_TO_KNOTS,
    map_openweather_to_silver,
    process_openweather_message,
)
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_structured_metric

# ==========================================================
# Paths & Constants
# ==========================================================

MOCKS_DIR = Path(__file__).parent.parent / "mocks"

# Representative Taipei hotspot — matches openweather_bronze_payload.json
_LAT  = 25.033
_LON  = 121.5654
_BRONZE_ENDPOINT = f"{OWM_BASE_URL}?lat={_LAT}&lon={_LON}&units=metric"

# Known OWM condition code with a defined severity
_WEATHER_ID_502 = 502   # heavy intensity rain → severity 4


# ==========================================================
# Helpers
# ==========================================================

def _make_envelope(raw_payload: dict) -> dict:
    """Wrap raw_payload in a full Bronze envelope (same shape as the producer)."""
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=_BRONZE_ENDPOINT,
        raw_payload=raw_payload,
    )


def _static_raw(
    hotspot_name: str = "Taipei",
    lat: float = _LAT,
    lon: float = _LON,
    strategic_tag: str = "taiwan_semiconductor",
    temperature_celsius: float = 22.4,
    wind_speed_ms: float = 8.7,
    pressure_hpa: float = 1008.0,
    humidity_pct: float = 91.0,
    weather_id: int = _WEATHER_ID_502,
    weather_main: str = "Rain",
    weather_description: str = "heavy intensity rain",
    official_alerts=None,
    fetch_timestamp: str = "2026-04-02T10:00:00+00:00",
) -> dict:
    """Build a static mode raw_payload matching the producer's _build_raw_payload() shape."""
    return {
        "hotspot_name":       hotspot_name,
        "lat":                lat,
        "lon":                lon,
        "strategic_tag":      strategic_tag,
        "temperature_celsius": temperature_celsius,
        "wind_speed_ms":      wind_speed_ms,
        "pressure_hpa":       pressure_hpa,
        "humidity_pct":       humidity_pct,
        "weather_id":         weather_id,
        "weather_main":       weather_main,
        "weather_description": weather_description,
        "official_alerts":    official_alerts if official_alerts is not None else [],
        "fetch_timestamp":    fetch_timestamp,
        "mode":               "static",
    }


def _reactive_raw(**overrides) -> dict:
    """Build a reactive mode raw_payload."""
    raw = _static_raw(**{k: v for k, v in overrides.items() if k != "mode"})
    raw["mode"] = "reactive"
    return raw


# ==========================================================
# [01–02] Routing — Happy Paths
# ==========================================================

class TestRoutingHappyPaths:
    """Gate 2 Silver checks [01]–[02]: valid payloads → SILVER_STRUCTURED_METRICS."""

    def test_01_static_routes_to_silver_structured_metrics(self):
        """[01] Valid static mode → SILVER_STRUCTURED_METRICS."""
        envelope = _make_envelope(_static_raw())
        topic, record = process_openweather_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record is not None

    def test_02_reactive_routes_to_silver_structured_metrics(self):
        """[02] Valid reactive mode → SILVER_STRUCTURED_METRICS."""
        envelope = _make_envelope(_reactive_raw())
        topic, record = process_openweather_message(envelope)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record is not None


# ==========================================================
# [03–13] Routing — DLQ Paths
# ==========================================================

class TestRoutingDLQPaths:
    """Gate 2 Silver checks [03]–[13]: invalid payloads → DEAD_LETTER_QUEUE."""

    def test_03_missing_envelope_fields_routes_to_dlq(self):
        """[03] Envelope missing required fields → DEAD_LETTER_QUEUE."""
        bad_envelope = {"event_id": str(uuid.uuid4())}  # stripped-down, invalid
        topic, record = process_openweather_message(bad_envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_04_invalid_bronze_payload_routes_to_dlq(self):
        """[04] Payload dict missing Bronze schema fields → DEAD_LETTER_QUEUE."""
        envelope = _make_envelope(_static_raw())
        envelope["payload"] = {"not_a_valid_payload": True}
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_05_missing_lat_routes_to_dlq(self):
        """[05] raw_payload missing lat → DEAD_LETTER_QUEUE (coordinates are canonical identity)."""
        raw = _static_raw()
        del raw["lat"]
        envelope = _make_envelope(raw)
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_06_missing_lon_routes_to_dlq(self):
        """[06] raw_payload missing lon → DEAD_LETTER_QUEUE."""
        raw = _static_raw()
        del raw["lon"]
        envelope = _make_envelope(raw)
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_07_missing_strategic_tag_routes_to_dlq(self):
        """[07] raw_payload missing strategic_tag → DEAD_LETTER_QUEUE (Gold triggers key on tag)."""
        raw = _static_raw()
        del raw["strategic_tag"]
        envelope = _make_envelope(raw)
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_08_empty_strategic_tag_routes_to_dlq(self):
        """[08] raw_payload.strategic_tag == '' → DEAD_LETTER_QUEUE."""
        raw = _static_raw(strategic_tag="")
        envelope = _make_envelope(raw)
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_09_missing_wind_speed_ms_routes_to_dlq(self):
        """[09] raw_payload missing wind_speed_ms → DEAD_LETTER_QUEUE (Shipping_Delay trigger input)."""
        raw = _static_raw()
        del raw["wind_speed_ms"]
        envelope = _make_envelope(raw)
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_10_non_numeric_wind_speed_ms_routes_to_dlq(self):
        """[10] wind_speed_ms is a non-numeric string → DEAD_LETTER_QUEUE."""
        raw = _static_raw()
        raw["wind_speed_ms"] = "strong"
        envelope = _make_envelope(raw)
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_11_unknown_weather_id_routes_to_dlq(self):
        """[11] weather_id not in CONDITION_ID_TO_SEVERITY → DEAD_LETTER_QUEUE.

        weather_id=9999 is not a documented OWM condition code.
        Gate 1 (TestConditionIdToSeverityMapping) verified all 61 documented
        codes are covered — an unknown ID signals either OWM added a new code
        or the producer sent corrupt data. Never silently default (Section 3.5).
        """
        raw = _static_raw(weather_id=9999)
        envelope = _make_envelope(raw)
        topic, record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE

    def test_12_dlq_record_carries_bronze_openweather_source_topic(self):
        """[12] DLQ record source_topic == BRONZE_OPENWEATHER, not FRED or POLYMARKET."""
        raw = _static_raw(weather_id=9999)  # invalid → DLQ
        envelope = _make_envelope(raw)
        topic, dlq_record = process_openweather_message(envelope)
        assert topic == DEAD_LETTER_QUEUE
        assert dlq_record["source_topic"] == BRONZE_OPENWEATHER

    def test_13_dlq_record_carries_original_message_intact(self):
        """[13] DLQ record original_message == the original envelope for replay."""
        raw = _static_raw(strategic_tag="")  # empty tag → DLQ
        envelope = _make_envelope(raw)
        _, dlq_record = process_openweather_message(envelope)
        assert dlq_record["original_message"] == envelope


# ==========================================================
# [14–23] Silver Schema Compliance
# ==========================================================

class TestSilverSchemaCompliance:
    """Gate 2 Silver checks [14]–[23]: schema structure and key field values."""

    def _process_static(self) -> dict:
        envelope = _make_envelope(_static_raw())
        _, silver = process_openweather_message(envelope)
        return silver

    def test_14_validate_silver_structured_metric_passes(self):
        """[14] Silver output passes validate_silver_structured_metric()."""
        silver = self._process_static()
        result = validate_silver_structured_metric(silver)
        assert result.is_valid, f"validate_silver_structured_metric() failed: {result.errors}"

    def test_15_top_level_schema_fields(self):
        """[15] schema_version == '2.0', layer == 'Silver', entity_type == 'Structured_Metric'."""
        silver = self._process_static()
        assert silver["schema_version"] == "2.0"
        assert silver["layer"] == "Silver"
        assert silver["entity_type"] == "Structured_Metric"

    def test_16_source_name_is_openweather(self):
        """[16] core_identity.source_name == 'openweather'."""
        silver = self._process_static()
        assert silver["core_identity"]["source_name"] == "openweather"

    def test_17_external_reference_id_equals_hotspot_name(self):
        """[17] core_identity.external_reference_id == hotspot_name (Gold keyed-state key)."""
        hotspot = "Hsinchu_Science_Park"
        raw = _static_raw(hotspot_name=hotspot)
        envelope = _make_envelope(raw)
        _, silver = process_openweather_message(envelope)
        assert silver["core_identity"]["external_reference_id"] == hotspot

    def test_18_metric_id_is_valid_uuid(self):
        """[18] core_identity.metric_id is a valid UUID string."""
        silver = self._process_static()
        metric_id = silver["core_identity"]["metric_id"]
        try:
            uuid.UUID(metric_id)
        except ValueError:
            pytest.fail(f"metric_id='{metric_id}' is not a valid UUID")

    def test_19_canonical_event_id_matches_envelope_event_id(self):
        """[19] core_identity.canonical_event_id == Bronze envelope event_id."""
        envelope = _make_envelope(_static_raw())
        _, silver = process_openweather_message(envelope)
        assert silver["core_identity"]["canonical_event_id"] == envelope["event_id"]

    def test_20_current_value_equals_temperature_celsius(self):
        """[20] data_point.current_value == temperature_celsius (not wind or humidity)."""
        temp = 18.5
        raw = _static_raw(temperature_celsius=temp)
        envelope = _make_envelope(raw)
        _, silver = process_openweather_message(envelope)
        assert silver["data_point"]["current_value"] == pytest.approx(temp)

    def test_21_unit_is_celsius(self):
        """[21] data_point.unit == 'celsius'."""
        silver = self._process_static()
        assert silver["data_point"]["unit"] == "celsius"

    def test_22_status_is_static_for_static_mode(self):
        """[22] data_point.status == 'static' for static mode."""
        silver = self._process_static()
        assert silver["data_point"]["status"] == "static"

    def test_23_status_is_reactive_for_reactive_mode(self):
        """[23] data_point.status == 'reactive' for reactive mode."""
        envelope = _make_envelope(_reactive_raw())
        _, silver = process_openweather_message(envelope)
        assert silver["data_point"]["status"] == "reactive"


# ==========================================================
# [24–25] Wind Conversion & Severity Mapping
# ==========================================================

class TestWindConversionAndSeverityMapping:
    """Gate 2 Silver checks [24]–[25]: Silver-layer transformations (Section 3.3)."""

    def test_24_wind_speed_knots_converted_from_ms(self):
        """[24] wind_speed_knots == round(wind_speed_ms × 1.94384, 4).

        The producer emits raw m/s (OWM API units). Silver is responsible for
        converting to knots — keeping the producer free of domain logic (§3.3).
        The Gold trigger uses knots (threshold 50 kt), so precision matters.
        """
        wind_ms = 8.7
        expected_knots = round(wind_ms * _MS_TO_KNOTS, 4)  # 16.9114
        raw = _static_raw(wind_speed_ms=wind_ms)
        envelope = _make_envelope(raw)
        _, silver = process_openweather_message(envelope)
        actual_knots = silver["metadata_extension"]["wind_speed_knots"]
        assert actual_knots == pytest.approx(expected_knots, abs=1e-4), (
            f"Expected wind_speed_knots={expected_knots}, got {actual_knots}"
        )

    def test_25_condition_severity_mapped_from_weather_id(self):
        """[25] condition_severity == CONDITION_ID_TO_SEVERITY[weather_id].

        weather_id=502 (heavy intensity rain) → severity 4.
        Silver maps the raw OWM condition code to the 1–5 severity scale —
        the Gold trigger checks condition_severity, not weather_id (§3.3).
        """
        weather_id = _WEATHER_ID_502
        expected_severity = CONDITION_ID_TO_SEVERITY[weather_id]  # 4
        raw = _static_raw(weather_id=weather_id)
        envelope = _make_envelope(raw)
        _, silver = process_openweather_message(envelope)
        assert silver["metadata_extension"]["condition_severity"] == expected_severity


# ==========================================================
# [26–33] Metadata Extension Fields
# ==========================================================

class TestMetadataExtensionFields:
    """Gate 2 Silver checks [26]–[33]: all metadata_extension fields present and typed."""

    def _process_static(self) -> dict:
        envelope = _make_envelope(_static_raw())
        _, silver = process_openweather_message(envelope)
        return silver

    def test_26_wind_speed_knots_present(self):
        """[26] metadata_extension.wind_speed_knots is present and numeric."""
        meta = self._process_static()["metadata_extension"]
        assert "wind_speed_knots" in meta
        assert isinstance(meta["wind_speed_knots"], (int, float))

    def test_27_pressure_hpa_present(self):
        """[27] metadata_extension.pressure_hpa is present and numeric."""
        meta = self._process_static()["metadata_extension"]
        assert "pressure_hpa" in meta
        assert isinstance(meta["pressure_hpa"], (int, float))

    def test_28_humidity_pct_present(self):
        """[28] metadata_extension.humidity_pct is present and numeric."""
        meta = self._process_static()["metadata_extension"]
        assert "humidity_pct" in meta
        assert isinstance(meta["humidity_pct"], (int, float))

    def test_29_condition_code_equals_raw_weather_id(self):
        """[29] metadata_extension.condition_code == raw weather_id (producer value preserved)."""
        raw = _static_raw(weather_id=_WEATHER_ID_502)
        envelope = _make_envelope(raw)
        _, silver = process_openweather_message(envelope)
        assert silver["metadata_extension"]["condition_code"] == _WEATHER_ID_502

    def test_30_condition_severity_present(self):
        """[30] metadata_extension.condition_severity is present and in 1–5."""
        meta = self._process_static()["metadata_extension"]
        assert "condition_severity" in meta
        assert meta["condition_severity"] in {1, 2, 3, 4, 5}

    def test_31_strategic_tag_present(self):
        """[31] metadata_extension.strategic_tag == the hotspot's strategic_tag."""
        raw = _static_raw(strategic_tag="taiwan_semiconductor")
        envelope = _make_envelope(raw)
        _, silver = process_openweather_message(envelope)
        assert silver["metadata_extension"]["strategic_tag"] == "taiwan_semiconductor"

    def test_32_coordinates_dict_with_lat_and_lon(self):
        """[32] metadata_extension.coordinates == {"lat": lat, "lon": lon}."""
        raw = _static_raw(lat=_LAT, lon=_LON)
        envelope = _make_envelope(raw)
        _, silver = process_openweather_message(envelope)
        coords = silver["metadata_extension"]["coordinates"]
        assert isinstance(coords, dict), "coordinates must be a dict"
        assert coords["lat"] == pytest.approx(_LAT)
        assert coords["lon"] == pytest.approx(_LON)

    def test_33_official_alerts_list_present(self):
        """[33] metadata_extension.official_alerts is a list (empty if no OWM alerts)."""
        meta = self._process_static()["metadata_extension"]
        assert "official_alerts" in meta
        assert isinstance(meta["official_alerts"], list)


# ==========================================================
# [34–37] Momentum Block Stubs
# ==========================================================

class TestMomentumBlockStubs:
    """Gate 2 Silver checks [34]–[37]: all momentum stubs == 0.0 at Silver layer."""

    def test_34_to_37_momentum_stubs_are_zero(self):
        """[34-37] change_24h/7d/30d == 0.0, is_new_market == False at Silver.

        Gold Job computes actual deltas from Flink keyed state (Section 4.2B).
        Silver stubs prevent accidental use of zero deltas as real values.
        """
        envelope = _make_envelope(_static_raw())
        _, silver = process_openweather_message(envelope)
        mb = silver["momentum_block"]
        assert mb["change_24h"]    == 0.0,  "change_24h stub must be 0.0"
        assert mb["change_7d"]     == 0.0,  "change_7d stub must be 0.0"
        assert mb["change_30d"]    == 0.0,  "change_30d stub must be 0.0"
        assert mb["is_new_market"] is False, "is_new_market stub must be False"


# ==========================================================
# [38] End-to-End Mock Round-Trip
# ==========================================================

class TestMockRoundTrip:
    """Gate 2 Silver check [38]: mock payload from file processes end-to-end."""

    def test_38_mock_payload_processes_correctly(self):
        """[38] openweather_bronze_payload.json can be processed without DLQ routing.

        The mock represents Taipei at 2026-04-02T10:00:00+00:00:
            weather_id=502 → condition_severity=4
            wind_speed_ms=8.7 → wind_speed_knots≈16.9114
            temperature_celsius=22.4 → current_value=22.4
        All these invariants are verified to confirm full-stack correctness.
        """
        mock_path = MOCKS_DIR / "openweather_bronze_payload.json"
        raw = json.loads(mock_path.read_text())
        raw.pop("_comment", None)

        envelope = _make_envelope(raw)
        topic, silver = process_openweather_message(envelope)

        assert topic == SILVER_STRUCTURED_METRICS, (
            f"Mock payload routed to '{topic}' instead of SILVER_STRUCTURED_METRICS"
        )
        result = validate_silver_structured_metric(silver)
        assert result.is_valid, f"Mock Silver output invalid: {result.errors}"

        # Key field assertions from Section C.1 / Section B.6
        assert silver["core_identity"]["source_name"] == "openweather"
        assert silver["core_identity"]["external_reference_id"] == "Taipei"
        assert silver["data_point"]["current_value"] == pytest.approx(22.4)
        assert silver["data_point"]["unit"] == "celsius"

        meta = silver["metadata_extension"]
        assert meta["condition_severity"] == 4,  "weather_id=502 must map to severity 4"
        assert meta["wind_speed_knots"] == pytest.approx(round(8.7 * _MS_TO_KNOTS, 4), abs=1e-4)
        assert meta["strategic_tag"] == "taiwan_semiconductor"
        assert meta["condition_code"] == 502
