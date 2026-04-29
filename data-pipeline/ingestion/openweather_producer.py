"""
OpenWeather Producer — OpenWeatherMap REST API, static polling & reactive mode.

Two operating modes (Section B.6):

  static    (default) — One-shot: fetches current weather for all 10 strategic
                         hotspots and emits one Bronze envelope per hotspot.
                         Designed to be invoked every 10 minutes by an Airflow
                         DAG or Docker scheduled task. Each run captures the
                         latest snapshot; Airflow handles the 10-minute cadence
                         (same one-shot-per-invocation pattern as fred_producer.py
                         and googletrends_producer.py).

  reactive            — On-demand: polls a specified subset of hotspots every
                         REACTIVE_POLL_INTERVAL_SEC (2 min) for a bounded
                         REACTIVE_WINDOW_MINUTES (60 min) window — 30 iterations
                         total. Invoked by the RAG agent via the ingestion_triggers
                         Kafka topic when a weather-related knowledge gap is
                         detected (Section 2.4).

Why one envelope per hotspot (not a batch):
    The Silver Job maps each Bronze message to exactly one Silver Structured
    Metric row (Section C.2). Batching multiple hotspots in one envelope would
    require the Silver Job to unpack and loop — violating the one-envelope-one-
    record contract and making DLQ routing ambiguous. Same rationale as
    fred_producer.py and googletrends_producer.py.

Service Isolation boundary (Section 3.3):
    This producer validates only that the OWM API response is structurally
    present — specifically that the `weather` list and `wind` object are
    non-empty in the response. It emits raw OWM values:
        wind.speed     → raw m/s  (Silver Job converts to knots via × 1.94384)
        weather[0].id  → raw OWM condition code  (Silver Job maps to severity 1–5)
    No unit conversion and no severity mapping are performed here. Computing
    derived values in the producer would violate the transformation boundary
    between ingestion and the Silver processing layer.

Structural guard:
    If the OWM API response is missing `weather` (list) or `wind` (dict),
    the hotspot fetch is logged at ERROR level and skipped — no Bronze message
    is emitted for that hotspot. This prevents malformed Bronze envelopes from
    entering the pipeline.

Partition key: hotspot name (bytes) — ensures all observations for the same
    geographic hotspot land in the same Kafka partition, preserving chronological
    order for the Gold Job's keyed-state momentum calculations (Section 4.2B).
    Same pattern as fred_producer.py partitioning by series_id.

Kafka Target: ingest.bronze.openweather (BRONZE_OPENWEATHER)

References:
    - Section 2.1:  Producer Matrix (OpenWeather row)
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section 2.4:  Reactive Ingestion — On-Demand Tool Calling
    - Section B.6:  OpenWeather technical parameters & strategic hotspots
    - Section C.1:  Bronze Schema
    - Section C.2:  Silver Structured Metric — OpenWeather metadata_extension fields
    - Section 3.2:  Message Envelope & DRY (all Kafka logic in kafka_utils.py)
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.2B: Momentum Block — keyed state per hotspot
    - Section 9.1:  Environment parity — OPENWEATHER_API_KEY from .env
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional

import requests

from config.kafka_topics import BRONZE_OPENWEATHER
from config.settings import OPENWEATHER_API_KEY
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# OpenWeather Constants (Section B.6)
# ==========================================================

OWM_BASE_URL: str = "https://api.openweathermap.org/data/2.5/weather"

# Static mode: one-shot per invocation (Airflow schedules every 10 min).
STATIC_POLL_INTERVAL_SEC: int = 600

# Reactive mode: poll interval and bounded window duration (Section B.6).
# 2-minute cadence × 60-minute window = 30 iterations per reactive activation.
REACTIVE_POLL_INTERVAL_SEC: int = 120
REACTIVE_WINDOW_MINUTES: int = 60

# Trigger threshold constants — defined here so Gate 1 tests can import and
# assert on them as structural invariants. Trigger logic lives in the Gold Job
# (Section 4.2B); these constants are the canonical source-of-truth.
WIND_KNOTS_SHIP_THRESHOLD: int = 50           # wind_speed_knots > 50 → Potential_Shipping_Delay
CONDITION_SEVERITY_DISASTER_THRESHOLD: int = 4  # condition_severity ≥ 4 → Natural_Disaster_Alert

# HTTP request timeout (connect_sec, read_sec).
REQUEST_TIMEOUT_SEC: tuple = (5, 15)

# Delay between consecutive hotspot fetches within one run — avoids
# hammering OWM under rate limits (Section 4.3).
REQUEST_DELAY_SEC: float = 0.3

# OWM units=metric → temperature in Celsius, wind in m/s.
# Silver Job converts wind m/s → knots (× 1.94384). Producer emits raw m/s.
OWM_UNITS: str = "metric"

# Canonical source identifier written into every Bronze envelope.
SOURCE_NAME: str = "openweather"


# ==========================================================
# Strategic Hotspots Matrix (Section B.6)
# ==========================================================
# 10 pre-defined geographic hotspots covering:
#   Semiconductor supply chain  — Taipei, Hsinchu Science Park
#   Tech manufacturing          — Tokyo
#   Active conflict zones       — Kyiv, Tel Aviv
#   Political/military nexus    — Washington D.C.
#   Maritime chokepoints        — Strait of Hormuz, Suez Canal, Port of Houston
#   Agricultural supply         — Ukraine wheat belt
#
# strategic_tag values are the canonical identifiers used by Gold Job triggers:
#   Tech_Supply_Risk         checks: "taiwan_semiconductor", "japan_tech"
#   Potential_Shipping_Delay checks: "strait_of_hormuz", "suez_canal", "port_of_houston"
#
# Coordinates are centroid approximations for the strategic zone — not a
# specific city-centre pin. OWM /weather uses the nearest grid point.

HOTSPOTS: list[dict] = [
    {
        "name":          "Taipei",
        "lat":           25.0330,
        "lon":           121.5654,
        "strategic_tag": "taiwan_semiconductor",
    },
    {
        "name":          "Hsinchu_Science_Park",
        "lat":           24.7881,
        "lon":           120.9971,
        "strategic_tag": "taiwan_semiconductor",
    },
    {
        "name":          "Tokyo",
        "lat":           35.6762,
        "lon":           139.6503,
        "strategic_tag": "japan_tech",
    },
    {
        "name":          "Kyiv",
        "lat":           50.4501,
        "lon":           30.5234,
        "strategic_tag": "ukraine_conflict",
    },
    {
        "name":          "Tel_Aviv",
        "lat":           32.0853,
        "lon":           34.7818,
        "strategic_tag": "middle_east_conflict",
    },
    {
        "name":          "Washington_DC",
        "lat":           38.9072,
        "lon":          -77.0369,
        "strategic_tag": "us_capital",
    },
    {
        "name":          "Strait_of_Hormuz",
        "lat":           26.5687,
        "lon":           56.2610,
        "strategic_tag": "strait_of_hormuz",
    },
    {
        "name":          "Suez_Canal",
        "lat":           30.0444,
        "lon":           32.5498,
        "strategic_tag": "suez_canal",
    },
    {
        "name":          "Port_of_Houston",
        "lat":           29.7604,
        "lon":          -95.3698,
        "strategic_tag": "port_of_houston",
    },
    {
        "name":          "Ukraine_Wheat_Belt",
        "lat":           49.0275,
        "lon":           31.4828,
        "strategic_tag": "wheat_region",
    },
]


# ==========================================================
# Producer
# ==========================================================

class OpenWeatherProducer:
    """
    REST-based producer for OpenWeatherMap current weather data.

    Two operating modes (Section B.6):
      static   — one-shot fetch of all 10 hotspots; called every 10 min by
                 Airflow (same invocation pattern as fred_producer.py and
                 googletrends_producer.py).
      reactive — bounded loop: polls specified hotspots every 2 min for 60 min.
                 Triggered by the RAG agent via ingestion_triggers (Section 2.4).

    Emits to: ingest.bronze.openweather (BRONZE_OPENWEATHER)
    """

    def __init__(self) -> None:
        if not OPENWEATHER_API_KEY:
            logger.warning(
                "[openweather] OPENWEATHER_API_KEY not set in .env — "
                "all API requests will return HTTP 401. Set the key in .env."
            )
        self._producer = make_producer()
        self._emitted: int = 0

    # ----------------------------------------------------------
    # OWM REST Fetch
    # ----------------------------------------------------------

    def _fetch_hotspot(self, hotspot: dict) -> Optional[dict]:
        """
        Fetch current weather for one hotspot from the OWM /weather endpoint.

        Returns the raw OWM JSON response dict on success, or None if the
        request fails or the response is missing structurally required fields.

        Structural guard (Service Isolation §3.3):
            Validates that `weather` (non-empty list) and `wind` (non-empty dict)
            are present in the response. These are the only two structural fields
            the producer enforces — all other validation (unit conversion, severity
            mapping, lat/lon presence) happens in the Silver Job. If either field
            is absent, the hotspot is skipped and no Bronze message is emitted.

        Args:
            hotspot: Entry from HOTSPOTS — must have 'name', 'lat', 'lon'.

        Returns:
            Raw OWM response dict, or None on fetch or structural failure.
        """
        name = hotspot["name"]
        params = {
            "lat":   hotspot["lat"],
            "lon":   hotspot["lon"],
            "units": OWM_UNITS,
            "appid": OPENWEATHER_API_KEY,
        }

        try:
            response, duration_ms = timed_request(
                lambda: requests.get(OWM_BASE_URL, params=params, timeout=REQUEST_TIMEOUT_SEC)
            )
            response.raise_for_status()
            data = response.json()

        except requests.HTTPError as exc:
            logger.error(
                "[openweather] %s — HTTP error: %s — skipping hotspot",
                name, exc,
            )
            return None
        except requests.RequestException as exc:
            logger.error(
                "[openweather] %s — Network error: %s — skipping hotspot",
                name, exc,
            )
            return None
        except ValueError as exc:
            logger.error(
                "[openweather] %s — JSON parse error: %s — skipping hotspot",
                name, exc,
            )
            return None

        # Structural guard: `weather` must be a non-empty list.
        weather_list = data.get("weather")
        if not weather_list or not isinstance(weather_list, list):
            logger.error(
                "[openweather] %s — OWM response missing 'weather' list — "
                "skipping hotspot (structural guard, Section 3.3)",
                name,
            )
            return None

        # Structural guard: `wind` must be a non-empty dict.
        wind_obj = data.get("wind")
        if not wind_obj or not isinstance(wind_obj, dict):
            logger.error(
                "[openweather] %s — OWM response missing 'wind' dict — "
                "skipping hotspot (structural guard, Section 3.3)",
                name,
            )
            return None

        logger.debug(
            "[openweather] %s — fetched OK (duration=%dms)", name, duration_ms,
        )
        return data

    # ----------------------------------------------------------
    # Raw Payload Builder
    # ----------------------------------------------------------

    def _build_raw_payload(
        self,
        hotspot: dict,
        owm_data: dict,
        fetch_timestamp: str,
        mode: str,
    ) -> dict:
        """
        Build the raw_payload dict for one OpenWeather observation.

        Stored verbatim as raw_payload inside the Bronze envelope (Section C.1).
        Carries all fields the Silver Job needs to populate the C.2
        metadata_extension block without consulting external lookup tables —
        keeping the Silver Job stateless and independently testable (§3.3).

        Service Isolation contract:
            wind_speed_ms:  raw OWM `wind.speed` in m/s.
                            Silver Job converts to knots via × 1.94384.
            weather_id:     raw OWM `weather[0].id` condition code.
                            Silver Job maps to severity 1–5 via
                            CONDITION_ID_TO_SEVERITY.
            official_alerts: raw OWM `alerts` list (empty list if absent —
                             free tier does not always include alert data).

        Args:
            hotspot:         Entry from HOTSPOTS.
            owm_data:        Raw OWM API response dict (structurally validated
                             by _fetch_hotspot — 'weather' and 'wind' present).
            fetch_timestamp: ISO8601 UTC string — wall-clock time of this fetch.
            mode:            "static" | "reactive".

        Returns:
            Raw payload dict to pass to build_bronze_message().
        """
        main_block    = owm_data.get("main", {})
        wind_block    = owm_data.get("wind", {})
        weather_entry = owm_data["weather"][0]  # presence validated by _fetch_hotspot

        payload: dict = {
            # Hotspot identity
            "hotspot_name":  hotspot["name"],
            "lat":           hotspot["lat"],
            "lon":           hotspot["lon"],
            "strategic_tag": hotspot["strategic_tag"],

            # Temperature in Celsius (current_value at Silver/Gold layer)
            "temperature_celsius": float(main_block.get("temp", 0.0)),

            # Wind — raw m/s; Silver converts to knots (Section 3.3)
            "wind_speed_ms": float(wind_block.get("speed", 0.0)),

            # Atmospheric
            "pressure_hpa": float(main_block.get("pressure", 0.0)),
            "humidity_pct": float(main_block.get("humidity", 0.0)),

            # OWM condition code — Silver maps to severity 1–5 (Section 3.3)
            "weather_id":          int(weather_entry.get("id", 0)),
            "weather_main":        str(weather_entry.get("main", "")),
            "weather_description": str(weather_entry.get("description", "")),

            # Official weather alerts (empty list if absent on free tier)
            "official_alerts": owm_data.get("alerts", []),

            # Ingestion metadata
            "fetch_timestamp": fetch_timestamp,
            "mode":            mode,
        }

        # Optional fields — included only when present in the OWM response
        if "gust" in wind_block:
            payload["wind_gust_ms"] = float(wind_block["gust"])
        if "deg" in wind_block:
            payload["wind_direction_deg"] = int(wind_block["deg"])

        return payload

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(self, raw_payload: dict) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to BRONZE_OPENWEATHER.

        Partition key: hotspot_name (bytes) — collocates all observations for
        the same geographic hotspot in one Kafka partition. The Gold Job's
        keyed-state momentum operator keys by (source_name,
        external_reference_id=hotspot_name), so partition affinity reduces
        cross-partition state lookups in the Flink cluster (Section 4.2B).
        Same pattern as fred_producer.py partitioning by series_id.

        Args:
            raw_payload: Dict produced by _build_raw_payload().
        """
        name = raw_payload["hotspot_name"]
        lat  = raw_payload["lat"]
        lon  = raw_payload["lon"]

        source_endpoint = f"{OWM_BASE_URL}?lat={lat}&lon={lon}&units={OWM_UNITS}"

        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
        )
        self._producer.send(
            BRONZE_OPENWEATHER,
            value=msg,
            key=name.encode("utf-8"),
        )
        self._emitted += 1

    # ----------------------------------------------------------
    # Static Mode — one-shot all hotspots
    # ----------------------------------------------------------

    def run_static(self) -> int:
        """
        Fetch and emit current weather for all 10 strategic hotspots.

        One-shot invocation — Airflow schedules this every 10 min (Section B.6:
        "Static Pulse: Every 10 min for all predefined bounding boxes"). After
        fetching all hotspots, flushes Kafka and returns. The Airflow DAG task
        handles the 10-minute cadence.

        Error handling: a failure on one hotspot (HTTP error, network timeout,
        or structural guard failure) is logged at ERROR level and skipped. The
        run continues with remaining hotspots — partial data is preferable to
        a full abort.

        Returns:
            Total number of Bronze messages emitted.
        """
        run_start = self._emitted
        fetch_ts  = datetime.now(timezone.utc).isoformat()

        logger.info(
            "[openweather] Static run starting — %d hotspots",
            len(HOTSPOTS),
        )

        for hotspot in HOTSPOTS:
            owm_data = self._fetch_hotspot(hotspot)
            if owm_data is not None:
                raw = self._build_raw_payload(hotspot, owm_data, fetch_ts, "static")
                self._emit(raw)
                logger.info(
                    "[openweather] static — %s: emitted "
                    "(temp=%.1f°C, wind=%.1f m/s, cond_id=%d)",
                    hotspot["name"],
                    raw["temperature_celsius"],
                    raw["wind_speed_ms"],
                    raw["weather_id"],
                )
            time.sleep(REQUEST_DELAY_SEC)

        run_emitted = self._emitted - run_start
        self._producer.flush()
        logger.info(
            "[openweather] Static run complete — emitted %d / %d hotspots",
            run_emitted, len(HOTSPOTS),
        )
        return run_emitted

    # ----------------------------------------------------------
    # Reactive Mode — bounded polling window
    # ----------------------------------------------------------

    def run_reactive(
        self,
        hotspot_names: list[str],
        window_minutes: int = REACTIVE_WINDOW_MINUTES,
        poll_interval_sec: int = REACTIVE_POLL_INTERVAL_SEC,
    ) -> int:
        """
        Poll a subset of hotspots every 2 min for a 60-min window on RAG trigger.

        Invoked by the RAG agent via the ingestion_triggers Kafka topic when a
        weather-related knowledge gap is detected during inference (Section 2.4).
        Example: Potential_Shipping_Delay fires for Strait of Hormuz; agent
        reactively escalates polling on Suez_Canal to assess regional scope.

        Iteration count: (window_minutes × 60) // poll_interval_sec.
        Default: (60 × 60) // 120 = 30 iterations.

        Unknown hotspot names are logged and skipped — the run continues with
        valid hotspots. If all names are invalid, the run aborts early.

        Args:
            hotspot_names:     List of hotspot 'name' values from HOTSPOTS.
            window_minutes:    Total polling window in minutes (default 60).
            poll_interval_sec: Seconds between polling rounds (default 120).

        Returns:
            Total number of Bronze messages emitted across all iterations.
        """
        hotspot_map = {h["name"]: h for h in HOTSPOTS}

        target_hotspots: list[dict] = []
        for name in hotspot_names:
            if name in hotspot_map:
                target_hotspots.append(hotspot_map[name])
            else:
                logger.warning(
                    "[openweather] reactive — hotspot '%s' not in HOTSPOTS — "
                    "skipping. Valid names: %s",
                    name, [h["name"] for h in HOTSPOTS],
                )

        if not target_hotspots:
            logger.error(
                "[openweather] reactive — no valid hotspots resolved from %s — "
                "aborting reactive run",
                hotspot_names,
            )
            return 0

        total_iterations = max(1, (window_minutes * 60) // poll_interval_sec)
        run_start = self._emitted

        logger.info(
            "[openweather] Reactive run starting — %d hotspot(s), "
            "%d iterations × %ds = %d min window",
            len(target_hotspots), total_iterations,
            poll_interval_sec, window_minutes,
        )

        for iteration in range(1, total_iterations + 1):
            iter_ts = datetime.now(timezone.utc).isoformat()

            logger.info(
                "[openweather] reactive — iteration %d / %d",
                iteration, total_iterations,
            )

            for hotspot in target_hotspots:
                owm_data = self._fetch_hotspot(hotspot)
                if owm_data is not None:
                    raw = self._build_raw_payload(hotspot, owm_data, iter_ts, "reactive")
                    self._emit(raw)
                time.sleep(REQUEST_DELAY_SEC)

            # Skip sleep after the final iteration
            if iteration < total_iterations:
                time.sleep(poll_interval_sec)

        run_emitted = self._emitted - run_start
        self._producer.flush()
        logger.info(
            "[openweather] Reactive run complete — %d messages emitted "
            "across %d iterations for %d hotspot(s)",
            run_emitted, total_iterations, len(target_hotspots),
        )
        return run_emitted

    # ----------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------

    def close(self) -> None:
        """
        Flush and close the Kafka producer connection cleanly.

        Must be called in the finally block of main() to prevent message
        loss — kafka-python-ng buffers messages in memory and flush()
        forces a synchronous drain before exit (same pattern as
        fred_producer.py and googletrends_producer.py).
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[openweather] Producer closed cleanly.")


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(
    mode: str = "static",
    hotspot_names: Optional[list[str]] = None,
) -> None:
    """
    Entry point for Docker container and Airflow BashOperator invocation.

    Modes:
        static   (default) — one-shot fetch of all 10 hotspots.
        reactive           — bounded 60-min polling on specified hotspots.

    Usage:
        python -m ingestion.openweather_producer              # static (default)
        python -m ingestion.openweather_producer static       # static (explicit)
        python -m ingestion.openweather_producer reactive "Taipei,Suez_Canal"

    Reactive mode expects hotspot names as a comma-separated string (second arg).
    Names must exactly match the 'name' field in the HOTSPOTS list.

    Args:
        mode:          Operating mode string.
        hotspot_names: Hotspot name list for reactive mode. Ignored for static.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = OpenWeatherProducer()
    try:
        if mode == "reactive":
            if not hotspot_names:
                logger.error(
                    "[openweather] reactive mode requires at least one hotspot name. "
                    "Pass names as the second argument (comma-separated). "
                    "Valid names: %s",
                    [h["name"] for h in HOTSPOTS],
                )
                sys.exit(1)
            producer.run_reactive(hotspot_names=hotspot_names)
        else:
            producer.run_static()
    finally:
        producer.close()


if __name__ == "__main__":
    _mode = sys.argv[1] if len(sys.argv) > 1 else "static"

    if _mode not in ("static", "reactive"):
        print(
            f"[openweather] Unknown mode '{_mode}'. "
            "Expected 'static' or 'reactive'.",
            file=sys.stderr,
        )
        sys.exit(1)

    _hotspot_names: Optional[list[str]] = None

    if _mode == "reactive":
        if len(sys.argv) < 3:
            print(
                "[openweather] reactive mode requires hotspot names as the second argument.\n"
                "Usage: python -m ingestion.openweather_producer reactive "
                "\"Taipei,Suez_Canal\"",
                file=sys.stderr,
            )
            sys.exit(1)
        _hotspot_names = [n.strip() for n in sys.argv[2].split(",") if n.strip()]

    main(_mode, hotspot_names=_hotspot_names)
