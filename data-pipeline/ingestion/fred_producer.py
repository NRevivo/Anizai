"""
FRED Producer — REST API, daily polling.

Polls the Federal Reserve Economic Data (FRED) API for 9 key economic
indicators and emits one Bronze envelope per observation to the
ingest.bronze.fred Kafka topic.

Two operating modes (Section 2.3 / B.3):
  - pulse    (default) — fetches the latest PULSE_LOOKBACK_OBS observations
                         per series. Designed to be called daily by an
                         Airflow DAG. Captures any data-publication lag
                         (FRED often publishes prior-period revisions).
  - backfill            — fetches the full 10-year history for all 9 series.
                         One-time operation; safe to re-run (Kafka retains 7
                         days and momentum_vault is idempotent on metric_id).

Why one message per observation (not one per series batch):
    The Silver Job maps each Bronze message to exactly one
    Silver Structured Metric row (Section C.2). Batching observations
    inside a single envelope would require the Silver Job to unpack and
    loop — violating the one-envelope-one-record contract and making the
    DLQ routing ambiguous.

Partition key: series_id — ensures all observations for the same
    indicator land in the same Kafka partition, preserving chronological
    order for the Gold Job's keyed-state momentum calculations (Section 4.2B).

FRED API details (Section B.3):
    Base URL:  https://api.stlouisfed.org/fred
    Endpoint:  /series/observations
    Auth:      api_key query parameter (FRED_API_KEY in .env)
    No API key: FRED allows anonymous access at reduced rate limits.
                The producer warns but continues without a key.

Kafka Target: ingest.bronze.fred (BRONZE_FRED)
Sprint Priority: 2 — establishes the scheduled REST polling pattern.

References:
    - Section 2.1:  Producer Matrix (FRED row)
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section B.3:  FRED technical parameters & Indicator Matrix
    - Section C.1:  Bronze Schema (build_bronze_message wraps every payload)
    - Section C.2:  Silver Structured Metric — FRED metadata_extension fields
    - Section 3.2:  Message Envelope & DRY (all Kafka logic in kafka_utils.py)
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.2B: Momentum Block — keyed state per series_id
    - Section 9.1:  Environment parity — FRED_API_KEY from .env
"""

from __future__ import annotations

import logging
import sys
import time
from datetime import date, timedelta
from typing import Optional

import requests

from config.kafka_topics import BRONZE_FRED
from config.settings import FRED_API_KEY
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# ==========================================================
# FRED API Constants (Section B.3)
# ==========================================================

FRED_BASE_URL = "https://api.stlouisfed.org/fred"
FRED_OBSERVATIONS_ENDPOINT = f"{FRED_BASE_URL}/series/observations"

# Number of most-recent observations to fetch per series in pulse mode.
# 10 covers any FRED publication lag (some monthly series are published
# 3–4 weeks after the reference period, and FRED often issues revisions).
PULSE_LOOKBACK_OBS = 10

# Note on zero momentum deltas for stable / monthly series:
# Monthly series (FEDFUNDS, CPIAUCSL, UNRATE, CSUSHPINSA) that have not
# changed value within the 24h/7d/30d lookback windows will correctly
# produce change_24h = change_7d = change_30d = 0.0. This is expected
# behaviour — compute_momentum_block returns zero delta when the reference
# observation has the same value as the current one (e.g. FEDFUNDS held
# flat by the Fed for multiple months). Zero deltas are not a bug; they
# accurately reflect that no movement occurred in the window.

# Years of history to fetch in backfill mode (Section B.3)
BACKFILL_YEARS = 10

# Inter-series delay in seconds — avoids hitting FRED's rate limit.
# FRED public API limit is 120 requests/minute; 0.6s keeps us well under.
REQUEST_DELAY_SEC = 0.6

# Canonical source identifier used in every Bronze envelope
SOURCE_NAME = "fred"


# ==========================================================
# FRED Indicator Matrix (Section B.3)
# ==========================================================
# Each entry maps a FRED series_id to metadata that the Silver Job needs
# for the metadata_extension block (Section C.2 — FRED row in the table).
#
# release_priority (1–5): importance to the RAG forecasting model.
#   5 = critical (FEDFUNDS, CPIAUCSL, DCOILWTICO, VIXCLS, T10Y2Y)
#   4 = high     (UNRATE, GOLDAMGBD228NLBM)
#   3 = moderate (CSUSHPINSA, GASREGW)
#
# Flink automation triggers (Section B.3):
#   T10Y2Y < 0           → Market_Anomaly, impact_level: 5
#   VIXCLS > 30          → impact_level: 5
#   price variance > 5% in 24h → flag for immediate grounding

INDICATORS: dict[str, dict] = {
    "FEDFUNDS": {
        "description":        "Federal Funds Effective Rate",
        "category":           "Macro",
        "unit":               "percent",
        "release_priority":   5,
        "seasonal_adjustment": "Not Seasonally Adjusted",
    },
    "CPIAUCSL": {
        "description":        "Consumer Price Index: All Items (Urban Consumers)",
        "category":           "Macro",
        "unit":               "Index 1982-1984=100",
        "release_priority":   5,
        "seasonal_adjustment": "Seasonally Adjusted",
    },
    "UNRATE": {
        "description":        "Unemployment Rate",
        "category":           "Macro",
        "unit":               "percent",
        "release_priority":   4,
        "seasonal_adjustment": "Seasonally Adjusted",
    },
    "CSUSHPINSA": {
        "description":        "S&P/Case-Shiller U.S. National Home Price Index",
        "category":           "Real Estate",
        "unit":               "Index Jan 2000=100",
        "release_priority":   3,
        "seasonal_adjustment": "Not Seasonally Adjusted",
    },
    "DCOILWTICO": {
        "description":        "Crude Oil Prices: West Texas Intermediate (WTI)",
        "category":           "Commodities",
        "unit":               "USD per Barrel",
        "release_priority":   5,
        "seasonal_adjustment": "Not Seasonally Adjusted",
    },
    "GASREGW": {
        "description":        "U.S. Regular Conventional Gas Price",
        "category":           "Commodities",
        "unit":               "USD per Gallon",
        "release_priority":   3,
        "seasonal_adjustment": "Not Seasonally Adjusted",
    },
    "DHHNGSP": {
        "description":        "Henry Hub Natural Gas Spot Price",
        "category":           "Commodities",
        "unit":               "USD per MMBtu",
        "release_priority":   4,
        "seasonal_adjustment": "Not Seasonally Adjusted",
    },
    "VIXCLS": {
        "description":        "CBOE Volatility Index: VIX",
        "category":           "Risk",
        "unit":               "Index",
        "release_priority":   5,
        "seasonal_adjustment": "Not Seasonally Adjusted",
    },
    "T10Y2Y": {
        "description":        "10-Year Treasury Minus 2-Year Treasury Yield Spread",
        "category":           "Risk",
        "unit":               "percent",
        "release_priority":   5,
        "seasonal_adjustment": "Not Seasonally Adjusted",
    },
}


# ==========================================================
# Producer
# ==========================================================

class FREDProducer:
    """
    Scheduled REST producer for FRED economic indicators.

    Unlike Polymarket (persistent WebSocket daemon), FRED data is published
    on a daily/monthly cadence. This producer is designed to be invoked once
    per Airflow DAG run (Section 2.3). It fetches observations, emits Bronze
    messages, flushes Kafka, and exits cleanly.

    Two modes:
      pulse    — latest PULSE_LOOKBACK_OBS observations per series (daily)
      backfill — full 10-year history per series (one-time)

    Why not a long-running loop: FRED's data cadence is daily at fastest.
    Running a persistent container for a source that updates once per day
    wastes Docker resources. Airflow schedules and retries this producer
    without requiring a daemon process (Section 2.3 / 8.2).

    Emits to: ingest.bronze.fred (BRONZE_FRED)
    """

    def __init__(self) -> None:
        if not FRED_API_KEY:
            logger.warning(
                "[fred] FRED_API_KEY not set in .env — running with anonymous access "
                "(rate-limited to 120 req/min). Set FRED_API_KEY for higher limits."
            )
        self._producer = make_producer()
        self._emitted  = 0   # running count for the summary log at exit

    # ----------------------------------------------------------
    # FRED REST Fetch
    # ----------------------------------------------------------

    def _fetch_observations(
        self,
        series_id: str,
        observation_start: Optional[str] = None,
        sort_order: str = "desc",
        limit: Optional[int] = None,
    ) -> list[dict]:
        """
        Fetch observations for one FRED series from the REST API.

        Why sort_order=desc with a limit for pulse: requesting the N most
        recent observations is cheaper than requesting all observations and
        filtering client-side. The observations are reversed to chronological
        order before returning so callers always see oldest-first.

        Args:
            series_id:         FRED series identifier (e.g., "FEDFUNDS").
            observation_start: ISO date string "YYYY-MM-DD" for backfill.
                               None = no lower bound (FRED returns from 1960s).
            sort_order:        "desc" (default, newest first) or "asc".
            limit:             Max observations to return. None = no limit.

        Returns:
            List of raw FRED observation dicts from the API response,
            oldest-first. Missing-value observations (value == ".") are
            excluded — FRED uses "." as a sentinel for unreported periods.

        Raises:
            requests.HTTPError: On non-2xx responses.
            requests.RequestException: On network errors.
        """
        params: dict = {
            "series_id":  series_id,
            "file_type":  "json",
            "sort_order": sort_order,
        }
        if FRED_API_KEY:
            params["api_key"] = FRED_API_KEY
        if observation_start:
            params["observation_start"] = observation_start
        if limit is not None:
            params["limit"] = limit

        response, duration_ms = timed_request(
            lambda: requests.get(
                FRED_OBSERVATIONS_ENDPOINT, params=params, timeout=20
            )
        )
        response.raise_for_status()

        data = response.json()
        raw_observations = data.get("observations", [])

        # Filter out missing-value sentinel "." — FRED uses this for periods
        # where data was not reported or has been revised away.
        valid = [obs for obs in raw_observations if obs.get("value", ".") != "."]

        logger.debug(
            "[fred] %s — fetched %d observations (%d valid, duration=%dms)",
            series_id, len(raw_observations), len(valid), duration_ms,
        )

        # Return oldest-first regardless of sort_order used for the request.
        # desc gives newest-first from the API; reverse to chronological.
        if sort_order == "desc":
            valid.reverse()

        return valid

    # ----------------------------------------------------------
    # Bronze Payload Builder
    # ----------------------------------------------------------

    def _build_raw_payload(
        self,
        series_id: str,
        obs: dict,
        meta: dict,
        fetch_mode: str,
    ) -> dict:
        """
        Construct the raw_payload dict for one FRED observation.

        This dict is stored verbatim as raw_payload inside the Bronze
        envelope (Section C.1). It carries everything the Silver Job needs
        to populate the metadata_extension block (Section C.2 — FRED row):
        series_id, observation_date, release_priority, seasonal_adjustment.

        Why include category/description/unit at Bronze layer: the Silver Job
        should not need to look up the indicator matrix to produce a valid
        Silver record. Embedding these fields in Bronze makes the Silver Job
        stateless and independently testable (Section 3.3 Service Isolation).

        Args:
            series_id:  FRED series ID (e.g., "FEDFUNDS").
            obs:        Single observation dict from the FRED API response.
            meta:       Entry from INDICATORS dict for this series.
            fetch_mode: "pulse" or "backfill".

        Returns:
            Raw payload dict to pass to build_bronze_message().
        """
        return {
            "series_id":          series_id,
            "observation_date":   obs["date"],
            "value":              float(obs["value"]),
            "realtime_start":     obs.get("realtime_start", ""),
            "realtime_end":       obs.get("realtime_end", ""),
            "category":           meta["category"],
            "description":        meta["description"],
            "unit":               meta["unit"],
            "release_priority":   meta["release_priority"],
            "seasonal_adjustment": meta["seasonal_adjustment"],
            "fetch_mode":         fetch_mode,
        }

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(self, raw_payload: dict) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to ingest.bronze.fred.

        Partition key: series_id — ensures all observations for the same
        indicator land in the same Kafka partition. The Gold Job's keyed-state
        momentum operator keys by series_id, so partition affinity reduces
        cross-partition state lookups in the Flink cluster (Section 4.2B).

        Args:
            raw_payload: Dict produced by _build_raw_payload().
        """
        series_id = raw_payload["series_id"]
        source_endpoint = (
            f"{FRED_OBSERVATIONS_ENDPOINT}?series_id={series_id}"
        )
        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
        )
        self._producer.send(BRONZE_FRED, value=msg, key=series_id)
        self._emitted += 1

    # ----------------------------------------------------------
    # Pulse Mode — daily polling
    # ----------------------------------------------------------

    def run_pulse(self) -> int:
        """
        Fetch and emit the latest PULSE_LOOKBACK_OBS observations per series.

        Designed to be called daily by an Airflow DAG (Section 2.3). Fetches
        the 10 most recent observations for each indicator to capture any
        data-publication lag or late revisions. Duplicate observations that
        were already emitted in a prior pulse run are idempotent at the
        momentum_vault level (ON CONFLICT DO NOTHING on metric_id).

        Returns:
            Total number of Bronze messages emitted.
        """
        logger.info(
            "[fred] Pulse run starting — fetching last %d observations "
            "for %d series",
            PULSE_LOOKBACK_OBS, len(INDICATORS),
        )

        for series_id, meta in INDICATORS.items():
            try:
                observations = self._fetch_observations(
                    series_id=series_id,
                    sort_order="desc",
                    limit=PULSE_LOOKBACK_OBS,
                )
                for obs in observations:
                    self._emit(self._build_raw_payload(series_id, obs, meta, "pulse"))

                logger.info(
                    "[fred] %s — emitted %d observations (pulse)",
                    series_id, len(observations),
                )

            except requests.HTTPError as exc:
                logger.error(
                    "[fred] %s — HTTP error: %s — skipping series",
                    series_id, exc,
                )
            except requests.RequestException as exc:
                logger.error(
                    "[fred] %s — Network error: %s — skipping series",
                    series_id, exc,
                )
            except (KeyError, ValueError) as exc:
                logger.error(
                    "[fred] %s — Data parsing error: %s — skipping series",
                    series_id, exc,
                )
            finally:
                # Rate-limit guard — applies even after errors to protect the
                # next series from hitting the FRED API too quickly.
                time.sleep(REQUEST_DELAY_SEC)

        self._producer.flush()
        logger.info("[fred] Pulse run complete — emitted %d messages total", self._emitted)
        return self._emitted

    # ----------------------------------------------------------
    # Backfill Mode — one-time historical load
    # ----------------------------------------------------------

    def run_backfill(self) -> int:
        """
        Fetch and emit the full 10-year observation history for all 9 series.

        One-time operation (Section B.3: "Backfill: One-time, last 10 years").
        Safe to re-run — the momentum_vault persistence layer uses
        ON CONFLICT (metric_id, timestamp_utc) DO NOTHING.

        Uses sort_order=asc (oldest-first) so Kafka partitions receive
        observations in chronological order. This ensures the Gold Job's
        keyed-state history is populated in the correct sequence — out-of-order
        observations would cause incorrect momentum delta calculations.

        Returns:
            Total number of Bronze messages emitted.
        """
        start_date = (
            date.today() - timedelta(days=BACKFILL_YEARS * 365)
        ).isoformat()

        logger.info(
            "[fred] Backfill run starting — fetching from %s for %d series",
            start_date, len(INDICATORS),
        )

        for series_id, meta in INDICATORS.items():
            try:
                observations = self._fetch_observations(
                    series_id=series_id,
                    observation_start=start_date,
                    sort_order="asc",
                    limit=None,   # No limit — fetch complete history
                )
                for obs in observations:
                    self._emit(self._build_raw_payload(series_id, obs, meta, "backfill"))

                logger.info(
                    "[fred] %s — emitted %d observations (backfill)",
                    series_id, len(observations),
                )

            except requests.HTTPError as exc:
                logger.error(
                    "[fred] %s — HTTP error during backfill: %s — skipping series",
                    series_id, exc,
                )
            except requests.RequestException as exc:
                logger.error(
                    "[fred] %s — Network error during backfill: %s — skipping series",
                    series_id, exc,
                )
            except (KeyError, ValueError) as exc:
                logger.error(
                    "[fred] %s — Data parsing error during backfill: %s — skipping series",
                    series_id, exc,
                )
            finally:
                time.sleep(REQUEST_DELAY_SEC)

        self._producer.flush()
        logger.info(
            "[fred] Backfill run complete — emitted %d messages total", self._emitted
        )
        return self._emitted

    # ----------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------

    def close(self) -> None:
        """
        Flush and close the Kafka producer connection cleanly.

        Must be called in the finally block of main() to prevent message
        loss — kafka-python-ng buffers messages in memory and flush()
        forces a synchronous drain before exit (same pattern as
        polymarket_producer.py).
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[fred] Producer closed cleanly.")


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(mode: str = "pulse") -> None:
    """
    Entry point for Docker container and Airflow BashOperator invocation.

    Modes:
        pulse    (default) — daily polling of latest observations.
        backfill           — one-time 10-year historical load.

    Usage:
        python -m ingestion.fred_producer              # pulse (default)
        python -m ingestion.fred_producer backfill     # backfill
        python -m ingestion.fred_producer pulse        # pulse (explicit)

    The Airflow DAG calls this via BashOperator or PythonOperator (Section 6A.1).
    The Docker container CMD defaults to pulse for the daily scheduled run.
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = FREDProducer()
    try:
        if mode == "backfill":
            producer.run_backfill()
        else:
            producer.run_pulse()
    finally:
        producer.close()


if __name__ == "__main__":
    _mode = sys.argv[1] if len(sys.argv) > 1 else "pulse"
    if _mode not in ("pulse", "backfill"):
        print(f"[fred] Unknown mode '{_mode}'. Expected 'pulse' or 'backfill'.", file=sys.stderr)
        sys.exit(1)
    main(_mode)
