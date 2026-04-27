"""
Gate 2 (Silver) — Logic Gate: OpenSky Silver Processing Validation.

Validates that the Silver Job's OpenSky branch transforms and routes
correctly using mock payloads — no live Flink cluster, no Kafka, no OpenSky API.

What Gate 2 Silver covers (Section 9.3 Gate 2):

  ROUTING — HAPPY PATHS
  [01] Valid static mode payload        → SILVER_STRUCTURED_METRICS
  [02] Valid reactive mode payload      → SILVER_STRUCTURED_METRICS
  [03] aircraft_count == 0 (empty box) → SILVER_STRUCTURED_METRICS (not DLQ)

  ROUTING — DLQ PATHS (6 guards from design decision 2026-04-04)
  [04] Missing envelope fields          → DEAD_LETTER_QUEUE
  [05] Invalid bronze payload           → DEAD_LETTER_QUEUE
  [06] Missing bounding_box_id          → DEAD_LETTER_QUEUE
  [07] Empty bounding_box_id            → DEAD_LETTER_QUEUE
  [08] Missing aircraft_count           → DEAD_LETTER_QUEUE
  [09] Non-integer aircraft_count       → DEAD_LETTER_QUEUE
  [10] Missing lat_min                  → DEAD_LETTER_QUEUE
  [11] Missing lon_max                  → DEAD_LETTER_QUEUE
  [12] DLQ record carries source_topic == BRONZE_OPENSKY
  [13] DLQ record carries original_message intact for replay

  TRANSPONDER SILENCE DOMAIN FILTER
  [14] Aircraft where lat=None AND lon=None AND on_ground=False → counted in transponder_silence_events
  [15] Aircraft where lat=None AND lon=None AND on_ground=True  → NOT counted (on ground)
  [16] Aircraft where lat is not None                          → NOT counted (has position)
  [17] Empty states_compact → transponder_silence_events == 0
  [18] aircraft_count=0 with empty states_compact → silence_events == 0

  SILVER SCHEMA COMPLIANCE
  [19] Silver output passes validate_silver_structured_metric()
  [20] schema_version == "2.0", layer == "Silver", entity_type == "Structured_Metric"
  [21] core_identity.source_name == "opensky"
  [22] core_identity.external_reference_id == bounding_box_id
  [23] core_identity.metric_id is a valid UUID
  [24] core_identity.canonical_event_id == Bronze envelope event_id
  [25] data_point.current_value == float(aircraft_count)
  [26] data_point.unit == "aircraft_count"
  [27] data_point.status == "static" for static mode
  [28] data_point.status == "reactive" for reactive mode

  METADATA EXTENSION FIELDS
  [29] bounding_box_id present in metadata_extension
  [30] aircraft_density_count == aircraft_count
  [31] transponder_silence_events is a non-negative integer
  [32] anomaly_score == 0.0 (stub — Gold fills in after momentum, Section 4.2B)
  [33] strategic_tag present
  [34] bounding_box_coords dict with lat_min, lat_max, lon_min, lon_max

  MOMENTUM BLOCK STUBS
  [35] momentum_block.change_24h == 0.0 (Gold Job computes real deltas, Section 4.2B)
  [36] momentum_block.change_7d  == 0.0
  [37] momentum_block.change_30d == 0.0
  [38] momentum_block.is_new_market == False

  END-TO-END MOCK
  [39] Mock payload from opensky_bronze_payload.json round-trips correctly:
       aircraft_count=47, transponder_silence_events=1 (only abc123 qualifies),
       anomaly_score=0.0

Test strategy:
    Fixtures build full Bronze envelopes via build_bronze_message() (same as
    the producer), then pass directly to process_opensky_message() — the pure
    dispatch function in silver_job.py. No Flink, no Kafka, no OpenSky API.

References:
    - Section 4.1:  Silver Job specification
    - Section 3.3:  Service Isolation — transponder domain filter is Silver's responsibility
    - Section 3.5:  Dead-Letter Queue routing — never silently drop
    - Section 4.2B: Gold Job keyed state (why momentum stubs are 0.0)
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 2
    - Section B.7:  OpenSky parameters and transponder monitoring
    - Section C.2:  Silver Structured Metric schema
"""

import copy
import json
import uuid
from pathlib import Path

import pytest

from config.kafka_topics import (
    BRONZE_OPENSKY,
    DEAD_LETTER_QUEUE,
    SILVER_STRUCTURED_METRICS,
)
from ingestion.opensky_producer import (
    BOUNDING_BOXES,
    OPENSKY_BASE_URL,
    SOURCE_NAME,
)
from processing.silver_job import (
    map_opensky_to_silver,
    process_opensky_message,
)
from utils.kafka_utils import build_bronze_message
from utils.validators import validate_silver_structured_metric

# ==========================================================
# Paths & Constants
# ==========================================================

_MOCKS_DIR   = Path(__file__).parent.parent / "mocks"
_MOCK_FILE   = _MOCKS_DIR / "opensky_bronze_payload.json"

# Reference box — Taiwan Strait
_BOX = BOUNDING_BOXES[1]  # taiwan_strait


# ==========================================================
# Helpers
# ==========================================================

def _make_raw_payload(
    bounding_box_id: str  = "taiwan_strait",
    strategic_tag: str    = "taiwan_strait",
    lat_min: float        = 22.0,
    lat_max: float        = 27.0,
    lon_min: float        = 118.0,
    lon_max: float        = 123.0,
    aircraft_count: int   = 47,
    states_compact: list  = None,
    mode: str             = "static",
    observation_time: int = 1743760800,
    fetch_timestamp: str  = "2026-04-04T10:00:00+00:00",
) -> dict:
    """Build a minimal raw_payload that mirrors OpenSkyProducer._build_raw_payload()."""
    return {
        "bounding_box_id":  bounding_box_id,
        "strategic_tag":    strategic_tag,
        "lat_min":          lat_min,
        "lat_max":          lat_max,
        "lon_min":          lon_min,
        "lon_max":          lon_max,
        "aircraft_count":   aircraft_count,
        "states_compact":   states_compact if states_compact is not None else [],
        "observation_time": observation_time,
        "fetch_timestamp":  fetch_timestamp,
        "mode":             mode,
    }


def _make_envelope(raw_payload: dict = None, box: dict = None) -> dict:
    """Build a full Bronze envelope from a raw_payload dict."""
    b   = box or _BOX
    raw = raw_payload or _make_raw_payload()
    lat_min, lat_max = b["lat_min"], b["lat_max"]
    lon_min, lon_max = b["lon_min"], b["lon_max"]
    endpoint = (
        f"{OPENSKY_BASE_URL}"
        f"?lamin={lat_min}&lamax={lat_max}&lomin={lon_min}&lomax={lon_max}"
    )
    return build_bronze_message(
        source_name=SOURCE_NAME,
        source_endpoint=endpoint,
        raw_payload=raw,
    )


def _make_silver(raw_payload: dict = None) -> dict:
    """Build a Silver Structured Metric directly via map_opensky_to_silver()."""
    raw      = raw_payload or _make_raw_payload()
    envelope = _make_envelope(raw)
    return map_opensky_to_silver(raw, envelope)


# Compact state for 3 aircraft used across multiple tests
_STATES_COMPACT_3 = [
    {"icao24": "abc123", "callsign": "CAL123 ", "lat": None, "lon": None, "on_ground": False, "last_contact": 1743760800},
    {"icao24": "def456", "callsign": "EVA456 ", "lat": 24.5,  "lon": 120.3, "on_ground": False, "last_contact": 1743760810},
    {"icao24": "ghi789", "callsign": "CKS789 ", "lat": None, "lon": None, "on_ground": True,  "last_contact": 1743760790},
]


# ==========================================================
# [01–03] Happy Paths
# ==========================================================

class TestHappyPaths:
    """Gate 2 Silver [01]–[03]: valid payloads route to SILVER_STRUCTURED_METRICS."""

    def test_01_static_mode_routes_to_silver(self):
        """[01] Valid static mode payload → SILVER_STRUCTURED_METRICS."""
        env = _make_envelope()
        topic, record = process_opensky_message(env)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record is not None

    def test_02_reactive_mode_routes_to_silver(self):
        """[02] Valid reactive mode payload → SILVER_STRUCTURED_METRICS."""
        raw = _make_raw_payload(mode="reactive")
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == SILVER_STRUCTURED_METRICS

    def test_03_aircraft_count_zero_routes_to_silver(self):
        """[03] aircraft_count=0 (empty box) → SILVER_STRUCTURED_METRICS, NOT DLQ."""
        raw = _make_raw_payload(aircraft_count=0, states_compact=[])
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == SILVER_STRUCTURED_METRICS
        assert record["data_point"]["current_value"] == 0.0


# ==========================================================
# [04–13] DLQ Routing
# ==========================================================

class TestDLQPaths:
    """Gate 2 Silver [04]–[13]: invalid payloads route to DEAD_LETTER_QUEUE."""

    def test_04_missing_envelope_fields(self):
        """[04] Envelope missing event_id → DLQ."""
        env = _make_envelope()
        bad = {k: v for k, v in env.items() if k != "event_id"}
        topic, record = process_opensky_message(bad)
        assert topic == DEAD_LETTER_QUEUE

    def test_05_invalid_bronze_payload(self):
        """[05] Bronze payload missing ingestion_id → DLQ."""
        env = _make_envelope()
        bad = copy.deepcopy(env)
        del bad["payload"]["ingestion_id"]
        topic, record = process_opensky_message(bad)
        assert topic == DEAD_LETTER_QUEUE

    def test_06_missing_bounding_box_id(self):
        """[06] raw_payload missing bounding_box_id → DLQ."""
        raw = _make_raw_payload()
        del raw["bounding_box_id"]
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE

    def test_07_empty_bounding_box_id(self):
        """[07] raw_payload.bounding_box_id == '' → DLQ."""
        raw = _make_raw_payload(bounding_box_id="")
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE

    def test_08_missing_aircraft_count(self):
        """[08] raw_payload missing aircraft_count → DLQ."""
        raw = _make_raw_payload()
        del raw["aircraft_count"]
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE

    def test_09_non_integer_aircraft_count(self):
        """[09] raw_payload.aircraft_count is a string → DLQ."""
        raw = _make_raw_payload()
        raw["aircraft_count"] = "forty-seven"
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE

    def test_10_missing_lat_min(self):
        """[10] raw_payload missing lat_min → DLQ."""
        raw = _make_raw_payload()
        del raw["lat_min"]
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE

    def test_11_missing_lon_max(self):
        """[11] raw_payload missing lon_max → DLQ."""
        raw = _make_raw_payload()
        del raw["lon_max"]
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE

    def test_12_dlq_record_carries_source_topic(self):
        """[12] DLQ record source_topic == BRONZE_OPENSKY."""
        raw = _make_raw_payload(bounding_box_id="")
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE
        assert record["source_topic"] == BRONZE_OPENSKY

    def test_13_dlq_record_carries_original_message(self):
        """[13] DLQ record carries original_message intact for replay."""
        raw = _make_raw_payload(bounding_box_id="")
        env = _make_envelope(raw)
        topic, record = process_opensky_message(env)
        assert topic == DEAD_LETTER_QUEUE
        assert record["original_message"]["event_id"] == env["event_id"]


# ==========================================================
# [14–18] Transponder Silence Domain Filter
# ==========================================================

class TestTransponderSilenceDomainFilter:
    """Gate 2 Silver [14]–[18]: Silver applies on_ground domain filter correctly."""

    def test_14_airborne_null_position_counts_as_silence(self):
        """[14] lat=None AND lon=None AND on_ground=False → counted."""
        raw = _make_raw_payload(states_compact=[
            {"icao24": "abc123", "callsign": "X", "lat": None, "lon": None,
             "on_ground": False, "last_contact": 1743760800},
        ])
        silver = _make_silver(raw)
        assert silver["metadata_extension"]["transponder_silence_events"] == 1

    def test_15_grounded_null_position_not_counted(self):
        """[15] lat=None AND lon=None AND on_ground=True → NOT counted."""
        raw = _make_raw_payload(states_compact=[
            {"icao24": "ghi789", "callsign": "X", "lat": None, "lon": None,
             "on_ground": True, "last_contact": 1743760790},
        ])
        silver = _make_silver(raw)
        assert silver["metadata_extension"]["transponder_silence_events"] == 0

    def test_16_aircraft_with_valid_position_not_counted(self):
        """[16] lat is not None → NOT counted (has position)."""
        raw = _make_raw_payload(states_compact=[
            {"icao24": "def456", "callsign": "X", "lat": 24.5, "lon": 120.3,
             "on_ground": False, "last_contact": 1743760810},
        ])
        silver = _make_silver(raw)
        assert silver["metadata_extension"]["transponder_silence_events"] == 0

    def test_17_empty_states_compact_gives_zero_silence(self):
        """[17] Empty states_compact → transponder_silence_events == 0."""
        raw = _make_raw_payload(states_compact=[])
        silver = _make_silver(raw)
        assert silver["metadata_extension"]["transponder_silence_events"] == 0

    def test_18_three_aircraft_only_one_counts(self):
        """[18] Mixed states — only the airborne null-position aircraft counts."""
        raw = _make_raw_payload(aircraft_count=3, states_compact=_STATES_COMPACT_3)
        silver = _make_silver(raw)
        # abc123: lat=None, lon=None, on_ground=False → counts
        # def456: has position → does not count
        # ghi789: lat=None, lon=None, on_ground=True → does not count
        assert silver["metadata_extension"]["transponder_silence_events"] == 1


# ==========================================================
# [19–28] Silver Schema Compliance
# ==========================================================

class TestSilverSchemaCompliance:
    """Gate 2 Silver [19]–[28]: Silver output schema correctness."""

    def test_19_passes_validate_silver_structured_metric(self):
        """[19] Silver output passes validate_silver_structured_metric()."""
        silver = _make_silver()
        result = validate_silver_structured_metric(silver)
        assert result.is_valid, result.errors

    def test_20_schema_version_layer_entity_type(self):
        """[20] schema_version == '2.0', layer == 'Silver', entity_type == 'Structured_Metric'."""
        silver = _make_silver()
        assert silver["schema_version"] == "2.0"
        assert silver["layer"]          == "Silver"
        assert silver["entity_type"]    == "Structured_Metric"

    def test_21_source_name_is_opensky(self):
        """[21] core_identity.source_name == 'opensky'."""
        silver = _make_silver()
        assert silver["core_identity"]["source_name"] == "opensky"

    def test_22_external_reference_id_equals_bounding_box_id(self):
        """[22] core_identity.external_reference_id == bounding_box_id."""
        raw    = _make_raw_payload(bounding_box_id="strait_of_hormuz")
        silver = _make_silver(raw)
        assert silver["core_identity"]["external_reference_id"] == "strait_of_hormuz"

    def test_23_metric_id_is_valid_uuid(self):
        """[23] core_identity.metric_id is a valid UUID."""
        silver = _make_silver()
        val = silver["core_identity"]["metric_id"]
        uuid.UUID(val)  # raises ValueError if invalid

    def test_24_canonical_event_id_matches_envelope(self):
        """[24] core_identity.canonical_event_id == Bronze envelope event_id."""
        raw      = _make_raw_payload()
        env      = _make_envelope(raw)
        silver   = map_opensky_to_silver(raw, env)
        assert silver["core_identity"]["canonical_event_id"] == env["event_id"]

    def test_25_current_value_equals_aircraft_count(self):
        """[25] data_point.current_value == float(aircraft_count)."""
        raw = _make_raw_payload(aircraft_count=47)
        silver = _make_silver(raw)
        assert silver["data_point"]["current_value"] == 47.0

    def test_26_unit_is_aircraft_count(self):
        """[26] data_point.unit == 'aircraft_count'."""
        silver = _make_silver()
        assert silver["data_point"]["unit"] == "aircraft_count"

    def test_27_status_is_static_for_static_mode(self):
        """[27] data_point.status == 'static' for static mode."""
        raw = _make_raw_payload(mode="static")
        silver = _make_silver(raw)
        assert silver["data_point"]["status"] == "static"

    def test_28_status_is_reactive_for_reactive_mode(self):
        """[28] data_point.status == 'reactive' for reactive mode."""
        raw = _make_raw_payload(mode="reactive")
        silver = _make_silver(raw)
        assert silver["data_point"]["status"] == "reactive"


# ==========================================================
# [29–34] Metadata Extension Fields
# ==========================================================

class TestMetadataExtension:
    """Gate 2 Silver [29]–[34]: metadata_extension content."""

    def test_29_bounding_box_id_present(self):
        """[29] bounding_box_id present in metadata_extension."""
        silver = _make_silver()
        assert "bounding_box_id" in silver["metadata_extension"]
        assert silver["metadata_extension"]["bounding_box_id"] == "taiwan_strait"

    def test_30_aircraft_density_count_equals_aircraft_count(self):
        """[30] aircraft_density_count == aircraft_count."""
        raw = _make_raw_payload(aircraft_count=35)
        silver = _make_silver(raw)
        assert silver["metadata_extension"]["aircraft_density_count"] == 35

    def test_31_transponder_silence_events_is_non_negative(self):
        """[31] transponder_silence_events is a non-negative integer."""
        silver = _make_silver()
        val = silver["metadata_extension"]["transponder_silence_events"]
        assert isinstance(val, int)
        assert val >= 0

    def test_32_anomaly_score_stub_is_zero(self):
        """[32] anomaly_score == 0.0 (stub — Gold fills in after momentum computation)."""
        silver = _make_silver()
        assert silver["metadata_extension"]["anomaly_score"] == 0.0

    def test_33_strategic_tag_present(self):
        """[33] strategic_tag present in metadata_extension."""
        silver = _make_silver()
        assert "strategic_tag" in silver["metadata_extension"]
        assert isinstance(silver["metadata_extension"]["strategic_tag"], str)

    def test_34_bounding_box_coords_dict_present(self):
        """[34] bounding_box_coords dict with lat_min, lat_max, lon_min, lon_max."""
        silver = _make_silver()
        coords = silver["metadata_extension"]["bounding_box_coords"]
        assert isinstance(coords, dict)
        for key in ("lat_min", "lat_max", "lon_min", "lon_max"):
            assert key in coords, f"bounding_box_coords missing key: {key}"


# ==========================================================
# [35–38] Momentum Block Stubs
# ==========================================================

class TestMomentumBlockStubs:
    """Gate 2 Silver [35]–[38]: momentum stubs are 0.0 (Gold computes real values)."""

    def test_35_change_24h_stub(self):
        """[35] momentum_block.change_24h == 0.0."""
        assert _make_silver()["momentum_block"]["change_24h"] == 0.0

    def test_36_change_7d_stub(self):
        """[36] momentum_block.change_7d == 0.0."""
        assert _make_silver()["momentum_block"]["change_7d"] == 0.0

    def test_37_change_30d_stub(self):
        """[37] momentum_block.change_30d == 0.0."""
        assert _make_silver()["momentum_block"]["change_30d"] == 0.0

    def test_38_is_new_market_stub(self):
        """[38] momentum_block.is_new_market == False (Gold sets True when empty history)."""
        assert _make_silver()["momentum_block"]["is_new_market"] is False


# ==========================================================
# [39] End-to-End Mock Round-Trip
# ==========================================================

class TestMockRoundTrip:
    """Gate 2 Silver [39]: mock payload from JSON file round-trips correctly."""

    def test_39_mock_payload_round_trip(self):
        """[39] opensky_bronze_payload.json: aircraft_count=47, transponder_silence_events=1."""
        assert _MOCK_FILE.exists(), f"Mock file not found: {_MOCK_FILE}"
        raw = json.loads(_MOCK_FILE.read_text(encoding="utf-8"))
        # Remove the _comment field before building the envelope
        raw_clean = {k: v for k, v in raw.items() if not k.startswith("_")}

        env    = _make_envelope(raw_clean)
        topic, silver = process_opensky_message(env)

        assert topic == SILVER_STRUCTURED_METRICS, f"Unexpected topic: {topic}"
        assert silver["data_point"]["current_value"]                    == 47.0
        # abc123: lat=None, lon=None, on_ground=False → 1 silence event
        # def456: has position → 0
        # ghi789: lat=None, lon=None, on_ground=True → 0
        assert silver["metadata_extension"]["transponder_silence_events"] == 1
        assert silver["metadata_extension"]["anomaly_score"]              == 0.0
        assert silver["metadata_extension"]["bounding_box_id"]            == "taiwan_strait"
