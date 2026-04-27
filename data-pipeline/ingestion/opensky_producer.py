"""
OpenSky Producer — OpenSky Network REST API, static polling & reactive mode.

Two operating modes (Section B.7):

  static    (default) — One-shot: fetches current aircraft states for all 7
                         strategic bounding boxes and emits one Bronze envelope
                         per box. Designed to be invoked every 60 seconds by an
                         Airflow DAG or Docker scheduled task.
                         Same one-shot-per-invocation pattern as openweather_producer.py.

  reactive            — On-demand: polls all boxes every REACTIVE_POLL_INTERVAL_SEC
                         (30 s) for a bounded REACTIVE_WINDOW_MINUTES (30 min) window,
                         filtering states for specific target callsigns. Invoked by the
                         RAG agent via the ingestion_triggers Kafka topic when callsign-
                         specific intelligence is needed (Section 2.4).

Why one envelope per bounding box (not a batch):
    The Silver Job maps each Bronze message to exactly one Silver Structured
    Metric row (Section C.2). Batching multiple boxes in one envelope would
    require the Silver Job to unpack and loop — violating the one-envelope-one-
    record contract and making DLQ routing ambiguous. Same rationale as
    openweather_producer.py.

Why states_compact instead of raw states array (Service Isolation §3.3):
    The OpenSky /states/all endpoint returns a states array where each element
    is a 17-field list. Passing the full array would:
      (a) create unbounded payload sizes (busy airspaces can return 200+ aircraft),
      (b) force Silver to index into positional arrays — fragile against API changes.
    Instead, the producer extracts a compact dict summary (5 fields per aircraft)
    that contains exactly the fields Silver needs to apply its domain filter for
    transponder_silence_events. The domain logic itself (on_ground == False check)
    stays in the Silver Job (Section 3.3).

Why aircraft_count is computed here (not in Silver):
    Counting len(states) is a direct structural property of the API response —
    equivalent to reading main.temp from OpenWeatherMap. No domain interpretation
    is involved. Silver receives the count and sets current_value directly.

Structural guard:
    If the OpenSky API returns a non-200 status or a JSON parse error, the box
    fetch is logged at ERROR level and skipped — no Bronze message is emitted for
    that box. This prevents malformed Bronze envelopes from entering the pipeline.
    An empty states list (states=[]) is a valid measurement (no aircraft in box)
    and MUST produce a Bronze message with aircraft_count=0.

Rate-limit strategy (Section B.7, task_plan.md design decision 2026-04-04):
    Authenticated (OPENSKY_CLIENT_ID + OPENSKY_CLIENT_SECRET both set): 4,000 calls/day.
      3-min Airflow cadence → 480 cycles/day × 7 boxes = 3,360 calls/day (16% buffer).
    Anonymous (either credential missing): ~100 calls/day.
      Both tiers use AUTH/ANON_REQUEST_DELAY_SEC=1.0 s between boxes (daily cap binding).
    Airflow handles the 3-minute cadence — the producer is one-shot per invocation.

Partition key: bounding_box_id (bytes) — ensures all observations for the same
    geographic box land in the same Kafka partition, preserving chronological
    order for the Gold Job's keyed-state momentum calculations (Section 4.2B).
    Same pattern as openweather_producer.py partitioning by hotspot_name.

Kafka Target: ingest.bronze.opensky (BRONZE_OPENSKY)

References:
    - Section 2.1:  Producer Matrix (OpenSky row)
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section 2.4:  Reactive Ingestion — On-Demand Tool Calling
    - Section B.7:  OpenSky technical parameters & strategic bounding boxes
    - Section C.1:  Bronze Schema
    - Section C.2:  Silver Structured Metric — OpenSky metadata_extension fields
    - Section 3.2:  Message Envelope & DRY (all Kafka logic in kafka_utils.py)
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.2B: Momentum Block — keyed state per bounding box
    - Section 9.1:  Environment parity — OPENSKY_CLIENT_ID/SECRET from .env
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config.kafka_topics import BRONZE_OPENSKY
from config.settings import OPENSKY_CLIENT_ID, OPENSKY_CLIENT_SECRET
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# OpenSky Constants (Section B.7)
# ==========================================================

OPENSKY_BASE_URL: str = "https://opensky-network.org/api/states/all"

# OAuth2 token endpoint (Section B.7, March 2026 auth migration).
OPENSKY_TOKEN_URL: str = (
    "https://auth.opensky-network.org/auth/realms/opensky-network"
    "/protocol/openid-connect/token"
)

# Static mode: 3-min cadence → 480 cycles/day × 7 boxes = 3,360 calls/day.
# 16% safety buffer under the 4,000/day authenticated cap.
# Updated from 60 s (old per-minute model) after OpenSky switched to daily
# quotas and OAuth2 in March 2026.
STATIC_POLL_INTERVAL_SEC: int = 180

# Reactive mode: poll interval and bounded window duration (Section B.7).
# 30-second cadence × 30-minute window = 60 iterations per reactive activation.
REACTIVE_POLL_INTERVAL_SEC: int = 30
REACTIVE_WINDOW_MINUTES: int = 30

# Inter-box delay within one cycle — daily cap is the binding rate limit.
# Both tiers use 1.0 s to keep a 7-box cycle under 10 s total.
ANON_REQUEST_DELAY_SEC: float = 1.0
AUTH_REQUEST_DELAY_SEC: float = 1.0

# Token refresh buffer: proactively refresh if token expires within this window.
TOKEN_REFRESH_BUFFER_SEC: int = 60

# HTTP request timeout (connect_sec, read_sec).
# OpenSky can be slow under load — 20 s read timeout prevents hanging.
REQUEST_TIMEOUT_SEC: tuple = (5, 20)

# Trigger threshold constants — defined here so Gate 1 tests can import and
# assert on them as structural invariants. Trigger logic lives in the Gold Job
# (Section 4.2B); these are the canonical source-of-truth values.
DENSITY_ESCALATION_THRESHOLD: float = 0.30   # change_30d > 0.30 → Aerial_Escalation_Risk

# Canonical source identifier written into every Bronze envelope.
SOURCE_NAME: str = "opensky"

# State vector field indices for the OpenSky /states/all response.
# Each element of the "states" array is a list with these positional fields.
# Defined here as constants so Silver Job can import them if needed, and to
# make the compact extraction logic self-documenting (Section 3.3).
_STATE_IDX_ICAO24        = 0
_STATE_IDX_CALLSIGN      = 1
_STATE_IDX_LAST_CONTACT  = 4
_STATE_IDX_LONGITUDE     = 5
_STATE_IDX_LATITUDE      = 6
_STATE_IDX_ON_GROUND     = 8


# ==========================================================
# Strategic Bounding Boxes Matrix (Section B.7)
# ==========================================================
# 7 pre-defined geographic bounding boxes covering:
#   Active conflict zones        — Polish-Ukrainian border, Iranian borders
#   Technology & trade nexus     — Taiwan Strait
#   Maritime chokepoints         — Strait of Hormuz, Bab al-Mandab
#   Regional military monitor    — Eastern Mediterranean
#   Capital airspace             — Washington D.C. Metro Area
#
# Design decision (task_plan.md 2026-04-04):
#   Jerusalem and London are expansion candidates for a future sprint.
#   Moscow is excluded — Russian ADS-B receiver restrictions make coverage
#   unreliable; the OpenSky Network has limited sensor coverage in that region.
#
# TENSION_ZONES (used by Gold Job Transponder_Deactivation_Alert):
#   All boxes except washington_dc_airspace — D.C. airspace generates too many
#   legitimate null-position readings from government/classified aircraft.
#   The tension-zone set is defined in gold_job.py; the canonical list is:
#   {polish_ukrainian_border, taiwan_strait, iranian_borders, strait_of_hormuz,
#    bab_al_mandab, eastern_mediterranean}
#
# Coordinates are bounding box extents (not centroid points).
# OpenSky /states/all uses: lamin, lamax, lomin, lomax.

BOUNDING_BOXES: list[dict] = [
    {
        "bounding_box_id": "polish_ukrainian_border",
        "lat_min": 49.0,
        "lat_max": 54.0,
        "lon_min": 22.0,
        "lon_max": 30.0,
        "strategic_tag":  "conflict_zone",
    },
    {
        "bounding_box_id": "taiwan_strait",
        "lat_min": 22.0,
        "lat_max": 27.0,
        "lon_min": 118.0,
        "lon_max": 123.0,
        "strategic_tag":  "taiwan_strait",
    },
    {
        "bounding_box_id": "iranian_borders",
        "lat_min": 25.0,
        "lat_max": 40.0,
        "lon_min": 44.0,
        "lon_max": 65.0,
        "strategic_tag":  "iranian_airspace",
    },
    {
        "bounding_box_id": "strait_of_hormuz",
        "lat_min": 24.5,
        "lat_max": 27.0,
        "lon_min": 55.0,
        "lon_max": 60.0,
        "strategic_tag":  "strait_of_hormuz",
    },
    {
        "bounding_box_id": "bab_al_mandab",
        "lat_min": 11.5,
        "lat_max": 14.0,
        "lon_min": 42.0,
        "lon_max": 45.5,
        "strategic_tag":  "bab_al_mandab",
    },
    {
        "bounding_box_id": "eastern_mediterranean",
        "lat_min": 30.0,
        "lat_max": 38.0,
        "lon_min": 26.0,
        "lon_max": 38.0,
        "strategic_tag":  "eastern_mediterranean",
    },
    {
        "bounding_box_id": "washington_dc_airspace",
        "lat_min": 37.5,
        "lat_max": 40.5,
        "lon_min": -80.0,
        "lon_max": -74.0,
        "strategic_tag":  "us_capital_airspace",
    },
]


# ==========================================================
# Producer
# ==========================================================

class OpenSkyProducer:
    """
    REST-based producer for OpenSky Network aircraft state data.

    Two operating modes (Section B.7):
      static   — one-shot fetch of all 7 bounding boxes; called every 60 s by
                 Airflow (same invocation pattern as openweather_producer.py).
      reactive — bounded loop: polls all boxes every 30 s for 30 min, filtering
                 for specific target callsigns. Triggered by the RAG agent via
                 ingestion_triggers (Section 2.4).

    Auth mode auto-detection (March 2026 — OAuth2 client credentials):
      If both OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET are set in .env →
        authenticated mode (4,000 calls/day); Bearer token fetched via
        OPENSKY_TOKEN_URL and refreshed automatically before expiry.
      Else → anonymous mode (~100 calls/day); no Authorization header sent.

    Emits to: ingest.bronze.opensky (BRONZE_OPENSKY)
    """

    def __init__(self) -> None:
        self._authenticated = bool(OPENSKY_CLIENT_ID and OPENSKY_CLIENT_SECRET)
        self._request_delay = (
            AUTH_REQUEST_DELAY_SEC if self._authenticated else ANON_REQUEST_DELAY_SEC
        )
        # OAuth2 token state — populated on first _ensure_token() call.
        self._access_token: Optional[str] = None
        self._token_expires_at: float = 0.0  # Unix timestamp

        mode_label = "authenticated" if self._authenticated else "anonymous"
        logger.info(
            "[opensky] Producer initialised — mode=%s, request_delay=%.1fs",
            mode_label, self._request_delay,
        )

        self._producer = make_producer()
        self._emitted: int = 0

    # ----------------------------------------------------------
    # OAuth2 Token Management
    # ----------------------------------------------------------

    def _ensure_token(self) -> None:
        """
        Fetch or refresh the OAuth2 Bearer token if expired or near expiry.

        Uses client_credentials grant (Section B.7, March 2026 auth update).
        Refreshes proactively when fewer than TOKEN_REFRESH_BUFFER_SEC seconds
        remain — prevents mid-cycle 401s without adding per-request overhead.
        No-op in anonymous mode (self._authenticated == False).
        """
        if not self._authenticated:
            return

        now = time.time()
        if self._access_token and now < self._token_expires_at - TOKEN_REFRESH_BUFFER_SEC:
            return  # Token still valid

        logger.info("[opensky] Fetching OAuth2 token (client_credentials)")
        try:
            resp = requests.post(
                OPENSKY_TOKEN_URL,
                data={
                    "grant_type": "client_credentials",
                    "client_id": OPENSKY_CLIENT_ID,
                    "client_secret": OPENSKY_CLIENT_SECRET,
                },
                timeout=REQUEST_TIMEOUT_SEC,
            )
            resp.raise_for_status()
            token_data = resp.json()
        except requests.RequestException as exc:
            logger.error("[opensky] OAuth2 token fetch failed: %s — falling back to anonymous", exc)
            self._access_token = None
            self._token_expires_at = 0.0
            return

        self._access_token = token_data.get("access_token")
        expires_in = token_data.get("expires_in", 300)
        self._token_expires_at = time.time() + expires_in
        logger.info("[opensky] OAuth2 token acquired — expires_in=%ds", expires_in)

    # ----------------------------------------------------------
    # OpenSky REST Fetch
    # ----------------------------------------------------------

    def _fetch_box(self, box: dict) -> Optional[dict]:
        """
        Fetch current aircraft states for one bounding box.

        Calls /states/all with lamin/lamax/lomin/lomax parameters.
        Returns the raw API response dict on success, or None on any error.

        Structural guard:
            An empty states list (states=[]) is a valid measurement and is
            returned as-is — it means no aircraft are currently in the box.
            Only HTTP errors and JSON parse failures cause a None return and
            skip the Bronze message for this box.

        Args:
            box: Entry from BOUNDING_BOXES — must have lat_min, lat_max,
                 lon_min, lon_max, bounding_box_id.

        Returns:
            Raw API response dict (with 'states' key, may be empty list or None),
            or None on fetch / parse failure.
        """
        box_id = box["bounding_box_id"]
        params = {
            "lamin": box["lat_min"],
            "lamax": box["lat_max"],
            "lomin": box["lon_min"],
            "lomax": box["lon_max"],
        }

        headers = {}
        if self._authenticated and self._access_token:
            headers["Authorization"] = f"Bearer {self._access_token}"

        try:
            response, duration_ms = timed_request(
                lambda: requests.get(
                    OPENSKY_BASE_URL,
                    params=params,
                    headers=headers,
                    timeout=REQUEST_TIMEOUT_SEC,
                )
            )
            response.raise_for_status()
            data = response.json()

        except requests.HTTPError as exc:
            logger.error(
                "[opensky] %s — HTTP error: %s — skipping box",
                box_id, exc,
            )
            return None
        except requests.RequestException as exc:
            logger.error(
                "[opensky] %s — Network error: %s — skipping box",
                box_id, exc,
            )
            return None
        except ValueError as exc:
            logger.error(
                "[opensky] %s — JSON parse error: %s — skipping box",
                box_id, exc,
            )
            return None

        logger.debug(
            "[opensky] %s — fetched OK (%d ms)",
            box_id, duration_ms,
        )
        return data

    # ----------------------------------------------------------
    # Compact State Extraction
    # ----------------------------------------------------------

    def _extract_states_compact(
        self, raw_states: list | None
    ) -> tuple[int, list[dict]]:
        """
        Extract a compact summary from the raw OpenSky states array.

        Why compact (not full states):
            The full state vector has 17 positional fields per aircraft. Passing
            the full array creates unbounded payload sizes (busy airspaces return
            200+ aircraft). The Silver Job only needs 5 fields to apply its domain
            filter for transponder_silence_events (lat, lon, on_ground, icao24,
            last_contact). Extracting only these fields here keeps Silver stateless
            and the payload size bounded (≈6KB for 100 aircraft) (Section 3.3).

        Domain logic stays in Silver:
            This method performs only structural extraction — it reads positional
            fields from the state vector and builds a dict. The domain rule
            (lat is None AND lon is None AND on_ground == False = transponder silence)
            is applied in process_opensky_message() in silver_job.py (Section 3.3).

        Args:
            raw_states: The 'states' value from the OpenSky API response.
                        May be None (API returns null when no aircraft) or a list
                        of 17-element state vectors.

        Returns:
            Tuple of (aircraft_count, states_compact):
                aircraft_count: Total aircraft in the box (0 if states is None).
                states_compact: List of dicts, one per aircraft, with keys:
                    icao24, callsign, lat, lon, on_ground, last_contact.
        """
        if not raw_states:
            return 0, []

        aircraft_count = len(raw_states)
        compact = []
        for state in raw_states:
            # Guard: skip malformed entries (state must be indexable with >= 9 fields)
            if not isinstance(state, (list, tuple)) or len(state) < 9:
                continue
            compact.append({
                "icao24":       state[_STATE_IDX_ICAO24],
                "callsign":     state[_STATE_IDX_CALLSIGN],
                "lat":          state[_STATE_IDX_LATITUDE],
                "lon":          state[_STATE_IDX_LONGITUDE],
                "on_ground":    state[_STATE_IDX_ON_GROUND],
                "last_contact": state[_STATE_IDX_LAST_CONTACT],
            })

        return aircraft_count, compact

    # ----------------------------------------------------------
    # Raw Payload Builder
    # ----------------------------------------------------------

    def _build_raw_payload(
        self,
        box: dict,
        api_data: dict,
        fetch_timestamp: str,
        mode: str,
        target_callsigns: Optional[list[str]] = None,
    ) -> dict:
        """
        Build the raw_payload dict for one OpenSky bounding box observation.

        Stored verbatim as raw_payload inside the Bronze envelope (Section C.1).
        Carries all fields the Silver Job needs to populate the C.2
        metadata_extension block without consulting external lookup tables —
        keeping the Silver Job stateless and independently testable (§3.3).

        Service Isolation contract:
            aircraft_count:    Structural count — len(states or []).
            states_compact:    5-field compact extraction — no domain interpretation.
            The Silver Job derives: aircraft_density_count, transponder_silence_events,
            and anomaly_score stub from these fields (Section 3.3).

        Args:
            box:              Entry from BOUNDING_BOXES.
            api_data:         Raw OpenSky API response dict.
            fetch_timestamp:  ISO8601 UTC string — wall-clock time of this fetch.
            mode:             "static" | "reactive".
            target_callsigns: For reactive mode — callsigns being tracked.
                              Stored for audit trail; Silver processes all states.

        Returns:
            Raw payload dict to pass to build_bronze_message().
        """
        raw_states = api_data.get("states")
        aircraft_count, states_compact = self._extract_states_compact(raw_states)

        payload: dict = {
            # Box identity
            "bounding_box_id": box["bounding_box_id"],
            "strategic_tag":   box["strategic_tag"],
            "lat_min":         box["lat_min"],
            "lat_max":         box["lat_max"],
            "lon_min":         box["lon_min"],
            "lon_max":         box["lon_max"],

            # Aircraft state summary — Silver applies domain logic
            "aircraft_count":  aircraft_count,
            "states_compact":  states_compact,

            # OpenSky API observation timestamp (Unix epoch) — may differ from
            # fetch_timestamp by a few seconds due to network latency
            "observation_time": api_data.get("time"),

            # Ingestion metadata
            "fetch_timestamp": fetch_timestamp,
            "mode":            mode,
        }

        # Reactive mode — record which callsigns triggered this fetch
        if target_callsigns:
            payload["target_callsigns"] = target_callsigns

        return payload

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(self, raw_payload: dict) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to BRONZE_OPENSKY.

        Partition key: bounding_box_id (bytes) — collocates all observations for
        the same geographic box in one Kafka partition. The Gold Job's keyed-state
        momentum operator keys by (source_name, external_reference_id=bounding_box_id),
        so partition affinity reduces cross-partition state lookups in the Flink
        cluster (Section 4.2B). Same pattern as openweather_producer.py.

        Args:
            raw_payload: Dict produced by _build_raw_payload().
        """
        box_id   = raw_payload["bounding_box_id"]
        lat_min  = raw_payload["lat_min"]
        lat_max  = raw_payload["lat_max"]
        lon_min  = raw_payload["lon_min"]
        lon_max  = raw_payload["lon_max"]

        source_endpoint = (
            f"{OPENSKY_BASE_URL}"
            f"?lamin={lat_min}&lamax={lat_max}&lomin={lon_min}&lomax={lon_max}"
        )

        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
        )
        self._producer.send(
            BRONZE_OPENSKY,
            value=msg,
            key=box_id.encode("utf-8"),
        )
        self._emitted += 1

    # ----------------------------------------------------------
    # Static Mode — one-shot all boxes
    # ----------------------------------------------------------

    def run_static(self) -> int:
        """
        Fetch and emit current aircraft states for all 7 strategic bounding boxes.

        One-shot invocation — Airflow schedules this every 60 s (Section B.7:
        "Pulse Trigger: aircraft density > 30% above 30-day hourly average").
        After fetching all boxes, flushes Kafka and returns. The Airflow DAG task
        handles the 60-second cadence.

        A failure on one box (HTTP error, network timeout) is logged at ERROR
        level and skipped. The run continues with remaining boxes — partial
        data is preferable to a full abort. Empty boxes (0 aircraft) are emitted.

        Returns:
            Total number of Bronze messages emitted.
        """
        run_start = self._emitted
        fetch_ts  = datetime.now(timezone.utc).isoformat()

        logger.info(
            "[opensky] Static run starting — %d boxes",
            len(BOUNDING_BOXES),
        )

        self._ensure_token()
        for box in BOUNDING_BOXES:
            api_data = self._fetch_box(box)
            if api_data is not None:
                raw = self._build_raw_payload(box, api_data, fetch_ts, "static")
                self._emit(raw)
                logger.info(
                    "[opensky] static — %s: emitted (aircraft_count=%d)",
                    box["bounding_box_id"],
                    raw["aircraft_count"],
                )
            time.sleep(self._request_delay)

        run_emitted = self._emitted - run_start
        self._producer.flush()
        logger.info(
            "[opensky] Static run complete — emitted %d / %d boxes",
            run_emitted, len(BOUNDING_BOXES),
        )
        return run_emitted

    # ----------------------------------------------------------
    # Reactive Mode — callsign tracking, bounded window
    # ----------------------------------------------------------

    def run_reactive(
        self,
        target_callsigns: list[str],
        window_minutes: int = REACTIVE_WINDOW_MINUTES,
        poll_interval_sec: int = REACTIVE_POLL_INTERVAL_SEC,
    ) -> int:
        """
        Poll all boxes every 30 s for a 30-min window, tracking specific callsigns.

        Invoked by the RAG agent via the ingestion_triggers Kafka topic when
        intelligence on specific military cargo or VIP aircraft is needed
        (Section 2.4, B.7: "RAG-triggered tracking of specific callsigns").

        All boxes are polled every iteration (unlike OpenWeather reactive mode
        which targets a subset of hotspots). This is because a target callsign's
        position is unknown at trigger time — it could appear in any box.

        The full states_compact list is included in every Bronze message regardless
        of whether a target callsign was found. The target_callsigns list is stored
        in the payload for audit trail and downstream filtering.

        Iteration count: (window_minutes × 60) // poll_interval_sec.
        Default: (30 × 60) // 30 = 60 iterations.

        Args:
            target_callsigns:  List of ICAO24 hex or callsign strings to track.
                               Cannot be empty — caller must supply at least one.
            window_minutes:    Total polling window in minutes (default 30).
            poll_interval_sec: Seconds between polling rounds (default 30).

        Returns:
            Total number of Bronze messages emitted across all iterations.
        """
        if not target_callsigns:
            logger.error(
                "[opensky] reactive — target_callsigns cannot be empty. "
                "Pass at least one callsign or ICAO24 hex string."
            )
            return 0

        total_iterations = max(1, (window_minutes * 60) // poll_interval_sec)
        run_start = self._emitted

        logger.info(
            "[opensky] Reactive run starting — tracking %d callsign(s), "
            "%d iterations × %ds = %d min window",
            len(target_callsigns), total_iterations,
            poll_interval_sec, window_minutes,
        )

        self._ensure_token()
        for iteration in range(1, total_iterations + 1):
            # Refresh token mid-window if near expiry
            self._ensure_token()
            iter_ts = datetime.now(timezone.utc).isoformat()

            logger.info(
                "[opensky] reactive — iteration %d / %d",
                iteration, total_iterations,
            )

            for box in BOUNDING_BOXES:
                api_data = self._fetch_box(box)
                if api_data is not None:
                    raw = self._build_raw_payload(
                        box, api_data, iter_ts, "reactive",
                        target_callsigns=target_callsigns,
                    )
                    self._emit(raw)
                time.sleep(self._request_delay)

            # Skip sleep after the final iteration
            if iteration < total_iterations:
                time.sleep(poll_interval_sec)

        run_emitted = self._emitted - run_start
        self._producer.flush()
        logger.info(
            "[opensky] Reactive run complete — %d messages emitted "
            "across %d iterations",
            run_emitted, total_iterations,
        )
        return run_emitted

    # ----------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------

    def close(self) -> None:
        """
        Flush and close the Kafka producer connection cleanly.

        Must be called in the finally block of main() to prevent message loss —
        kafka-python-ng buffers messages in memory and flush() forces a synchronous
        drain before exit (same pattern as openweather_producer.py).
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[opensky] Producer closed cleanly.")


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(
    mode: str = "static",
    target_callsigns: Optional[list[str]] = None,
) -> None:
    """
    Entry point for Docker container and Airflow BashOperator invocation.

    Modes:
        static   (default) — one-shot fetch of all 7 bounding boxes.
        reactive           — bounded 30-min callsign-tracking loop.

    Usage:
        python -m ingestion.opensky_producer              # static (default)
        python -m ingestion.opensky_producer static       # static (explicit)
        python -m ingestion.opensky_producer reactive "abc123,def456"

    Reactive mode expects ICAO24 hex codes or callsigns as a comma-separated
    string (second argument).

    Args:
        mode:             Operating mode string.
        target_callsigns: Callsign list for reactive mode. Ignored for static.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = OpenSkyProducer()
    try:
        if mode == "reactive":
            if not target_callsigns:
                logger.error(
                    "[opensky] reactive mode requires at least one callsign. "
                    "Pass callsigns as the second argument (comma-separated)."
                )
                sys.exit(1)
            producer.run_reactive(target_callsigns=target_callsigns)
        else:
            producer.run_static()
    finally:
        producer.close()


if __name__ == "__main__":
    _mode = sys.argv[1] if len(sys.argv) > 1 else "static"

    if _mode not in ("static", "reactive"):
        print(
            f"[opensky] Unknown mode '{_mode}'. "
            "Expected 'static' or 'reactive'.",
            file=sys.stderr,
        )
        sys.exit(1)

    _target_callsigns: Optional[list[str]] = None

    if _mode == "reactive":
        if len(sys.argv) < 3:
            print(
                "[opensky] reactive mode requires callsigns as the second argument.\n"
                "Usage: python -m ingestion.opensky_producer reactive "
                "\"abc123,def456\"",
                file=sys.stderr,
            )
            sys.exit(1)
        _target_callsigns = [c.strip() for c in sys.argv[2].split(",") if c.strip()]

    main(_mode, target_callsigns=_target_callsigns)
