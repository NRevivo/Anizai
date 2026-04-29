"""
Gate 1 — Ingestion Gate: OpenSky Bronze Schema Compliance.

Validates that the OpenSky producer's envelope-building logic produces messages
fully compliant with the Bronze Schema (Section C.1) and Message Envelope
(Section 3.2), and that all producer config invariants match the spec —
before any message reaches Kafka.

Design decisions:
  - Zero live connections: no Kafka broker, no OpenSky API calls.
    _build_raw_payload() and build_bronze_message() are pure functions
    tested in complete isolation (same pattern as test_openweather_gate1.py).
  - Constants imported from the producer are tested for spec invariants without
    hard-coding values — guards against config drift.
  - Validators from utils/validators.py are called directly — the same validators
    the Flink Silver Job uses, so a Gate 1 pass guarantees the Silver Job's first
    validation gate will also pass.

Gate 1 checklist (Section 9.3 Gate 1):

  ENVELOPE STRUCTURE
  [01] All required envelope fields present and correctly typed
  [02] event_id is a valid UUIDv4 string
  [03] producer_timestamp is a valid ISO8601 UTC string
  [04] source_name == "opensky"
  [05] payload.raw_payload is a non-empty dict
  [06] validate_envelope() passes without errors
  [07] validate_bronze_payload() passes without errors
  [08] NDJSON round-trip (serialize → deserialize) preserves the full envelope
  [09] Each call to build_bronze_message() produces a distinct event_id

  RAW PAYLOAD — BOUNDING BOX FIELDS
  [10] bounding_box_id is a non-empty string
  [11] strategic_tag is a non-empty string
  [12] lat_min, lat_max, lon_min, lon_max are all present and numeric
  [13] aircraft_count is a non-negative integer
  [14] aircraft_count == 0 is valid (empty box — real measurement, not error)
  [15] states_compact is a list (may be empty)
  [16] Each entry in states_compact has keys: icao24, callsign, lat, lon, on_ground, last_contact
  [17] fetch_timestamp is a non-empty ISO8601 string
  [18] mode is one of "static" | "reactive"
  [19] target_callsigns key present when mode="reactive", absent when mode="static"

  KAFKA PARTITION KEY
  [20] Partition key is bounding_box_id encoded as UTF-8 bytes
  [20b] Partition key is deterministic — two successive calls for the same box
        produce identical bytes (validates Flink keyed-state affinity)

  PRODUCER CONFIG INVARIANTS — BOUNDING_BOXES LIST
  [21] BOUNDING_BOXES list has exactly 7 entries
  [22] Every entry has required keys: bounding_box_id, lat_min, lat_max, lon_min, lon_max, strategic_tag
  [23] Every bounding_box_id is a non-empty string
  [24] No duplicate bounding_box_ids
  [25] Every lat_min < lat_max (valid latitude ordering)
  [26] Every lon_min < lon_max (valid longitude ordering)
  [27] Every lat value is within [-90.0, 90.0]
  [28] Every lon value is within [-180.0, 180.0]
  [29] Every strategic_tag is a non-empty string

  PRODUCER CONFIG INVARIANTS — CONSTANTS
  [30] STATIC_POLL_INTERVAL_SEC == 60
  [31] REACTIVE_POLL_INTERVAL_SEC == 30
  [32] REACTIVE_WINDOW_MINUTES == 30
  [33] ANON_REQUEST_DELAY_SEC == 6.5
  [34] AUTH_REQUEST_DELAY_SEC == 1.5
  [35] DENSITY_ESCALATION_THRESHOLD == 0.30
  [36] SOURCE_NAME == "opensky"

  TENSION ZONES (imported from gold_job — structural invariant)
  [37] _OPENSKY_TENSION_ZONES does not include "washington_dc_airspace"
  [38] _OPENSKY_TENSION_ZONES includes the 6 expected high-tension boxes

References:
    - Section 2.1:  Producer Matrix (OpenSky row)
    - Section 3.2:  Message Envelope
    - Section 3.4:  NDJSON serialisation
    - Section 4.4:  Mock-Driven Development
    - Section 9.3:  Triple-Gate Test Matrix — Gate 1
    - Section B.7:  OpenSky parameters & strategic bounding boxes
    - Section C.1:  Bronze Schema
    - Section C.2:  Silver Structured Metric — OpenSky metadata_extension fields
"""

import json
import re
import uuid
from datetime import datetime, timezone

import pytest

from ingestion.opensky_producer import (
    ANON_REQUEST_DELAY_SEC,
    AUTH_REQUEST_DELAY_SEC,
    BOUNDING_BOXES,
    DENSITY_ESCALATION_THRESHOLD,
    OPENSKY_BASE_URL,
    REACTIVE_POLL_INTERVAL_SEC,
    REACTIVE_WINDOW_MINUTES,
    SOURCE_NAME,
    STATIC_POLL_INTERVAL_SEC,
    OpenSkyProducer,
)
from processing.gold_job import _OPENSKY_TENSION_ZONES
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


def _make_producer() -> OpenSkyProducer:
    """
    Instantiate OpenSkyProducer without starting Kafka or hitting the API.

    We only need _build_raw_payload() and _extract_states_compact() for Gate 1,
    which are pure functions. Bypass __init__ (which creates a Kafka producer and
    reads env vars) via __new__ to avoid requiring a live Kafka broker or
    credentials (same pattern as test_openweather_gate1.py).

    OAuth2 token state initialised to None/0.0 — _fetch_box is not called in
    Gate 1, so _ensure_token() is never triggered.
    """
    p = OpenSkyProducer.__new__(OpenSkyProducer)
    p._authenticated = False
    p._request_delay = ANON_REQUEST_DELAY_SEC
    p._access_token = None
    p._token_expires_at = 0.0
    p._emitted = 0
    return p


_producer = _make_producer()

# Reference box — Taiwan Strait
_BOX = BOUNDING_BOXES[1]  # taiwan_strait
_FETCH_TS = "2026-04-04T10:00:00+00:00"

# Realistic raw states list — 3 aircraft, one with null position (silence event candidate)
_RAW_STATES = [
    ["abc123", "CAL123 ", "Taiwan", 1743760800, 1743760800, None, None, None, False, None, None, None, None, None, None, False, 0],
    ["def456", "EVA456 ", "Taiwan", 1743760810, 1743760810, 120.3, 24.5, 8500.0, False, 250.0, 180.0, 0.0, None, 8200.0, "7700", False, 0],
    ["ghi789", "CKS789 ", "Taiwan", 1743760790, 1743760790, None, None, None, True,  0.0,   0.0,   0.0,  None, None,   None,   False, 0],
]


def _make_api_data(states: list = None) -> dict:
    """Build a minimal OpenSky API response dict."""
    return {
        "time":   1743760800,
        "states": states if states is not None else _RAW_STATES,
    }


def _make_raw(
    box: dict = None,
    states: list = None,
    mode: str = "static",
    target_callsigns: list = None,
) -> dict:
    """Build a raw_payload using _build_raw_payload()."""
    b = box or _BOX
    api = _make_api_data(states)
    return _producer._build_raw_payload(b, api, _FETCH_TS, mode, target_callsigns)


def _make_envelope(raw: dict = None, box: dict = None) -> dict:
    b   = box or _BOX
    raw = raw or _make_raw(box=b)
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


# ==========================================================
# [01–09] Envelope Structure
# ==========================================================

class TestEnvelopeStructure:
    """Gate 1 checks [01]–[09]: Bronze envelope fields and types."""

    def test_01_required_fields_present_and_typed(self):
        """[01] All required envelope fields are present with correct types."""
        env = _make_envelope()
        assert isinstance(env.get("event_id"), str)
        assert isinstance(env.get("trace_id"), str)
        assert isinstance(env.get("producer_timestamp"), str)
        assert isinstance(env.get("schema_version"), str)
        assert isinstance(env.get("source_name"), str)
        assert isinstance(env.get("payload"), dict)

    def test_02_event_id_is_valid_uuid4(self):
        """[02] event_id is a valid UUIDv4 string."""
        env = _make_envelope()
        assert _is_valid_uuid4(env["event_id"])

    def test_03_producer_timestamp_is_iso8601(self):
        """[03] producer_timestamp is a valid ISO8601 UTC string."""
        env = _make_envelope()
        assert _is_valid_iso8601(env["producer_timestamp"])

    def test_04_source_name_is_opensky(self):
        """[04] source_name == 'opensky'."""
        env = _make_envelope()
        assert env["source_name"] == "opensky"

    def test_05_raw_payload_is_non_empty_dict(self):
        """[05] payload.raw_payload is a non-empty dict."""
        env = _make_envelope()
        raw = env["payload"]["raw_payload"]
        assert isinstance(raw, dict)
        assert len(raw) > 0

    def test_06_validate_envelope_passes(self):
        """[06] validate_envelope() passes without errors."""
        env = _make_envelope()
        result = validate_envelope(env)
        assert result.is_valid, result.errors

    def test_07_validate_bronze_payload_passes(self):
        """[07] validate_bronze_payload() passes without errors."""
        env = _make_envelope()
        result = validate_bronze_payload(env["payload"])
        assert result.is_valid, result.errors

    def test_08_ndjson_round_trip(self):
        """[08] NDJSON serialize → deserialize preserves the full envelope."""
        env = _make_envelope()
        serialized   = ndjson_serializer(env)
        deserialized = ndjson_deserializer(serialized)
        assert deserialized["event_id"]           == env["event_id"]
        assert deserialized["source_name"]        == env["source_name"]
        assert deserialized["payload"]["raw_payload"]["bounding_box_id"] == \
               env["payload"]["raw_payload"]["bounding_box_id"]

    def test_09_successive_calls_produce_distinct_event_ids(self):
        """[09] Each call to build_bronze_message() produces a distinct event_id."""
        env1 = _make_envelope()
        env2 = _make_envelope()
        assert env1["event_id"] != env2["event_id"]


# ==========================================================
# [10–19] Raw Payload Fields
# ==========================================================

class TestRawPayloadFields:
    """Gate 1 checks [10]–[19]: raw_payload content and types."""

    def test_10_bounding_box_id_is_non_empty_string(self):
        """[10] bounding_box_id is a non-empty string."""
        raw = _make_raw()
        assert isinstance(raw["bounding_box_id"], str)
        assert len(raw["bounding_box_id"]) > 0

    def test_11_strategic_tag_is_non_empty_string(self):
        """[11] strategic_tag is a non-empty string."""
        raw = _make_raw()
        assert isinstance(raw["strategic_tag"], str)
        assert len(raw["strategic_tag"]) > 0

    def test_12_bounding_box_coords_present_and_numeric(self):
        """[12] lat_min, lat_max, lon_min, lon_max are all present and numeric."""
        raw = _make_raw()
        for field in ("lat_min", "lat_max", "lon_min", "lon_max"):
            assert field in raw, f"{field} missing from raw_payload"
            assert isinstance(raw[field], (int, float)), f"{field} is not numeric"

    def test_13_aircraft_count_is_non_negative_integer(self):
        """[13] aircraft_count is a non-negative integer."""
        raw = _make_raw()
        assert isinstance(raw["aircraft_count"], int)
        assert raw["aircraft_count"] >= 0

    def test_14_aircraft_count_zero_is_valid(self):
        """[14] aircraft_count==0 is valid (empty box — real measurement, not error)."""
        raw = _make_raw(states=[])
        assert raw["aircraft_count"] == 0
        # Must still produce a valid Bronze envelope
        env = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=f"{OPENSKY_BASE_URL}?lamin=22.0&lamax=27.0&lomin=118.0&lomax=123.0",
            raw_payload=raw,
        )
        result = validate_envelope(env)
        assert result.is_valid, result.errors

    def test_15_states_compact_is_a_list(self):
        """[15] states_compact is a list (may be empty)."""
        raw = _make_raw()
        assert isinstance(raw["states_compact"], list)

    def test_16_states_compact_entries_have_required_keys(self):
        """[16] Each entry in states_compact has required keys."""
        raw = _make_raw()
        required_keys = {"icao24", "callsign", "lat", "lon", "on_ground", "last_contact"}
        for entry in raw["states_compact"]:
            assert isinstance(entry, dict), "states_compact entry is not a dict"
            assert required_keys.issubset(entry.keys()), (
                f"states_compact entry missing keys: {required_keys - entry.keys()}"
            )

    def test_17_fetch_timestamp_is_iso8601(self):
        """[17] fetch_timestamp is a non-empty ISO8601 string."""
        raw = _make_raw()
        assert isinstance(raw["fetch_timestamp"], str)
        assert _is_valid_iso8601(raw["fetch_timestamp"])

    def test_18_mode_is_valid_string(self):
        """[18] mode is one of 'static' | 'reactive'."""
        raw_static   = _make_raw(mode="static")
        raw_reactive = _make_raw(mode="reactive", target_callsigns=["abc123"])
        assert raw_static["mode"]   == "static"
        assert raw_reactive["mode"] == "reactive"

    def test_19_target_callsigns_present_only_in_reactive_mode(self):
        """[19] target_callsigns present when reactive, absent when static."""
        raw_static   = _make_raw(mode="static")
        raw_reactive = _make_raw(mode="reactive", target_callsigns=["abc123", "def456"])
        assert "target_callsigns" not in raw_static
        assert raw_reactive.get("target_callsigns") == ["abc123", "def456"]


# ==========================================================
# [20] Kafka Partition Key
# ==========================================================

class TestPartitionKey:
    """Gate 1 checks [20]–[20b]: partition key encoding and determinism."""

    def test_20_partition_key_is_bounding_box_id_bytes(self):
        """[20] Partition key is bounding_box_id encoded as UTF-8 bytes."""
        raw = _make_raw()
        expected_key = raw["bounding_box_id"].encode("utf-8")
        assert expected_key == b"taiwan_strait"

    def test_20b_partition_key_is_deterministic(self):
        """[20b] Two successive payloads for the same box produce identical partition keys."""
        raw1 = _make_raw(box=BOUNDING_BOXES[1])
        raw2 = _make_raw(box=BOUNDING_BOXES[1])
        key1 = raw1["bounding_box_id"].encode("utf-8")
        key2 = raw2["bounding_box_id"].encode("utf-8")
        assert key1 == key2


# ==========================================================
# [21–29] BOUNDING_BOXES Structural Invariants
# ==========================================================

class TestBoundingBoxesInvariants:
    """Gate 1 checks [21]–[29]: BOUNDING_BOXES list structural correctness."""

    def test_21_exactly_7_entries(self):
        """[21] BOUNDING_BOXES list has exactly 7 entries."""
        assert len(BOUNDING_BOXES) == 7

    def test_22_every_entry_has_required_keys(self):
        """[22] Every entry has all required keys."""
        required = {"bounding_box_id", "lat_min", "lat_max", "lon_min", "lon_max", "strategic_tag"}
        for box in BOUNDING_BOXES:
            missing = required - box.keys()
            assert not missing, f"Box {box.get('bounding_box_id')} missing keys: {missing}"

    def test_23_every_bounding_box_id_is_non_empty_string(self):
        """[23] Every bounding_box_id is a non-empty string."""
        for box in BOUNDING_BOXES:
            assert isinstance(box["bounding_box_id"], str)
            assert len(box["bounding_box_id"]) > 0

    def test_24_no_duplicate_bounding_box_ids(self):
        """[24] No duplicate bounding_box_ids."""
        ids = [b["bounding_box_id"] for b in BOUNDING_BOXES]
        assert len(ids) == len(set(ids)), f"Duplicate bounding_box_ids: {ids}"

    def test_25_lat_min_less_than_lat_max(self):
        """[25] Every lat_min < lat_max (valid latitude ordering)."""
        for box in BOUNDING_BOXES:
            assert box["lat_min"] < box["lat_max"], (
                f"{box['bounding_box_id']}: lat_min={box['lat_min']} >= lat_max={box['lat_max']}"
            )

    def test_26_lon_min_less_than_lon_max(self):
        """[26] Every lon_min < lon_max (valid longitude ordering)."""
        for box in BOUNDING_BOXES:
            assert box["lon_min"] < box["lon_max"], (
                f"{box['bounding_box_id']}: lon_min={box['lon_min']} >= lon_max={box['lon_max']}"
            )

    def test_27_lat_values_in_valid_range(self):
        """[27] Every lat value is within [-90.0, 90.0]."""
        for box in BOUNDING_BOXES:
            for field in ("lat_min", "lat_max"):
                val = box[field]
                assert -90.0 <= val <= 90.0, (
                    f"{box['bounding_box_id']}.{field}={val} out of range [-90, 90]"
                )

    def test_28_lon_values_in_valid_range(self):
        """[28] Every lon value is within [-180.0, 180.0]."""
        for box in BOUNDING_BOXES:
            for field in ("lon_min", "lon_max"):
                val = box[field]
                assert -180.0 <= val <= 180.0, (
                    f"{box['bounding_box_id']}.{field}={val} out of range [-180, 180]"
                )

    def test_29_every_strategic_tag_is_non_empty_string(self):
        """[29] Every strategic_tag is a non-empty string."""
        for box in BOUNDING_BOXES:
            assert isinstance(box["strategic_tag"], str)
            assert len(box["strategic_tag"]) > 0


# ==========================================================
# [30–36] Producer Constants
# ==========================================================

class TestProducerConstants:
    """Gate 1 checks [30]–[36]: producer configuration constants match spec."""

    def test_30_static_poll_interval(self):
        """[30] STATIC_POLL_INTERVAL_SEC == 180 (3-min cadence, 3,360 calls/day within 4,000/day cap)."""
        assert STATIC_POLL_INTERVAL_SEC == 180

    def test_31_reactive_poll_interval(self):
        """[31] REACTIVE_POLL_INTERVAL_SEC == 30."""
        assert REACTIVE_POLL_INTERVAL_SEC == 30

    def test_32_reactive_window_minutes(self):
        """[32] REACTIVE_WINDOW_MINUTES == 30."""
        assert REACTIVE_WINDOW_MINUTES == 30

    def test_33_anon_request_delay(self):
        """[33] ANON_REQUEST_DELAY_SEC == 1.0 (daily cap is binding; inter-box delay minimised)."""
        assert ANON_REQUEST_DELAY_SEC == 1.0

    def test_34_auth_request_delay(self):
        """[34] AUTH_REQUEST_DELAY_SEC == 1.0 (daily cap is binding; inter-box delay minimised)."""
        assert AUTH_REQUEST_DELAY_SEC == 1.0

    def test_35_density_escalation_threshold(self):
        """[35] DENSITY_ESCALATION_THRESHOLD == 0.30."""
        assert DENSITY_ESCALATION_THRESHOLD == 0.30

    def test_36_source_name(self):
        """[36] SOURCE_NAME == 'opensky'."""
        assert SOURCE_NAME == "opensky"


# ==========================================================
# [37–38] Tension Zones Structural Invariants
# ==========================================================

class TestTensionZonesInvariants:
    """Gate 1 checks [37]–[38]: TENSION_ZONES set matches design decisions."""

    def test_37_washington_dc_not_in_tension_zones(self):
        """[37] washington_dc_airspace is NOT in TENSION_ZONES (high false-positive risk)."""
        assert "washington_dc_airspace" not in _OPENSKY_TENSION_ZONES

    def test_38_tension_zones_contains_expected_6_boxes(self):
        """[38] TENSION_ZONES contains all 6 high-tension boxes."""
        expected = {
            "polish_ukrainian_border",
            "taiwan_strait",
            "iranian_borders",
            "strait_of_hormuz",
            "bab_al_mandab",
            "eastern_mediterranean",
        }
        assert expected == _OPENSKY_TENSION_ZONES, (
            f"TENSION_ZONES mismatch.\n"
            f"Expected: {expected}\n"
            f"Got:      {set(_OPENSKY_TENSION_ZONES)}"
        )
