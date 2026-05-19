"""
Google Trends Producer — Pytrends wrapper, scheduled polling & reactive mode.

Three operating modes (Section B.5 — mirrors fred_producer.py two-mode pattern):

  static    (default) — Daily run: fetches up to MAX_TRENDING_PER_GEO trending
                         topics for each of the 4 monitored geographies
                         (US, UK, IL, DE). Emits one Bronze envelope per
                         (keyword, geo) pair. Interest score is derived from
                         trending rank: rank 1 → 100.0, rank 50 → 2.0
                         (see "Why rank-to-100 scoring" below).

  reactive            — On-demand: fetches 7-day Interest Over Time for a
                         specific keyword list and target geo. Invoked by the
                         RAG agent via the ingestion_triggers Kafka topic
                         (Section 2.4). Emits one Bronze envelope per
                         (keyword, date) pair with the actual 0-100 Google
                         Trends interest score.

  backfill            — One-time: fetches 5-year Interest Over Time for
                         BACKFILL_KEYWORDS across all 4 monitored geos.
                         Emits one Bronze envelope per (keyword, date, geo)
                         triplet. Safe to re-run — momentum_vault uses
                         ON CONFLICT (metric_id, timestamp_utc) DO NOTHING.

Why one envelope per (keyword, geo) / (keyword, date, geo):
    The Silver Job maps each Bronze message to exactly one Silver Structured
    Metric row (Section C.2). Batching multiple keywords in one envelope
    would require the Silver Job to unpack and loop — violating the
    one-envelope-one-record contract and making DLQ routing ambiguous.
    This is the same rationale as fred_producer.py.

Why rank-to-100 scoring for static mode:
    Pytrends' trending_searches() returns keyword text only — no raw
    Google Trends interest score. To produce a numeric value compatible
    with momentum_vault and the Public_Hype_Alert trigger (Section B.5:
    score delta > 50 in 24 h), we map rank position linearly to 0-100:
        rank_index 0  (rank 1,  most trending)  → 100.0
        rank_index 49 (rank 50, least trending) →   2.0
        score = max(0.0, 100.0 - rank_index * 2.0)
    A keyword that jumps from rank 50 (score = 2) to rank 1 (score = 100)
    in 24 hours produces change_24h = +98 > 50 → Public_Hype_Alert fires.
    This is semantically correct: sudden appearance in the top trending
    list is exactly the hype spike the system is designed to detect.

Why backfill processes one keyword at a time (PYTRENDS_BATCH_SIZE = 1):
    Pytrends normalises Interest Over Time scores relative to the highest-
    interest keyword in the batch. Processing solo gives each keyword a
    true 0-100 scale independent of its co-batched peers. This ensures
    backfill scores are comparable to reactive-mode scores (which also
    process one keyword at a time).

Why no API key:
    Pytrends wraps Google Trends' unofficial web API using cookie-based
    authentication. No developer credentials are required (Section B.5).

Partition key: keyword (bytes) — ensures all observations for the same
    keyword land in the same Kafka partition, preserving chronological order
    for the Gold Job's keyed-state momentum calculations (Section 4.2B).

Kafka Target: ingest.bronze.googletrends (BRONZE_GOOGLETRENDS)

References:
    - Section 2.1:  Producer Matrix (Google Trends row)
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section 2.4:  Reactive Ingestion — On-Demand Tool Calling
    - Section B.5:  Google Trends technical parameters
    - Section C.1:  Bronze Schema
    - Section C.2:  Silver Structured Metric — metadata_extension fields
    - Section 3.2:  Message Envelope & DRY (all Kafka logic in kafka_utils.py)
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.2B: Momentum Block — keyed state per keyword
    - Section 9.1:  Environment parity — no API key required
"""

from __future__ import annotations

import copy
import logging
import sys
import time
from datetime import datetime, timezone
from typing import Optional

from pytrends.request import TrendReq

from config.kafka_topics import BRONZE_GOOGLETRENDS
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# Google Trends Constants (Section B.5)
# ==========================================================

# Geographic monitoring matrix (Section B.5: US, UK, Israel, Germany).
#
# geo_code:     ISO 3166-1 alpha-2 — used by build_payload() and
#               interest_over_time(). This is the canonical geo identifier
#               stored in every Bronze raw_payload.
# trending_pn:  Country name slug — used by trending_searches() which
#               accepts a different format than build_payload(). Pytrends
#               uses two separate geo-identification schemes depending on
#               which API endpoint it wraps internally.
GEO_MATRIX: dict[str, dict] = {
    "US": {"name": "United States", "trending_pn": "united_states"},
    "GB": {"name": "United Kingdom", "trending_pn": "united_kingdom"},
    "IL": {"name": "Israel",         "trending_pn": "israel"},
    "DE": {"name": "Germany",        "trending_pn": "germany"},
}

# Core keywords for the 5-year "Interest Over Time" backfill (Section B.5).
# These are foundational economic and geopolitical signals that the RAG
# agent needs a multi-year baseline for before it can evaluate trend
# deviations meaningfully.
BACKFILL_KEYWORDS: list[str] = [
    "Inflation",
    "Crude Oil",
    "Recession",
    "Interest Rates",
    "Federal Reserve",
    "Unemployment",
    "Stock Market",
    "Bitcoin",
    "NATO",
    "Ukraine War",
]

# Maximum trending topics to fetch per geo in static mode.
# Section B.5: "Top 50 trending topics". trending_searches() may return
# fewer than 50 for some geos — we cap and never pad.
MAX_TRENDING_PER_GEO: int = 50

# Pytrends rate-limit guard (Section 4.3).
# Google's unofficial API imposes opaque rate limits. 1.5 s between calls
# is conservative and avoids ResponseError / 429 under normal load.
# Increase to 3.0 s if ResponseError frequency rises in production logs.
REQUEST_DELAY_SEC: float = 1.5

# Timeframe strings for Pytrends build_payload() (Pytrends convention).
TIMEFRAME_7D = "now 7-d"    # Reactive mode: last 7 days of daily scores
TIMEFRAME_5Y = "today 5-y"  # Backfill mode: last 5 years of weekly scores

# Pytrends session settings.
# hl='en-US': returns categories and keyword suggestions in English.
#   Ensures the Silver Job's _translate_keyword() stub (Phase 5) receives
#   normalised ASCII input even when the underlying Google trend has a
#   localised label in DE or IL.
# tz=360: Pytrends default UTC offset (minutes). Does not affect the
#   timestamps returned by interest_over_time() — those are always UTC.
PYTRENDS_HL: str = "en-US"
PYTRENDS_TZ: int = 360

# Base URL embedded in the source_endpoint field of each Bronze envelope.
# Provides an audit-trail link back to the Google Trends explore UI for
# the exact keyword + geo combination that was fetched.
PYTRENDS_BASE_URL: str = "https://trends.google.com/trends/explore"

# Browser-like headers for TrendReq to reduce 429 rate-limit responses.
#
# Google's unofficial Trends API aggressively rate-limits requests that do not
# carry standard browser headers. Passing a real Chrome User-Agent and Accept
# headers via requests_args makes pytrends sessions look like organic browser
# traffic, significantly reducing 429 frequency in production.
#
# IMPORTANT — deep-copy before passing to TrendReq():
#   pytrends.__init__ calls self.requests_args.pop("headers", {}) which
#   permanently mutates the dict passed in. Always pass
#   copy.deepcopy(PYTRENDS_REQUESTS_ARGS) so successive TrendReq instantiations
#   still see the headers key.
PYTRENDS_REQUESTS_ARGS: dict = {
    "headers": {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/123.0.0.0 Safari/537.36"
        ),
        "Accept": (
            "text/html,application/xhtml+xml,application/xml;"
            "q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8"
        ),
        "Accept-Language": "en-US,en;q=0.9",
        "Referer": "https://trends.google.com/",
    }
}

# Canonical source identifier written into every Bronze envelope.
SOURCE_NAME: str = "googletrends"


# ==========================================================
# Producer
# ==========================================================

class GoogleTrendsProducer:
    """
    Pytrends-based producer for Google Trends interest data.

    Three operating modes (Section B.5):
      static    — daily top-50 trending topics, all 4 monitored geos.
      reactive  — on-demand specific keyword Interest Over Time, one geo.
      backfill  — one-time 5-year Interest Over Time for BACKFILL_KEYWORDS,
                  all 4 geos.

    Like fred_producer.py, this producer is designed to be invoked once per
    Airflow DAG run (Section 2.3) or on demand by the RAG agent's reactive
    ingestion system (Section 2.4). It is not a long-running daemon — after
    fetching and emitting, it flushes Kafka and exits cleanly.

    Emits to: ingest.bronze.googletrends (BRONZE_GOOGLETRENDS)
    """

    def __init__(self) -> None:
        """
        Initialise Pytrends TrendReq session and Kafka producer.

        TrendReq session parameters:
          hl='en-US':      Return keywords in English — see PYTRENDS_HL note.
          tz=360:          Pytrends default offset — does not affect UTC
                           timestamps in interest_over_time() output.
          timeout=(5, 25): (connect_timeout_s, read_timeout_s). Read timeout
                           of 25 s is generous — Google's endpoint can take
                           10-15 s under load.

        Note: retries/backoff_factor are intentionally omitted. Pytrends passes
        them to urllib3.util.retry.Retry using the deprecated 'method_whitelist'
        kwarg, which was removed in urllib3 2.0 (raises TypeError at init time).
        Network retries are handled at the caller level (REQUEST_DELAY_SEC +
        Airflow DAG task retries in production).

        requests_args: browser-like headers to reduce 429 rate-limit responses.
        deep-copied because pytrends.__init__ pops the 'headers' key in-place.
        """
        self._pytrends = TrendReq(
            hl=PYTRENDS_HL,
            tz=PYTRENDS_TZ,
            timeout=(5, 25),
            requests_args=copy.deepcopy(PYTRENDS_REQUESTS_ARGS),
        )
        self._producer = make_producer()
        self._emitted: int = 0  # running count for summary log at exit

    # ----------------------------------------------------------
    # Raw Payload Builder
    # ----------------------------------------------------------

    def _build_raw_payload(
        self,
        keyword: str,
        geo: str,
        interest_score: float,
        fetch_timestamp: str,
        mode: str,
        observation_date: Optional[str] = None,
        rank_in_trending: Optional[int] = None,
        timeframe: Optional[str] = None,
    ) -> dict:
        """
        Build the raw_payload dict for one Google Trends observation.

        This dict is stored verbatim as raw_payload inside the Bronze
        envelope (Section C.1). It carries all fields the Silver Job needs
        to populate the C.2 metadata_extension block without consulting
        any external lookup table — keeping the Silver Job stateless and
        independently testable (Section 3.3 Service Isolation).

        Args:
            keyword:          The trending keyword or search term.
            geo:              ISO geo code ("US", "GB", "IL", "DE").
            interest_score:   0.0–100.0.
                              static mode:          rank-to-100 approximation.
                              reactive/backfill:    raw Google Trends score.
            fetch_timestamp:  ISO8601 UTC — when the fetch occurred.
            mode:             "static" | "reactive" | "backfill".
            observation_date: "YYYY-MM-DD" — the date the score refers to.
                              Populated for reactive and backfill (where
                              interest_over_time() returns dated rows).
                              None for static (daily snapshot, no sub-day
                              granularity from trending_searches()).
            rank_in_trending: 1-based rank within the trending list.
                              Only populated for static mode — allows
                              downstream reconstruction of the original
                              ranking regardless of the derived score.
            timeframe:        Pytrends timeframe string used in this request
                              (e.g., "now 7-d", "today 5-y"). None for static.

        Returns:
            Raw payload dict to pass to build_bronze_message().
        """
        geo_info = GEO_MATRIX.get(geo, {"name": geo, "trending_pn": geo.lower()})

        payload: dict = {
            "keyword":         keyword,
            "geo":             geo,
            "geo_name":        geo_info["name"],
            "interest_score":  float(interest_score),
            "mode":            mode,
            "fetch_timestamp": fetch_timestamp,
        }
        if observation_date is not None:
            payload["observation_date"] = observation_date
        if rank_in_trending is not None:
            payload["rank_in_trending"] = rank_in_trending
        if timeframe is not None:
            payload["timeframe"] = timeframe
        return payload

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(self, raw_payload: dict) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to BRONZE_GOOGLETRENDS.

        Partition key: keyword (bytes) — collocates all observations for the
        same keyword in one Kafka partition. The Gold Job's keyed-state
        momentum operator keys by (source_name, external_reference_id=keyword),
        so partition affinity reduces cross-partition state lookups in the
        Flink cluster (Section 4.2B). Same pattern as fred_producer.py
        partitioning by series_id.

        source_endpoint encodes keyword + geo + mode for the Bronze audit trail.

        Args:
            raw_payload: Dict produced by _build_raw_payload().
        """
        keyword = raw_payload["keyword"]
        geo     = raw_payload["geo"]
        mode    = raw_payload["mode"]

        source_endpoint = (
            f"{PYTRENDS_BASE_URL}?geo={geo}&q={keyword}&mode={mode}"
        )
        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
        )
        self._producer.send(
            BRONZE_GOOGLETRENDS,
            value=msg,
            key=keyword.encode("utf-8"),
        )
        self._emitted += 1

    # ----------------------------------------------------------
    # Static Mode — daily trending topics
    # ----------------------------------------------------------

    def run_static(self) -> int:
        """
        Fetch and emit up to MAX_TRENDING_PER_GEO trending topics per geo.

        Called daily by an Airflow DAG (Section 2.3). For each of the 4
        monitored geographies, fetches the current top trending keywords via
        pytrends.trending_searches() and emits one Bronze envelope per keyword.

        Interest score derivation (rank-to-100, see module docstring):
            score = max(0.0, 100.0 - rank_index * 2.0)
            where rank_index is 0-based (rank_index 0 = rank 1).

        Error handling: a failure on one geo skips that geo and continues
        to the next — partial runs are better than a full abort. Each geo
        failure is logged at ERROR level so the Airflow task is marked
        failed even if the run partially succeeded (the DAG can retry).

        Returns:
            Total number of Bronze messages emitted across all geos.
        """
        run_start_emitted = self._emitted
        fetch_ts = datetime.now(timezone.utc).isoformat()

        logger.info(
            "[googletrends] Static run starting — %d geos, up to %d topics each",
            len(GEO_MATRIX), MAX_TRENDING_PER_GEO,
        )

        for geo_code, geo_info in GEO_MATRIX.items():
            pn = geo_info["trending_pn"]
            try:
                df, duration_ms = timed_request(
                    lambda _pn=pn: self._pytrends.trending_searches(pn=_pn)
                )

                if df is None or df.empty:
                    logger.warning(
                        "[googletrends] static — %s (%s): trending_searches() "
                        "returned empty result — skipping geo",
                        geo_code, pn,
                    )
                    continue

                # trending_searches() returns a single-column DataFrame.
                # Column 0 contains the keyword strings in trending order.
                keywords = df.iloc[:, 0].tolist()[:MAX_TRENDING_PER_GEO]
                geo_emitted = 0

                for rank_index, keyword in enumerate(keywords):
                    keyword = str(keyword).strip()
                    if not keyword:
                        continue
                    # rank-to-100 score: rank 0 → 100.0, rank 49 → 2.0
                    score = max(0.0, 100.0 - rank_index * 2.0)
                    raw = self._build_raw_payload(
                        keyword=keyword,
                        geo=geo_code,
                        interest_score=score,
                        fetch_timestamp=fetch_ts,
                        mode="static",
                        rank_in_trending=rank_index + 1,  # 1-based for readability
                    )
                    self._emit(raw)
                    geo_emitted += 1

                logger.info(
                    "[googletrends] static — %s: emitted %d topics (duration=%dms)",
                    geo_code, geo_emitted, duration_ms,
                )

            except Exception as exc:
                # Catch all Pytrends / network errors at the geo level.
                # Log at ERROR so the Airflow DAG marks the task failed,
                # but continue to remaining geos (partial data > no data).
                logger.error(
                    "[googletrends] static — %s: failed (%s: %s) — skipping geo",
                    geo_code, type(exc).__name__, exc,
                )
            finally:
                time.sleep(REQUEST_DELAY_SEC)

        run_emitted = self._emitted - run_start_emitted
        self._producer.flush()
        logger.info(
            "[googletrends] Static run complete — emitted %d messages across %d geos",
            run_emitted, len(GEO_MATRIX),
        )

        # Phase 9.5 Stage B Item 4 — raise on 0% success.
        # Previous behavior: pytrends 404 errors on every geo (KG-PHASE-C-7)
        # were caught + logged + skipped, and the producer returned 0 cleanly.
        # Airflow saw `task: success` despite zero Bronze messages emitted.
        # Now: if 0-of-N geos produced any messages, raise so the DAG task
        # is correctly marked `failed` and the monitoring layer has a
        # single clean signal to alert on.
        if run_emitted == 0 and len(GEO_MATRIX) > 0:
            raise RuntimeError(
                f"[googletrends] All {len(GEO_MATRIX)} geo fetches failed "
                f"— emitted 0 Bronze messages. See per-geo ERROR logs above "
                f"for upstream cause (typically pytrends ResponseError 404 "
                f"from Google's unofficial Trends endpoint: KG-PHASE-C-7)."
            )

        return run_emitted

    # ----------------------------------------------------------
    # Reactive Mode — on-demand keyword lookup
    # ----------------------------------------------------------

    def run_reactive(
        self,
        keywords: list[str],
        geo: str = "US",
        timeframe: str = TIMEFRAME_7D,
    ) -> int:
        """
        Fetch and emit 7-day Interest Over Time for specific keywords on demand.

        Called by the RAG agent via the ingestion_triggers Kafka topic
        (Section 2.4) when a knowledge gap is detected during inference
        (e.g., the agent needs to know current public hype around "Kuwait
        oil strike"). Emits one Bronze envelope per (keyword, date) pair
        with the actual 0-100 Google Trends interest score.

        Why interest_over_time() here vs. trending_searches() in static:
            trending_searches() provides no interest score — only a keyword
            list. For reactive lookups, the RAG agent needs the actual
            interest trajectory over time, not just whether the keyword is
            currently trending. interest_over_time() provides both: a
            time-series of 0-100 scores that feed directly into the Gold
            Job's momentum calculations.

        Why one keyword at a time (solo build_payload):
            Pytrends normalises scores relative to the highest-interest
            keyword in the batch. Solo processing ensures the returned
            score is the true 0-100 scale for each individual keyword,
            not deflated by co-batching with a higher-volume term.

        Args:
            keywords: List of search keywords to fetch (RAG agent provides these).
            geo:      ISO geo code (default "US"). GEO_MATRIX codes only.
            timeframe: Pytrends timeframe string (default TIMEFRAME_7D = "now 7-d").

        Returns:
            Total number of Bronze messages emitted.
        """
        run_start_emitted = self._emitted
        fetch_ts = datetime.now(timezone.utc).isoformat()

        if geo not in GEO_MATRIX:
            logger.warning(
                "[googletrends] reactive — geo '%s' not in GEO_MATRIX %s — "
                "using 'US' as fallback",
                geo, list(GEO_MATRIX.keys()),
            )
            geo = "US"

        logger.info(
            "[googletrends] Reactive run starting — %d keywords, geo=%s, timeframe=%s",
            len(keywords), geo, timeframe,
        )

        for keyword in keywords:
            keyword = keyword.strip()
            if not keyword:
                continue
            try:
                self._pytrends.build_payload(
                    kw_list=[keyword],
                    timeframe=timeframe,
                    geo=geo,
                )
                df, duration_ms = timed_request(
                    lambda: self._pytrends.interest_over_time()
                )

                if df is None or df.empty:
                    logger.warning(
                        "[googletrends] reactive — '%s' (%s): interest_over_time() "
                        "returned empty — keyword may have no data in this timeframe",
                        keyword, geo,
                    )
                    time.sleep(REQUEST_DELAY_SEC)
                    continue

                # interest_over_time() returns a DataFrame indexed by date,
                # with one column per keyword + an "isPartial" boolean column.
                # We only want the keyword's score column.
                if keyword not in df.columns:
                    logger.warning(
                        "[googletrends] reactive — '%s' column not found in "
                        "interest_over_time() result — columns: %s",
                        keyword, list(df.columns),
                    )
                    time.sleep(REQUEST_DELAY_SEC)
                    continue

                kw_col = df[keyword]
                kw_emitted = 0

                for date_idx, score_val in kw_col.items():
                    # date_idx is a pandas Timestamp; convert to ISO date string.
                    obs_date = date_idx.strftime("%Y-%m-%d")
                    raw = self._build_raw_payload(
                        keyword=keyword,
                        geo=geo,
                        interest_score=float(score_val),
                        fetch_timestamp=fetch_ts,
                        mode="reactive",
                        observation_date=obs_date,
                        timeframe=timeframe,
                    )
                    self._emit(raw)
                    kw_emitted += 1

                logger.info(
                    "[googletrends] reactive — '%s' (%s): emitted %d data points "
                    "(duration=%dms)",
                    keyword, geo, kw_emitted, duration_ms,
                )

            except Exception as exc:
                logger.error(
                    "[googletrends] reactive — '%s' (%s): failed (%s: %s) — skipping",
                    keyword, geo, type(exc).__name__, exc,
                )
            finally:
                time.sleep(REQUEST_DELAY_SEC)

        run_emitted = self._emitted - run_start_emitted
        self._producer.flush()
        logger.info(
            "[googletrends] Reactive run complete — emitted %d messages",
            run_emitted,
        )
        return run_emitted

    # ----------------------------------------------------------
    # Backfill Mode — 5-year historical load
    # ----------------------------------------------------------

    def run_backfill(self) -> int:
        """
        Fetch and emit 5-year Interest Over Time for all BACKFILL_KEYWORDS,
        across all 4 monitored geos.

        One-time operation (Section B.5: "Backfill: 5-year 'Interest Over Time'
        for core keywords"). Safe to re-run — momentum_vault uses
        ON CONFLICT (metric_id, timestamp_utc) DO NOTHING, so duplicate
        observations from a re-run are silently ignored.

        Uses TIMEFRAME_5Y = "today 5-y" (Pytrends weekly resolution over 5 years).
        Processes one keyword per geo at a time (PYTRENDS_BATCH_SIZE = 1)
        for absolute score accuracy — see "Why backfill processes one keyword
        at a time" in the module docstring.

        Ordering: outer loop = geo, inner loop = keyword. This minimises
        TrendReq session re-initialisation and produces geo-collocated Kafka
        messages, which is natural for momentum_vault's (source, external_ref)
        index but irrelevant to correctness.

        Returns:
            Total number of Bronze messages emitted.
        """
        run_start_emitted = self._emitted
        fetch_ts = datetime.now(timezone.utc).isoformat()
        total_keywords = len(BACKFILL_KEYWORDS) * len(GEO_MATRIX)

        logger.info(
            "[googletrends] Backfill run starting — %d keywords × %d geos = "
            "%d total keyword-geo combinations, timeframe=%s",
            len(BACKFILL_KEYWORDS), len(GEO_MATRIX), total_keywords, TIMEFRAME_5Y,
        )

        for geo_code in GEO_MATRIX:
            for keyword in BACKFILL_KEYWORDS:
                try:
                    self._pytrends.build_payload(
                        kw_list=[keyword],
                        timeframe=TIMEFRAME_5Y,
                        geo=geo_code,
                    )
                    df, duration_ms = timed_request(
                        lambda: self._pytrends.interest_over_time()
                    )

                    if df is None or df.empty:
                        logger.warning(
                            "[googletrends] backfill — '%s' (%s): "
                            "interest_over_time() returned empty — skipping",
                            keyword, geo_code,
                        )
                        time.sleep(REQUEST_DELAY_SEC)
                        continue

                    if keyword not in df.columns:
                        logger.warning(
                            "[googletrends] backfill — '%s' (%s): keyword column "
                            "not found — columns: %s — skipping",
                            keyword, geo_code, list(df.columns),
                        )
                        time.sleep(REQUEST_DELAY_SEC)
                        continue

                    kw_col = df[keyword]
                    kw_emitted = 0

                    for date_idx, score_val in kw_col.items():
                        obs_date = date_idx.strftime("%Y-%m-%d")
                        raw = self._build_raw_payload(
                            keyword=keyword,
                            geo=geo_code,
                            interest_score=float(score_val),
                            fetch_timestamp=fetch_ts,
                            mode="backfill",
                            observation_date=obs_date,
                            timeframe=TIMEFRAME_5Y,
                        )
                        self._emit(raw)
                        kw_emitted += 1

                    logger.info(
                        "[googletrends] backfill — '%s' (%s): emitted %d data points "
                        "(duration=%dms)",
                        keyword, geo_code, kw_emitted, duration_ms,
                    )

                except Exception as exc:
                    logger.error(
                        "[googletrends] backfill — '%s' (%s): failed (%s: %s) — "
                        "skipping keyword-geo combination",
                        keyword, geo_code, type(exc).__name__, exc,
                    )
                finally:
                    time.sleep(REQUEST_DELAY_SEC)

        run_emitted = self._emitted - run_start_emitted
        self._producer.flush()
        logger.info(
            "[googletrends] Backfill run complete — emitted %d messages "
            "across %d keywords × %d geos",
            run_emitted, len(BACKFILL_KEYWORDS), len(GEO_MATRIX),
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
        fred_producer.py).
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[googletrends] Producer closed cleanly.")


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(mode: str = "static", keywords: Optional[list[str]] = None, geo: str = "US") -> None:
    """
    Entry point for Docker container and Airflow BashOperator invocation.

    Modes:
        static    (default) — daily top-50 trending topics, all 4 geos.
        reactive            — on-demand keyword lookup (keywords required).
        backfill            — one-time 5-year historical load, all geos.

    Usage:
        python -m ingestion.googletrends_producer              # static (default)
        python -m ingestion.googletrends_producer static       # static (explicit)
        python -m ingestion.googletrends_producer backfill     # backfill
        python -m ingestion.googletrends_producer reactive "Inflation,Crude Oil" US
        # reactive: comma-separated keywords and optional geo code

    The Airflow DAG calls this via BashOperator or PythonOperator (Section 6A.1).
    The Docker container CMD defaults to "static" for the daily scheduled run.

    Args:
        mode:     Operating mode string.
        keywords: Keyword list for reactive mode. Ignored for static/backfill.
        geo:      ISO geo code for reactive mode (default "US").
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = GoogleTrendsProducer()
    try:
        if mode == "backfill":
            producer.run_backfill()
        elif mode == "reactive":
            if not keywords:
                logger.error(
                    "[googletrends] reactive mode requires at least one keyword. "
                    "Pass keywords as the second argument (comma-separated)."
                )
                sys.exit(1)
            producer.run_reactive(keywords=keywords, geo=geo)
        else:
            producer.run_static()
    finally:
        producer.close()


if __name__ == "__main__":
    _mode = sys.argv[1] if len(sys.argv) > 1 else "static"

    if _mode not in ("static", "reactive", "backfill"):
        print(
            f"[googletrends] Unknown mode '{_mode}'. "
            "Expected 'static', 'reactive', or 'backfill'.",
            file=sys.stderr,
        )
        sys.exit(1)

    _keywords: Optional[list[str]] = None
    _geo = "US"

    if _mode == "reactive":
        if len(sys.argv) < 3:
            print(
                "[googletrends] reactive mode requires keywords as the second argument.\n"
                "Usage: python -m ingestion.googletrends_producer reactive "
                "\"Inflation,Crude Oil\" US",
                file=sys.stderr,
            )
            sys.exit(1)
        _keywords = [kw.strip() for kw in sys.argv[2].split(",") if kw.strip()]
        _geo = sys.argv[3].upper() if len(sys.argv) > 3 else "US"

    main(_mode, keywords=_keywords, geo=_geo)
