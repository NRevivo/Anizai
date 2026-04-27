"""
NewsAPI Producer — REST API, 15-30 min polling.

Fetches top headlines from authority-whitelisted sources across Business,
Technology, General, Health, and Science categories. Applies the Keyword
Sniper pre-filter on the 'General' category before Bronze emission — only
articles matching at least one keyword in GENERAL_KEYWORDS are forwarded.
All other categories pass through the whitelist check only.

Note on 'politics' category: newsapi.org's /top-headlines endpoint does not
support a 'politics' category (valid values: business, entertainment, general,
health, science, sports, technology). Political content is captured via the
'general' category combined with the Keyword Sniper filter (Section 2.2).

Two operating modes (Section 2.3 / B.4):
  - pulse    (default) — one page of top-headlines per category (max 100
                         articles). Designed to be called every 15-30 min
                         by an Airflow DAG.
  - backfill            — tiered historical load via /v2/everything:
                           Tier 1: full density, 0-6 months, all authority sources
                           Tier 2: tier-1 sources only, 6-24 months
                           Tier 3: tier-1 + keyword query, 2-5 years (anomaly events;
                                   only executed if --keywords is supplied)

Why pre-filter at the producer (not only in Flink):
    Section 2.2 mandates "Only high-signal data enters Bronze Kafka topics."
    Pushing noise into Bronze wastes Kafka retention, Silver processing cycles,
    and Silver DLQ capacity. The whitelist check and General Keyword Sniper gate
    are cheap string operations; the cost of running them here is negligible.
    The Silver Job's keyword_sniper.py module (Task 3.2) applies a more
    sophisticated relevance-scoring pass inside Flink (Section 4.1A).

Why embed impact_boost flag in raw_payload:
    Section B.4 requires a +1 boost to impact_level for Israeli/Middle East
    security or energy articles. Embedding the boolean flag and matched keyword
    at Bronze time makes the Gold Job stateless — it reads the flag rather than
    re-scanning article text, keeping Section 3.3 Service Isolation intact.

Partition key: source.id — groups articles by publisher into the same Kafka
    partition. The Silver Job's SHA-256 dedup sees all articles from the same
    source on the same consumer, making hash collision detection consistent.
    Falls back to source.name if id is absent (Israeli/regional outlets).

Kafka Target: ingest.bronze.newsapi (BRONZE_NEWSAPI)
Sprint Priority: 3 — establishes the REST + keyword filtering pattern.

References:
    - Section 2.1:  Producer Matrix (NewsAPI row)
    - Section 2.2:  SNR Optimization — Domain Filtering Strategy
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section B.4:  NewsAPI technical parameters, Authority Whitelist,
                    Keyword Sniper keywords, Tiered Backfill, Impact Boost
    - Section C.1:  Bronze Schema (build_bronze_message wraps every payload)
    - Section C.3:  Silver Full-Text Document Store — fields this producer populates
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.1A: Keyword Sniper Filter (Silver-level; producer does pre-filter only)
    - Section 9.1:  Environment parity — NEWS_API_KEY from .env
"""

from __future__ import annotations
import logging
import sys
import time
from datetime import date, timedelta
from typing import Optional

import requests

from config.kafka_topics import BRONZE_NEWSAPI
from config.settings import NEWS_API_KEY
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# NewsAPI Constants (Section B.4)
# ==========================================================

SOURCE_NAME = "newsapi"

NEWSAPI_BASE_URL       = "https://newsapi.org/v2"
TOP_HEADLINES_ENDPOINT = f"{NEWSAPI_BASE_URL}/top-headlines"
EVERYTHING_ENDPOINT    = f"{NEWSAPI_BASE_URL}/everything"

# Maximum articles per page (hard NewsAPI limit).
PAGE_SIZE = 100

# Inter-request delay. newsapi.org paid plans allow ~1 req/sec sustained.
REQUEST_DELAY_SEC = 1.0

# Categories fetched in pulse mode via /top-headlines.
# Each category passes through the authority whitelist only.
PULSE_CATEGORIES = ["business", "technology", "health", "science"]

# The 'general' category is fetched separately and also keyword-sniped
# because it mixes high- and low-signal content.
GENERAL_CATEGORY = "general"


# ==========================================================
# Authority Whitelist (Section B.4)
# ==========================================================
# Matched against article source.id AND source.name (both lowercased).
# Why both fields: Israeli/regional outlets often lack a formal NewsAPI
# source.id, so name-matching is the only reliable gate for those sources.

AUTHORITY_WHITELIST: frozenset[str] = frozenset({
    # --- NewsAPI source IDs (lowercase, hyphenated) ---
    "reuters",
    "associated-press",
    "the-wall-street-journal",
    "bloomberg",
    "the-new-york-times",
    "the-washington-post",
    "cnbc",
    "cnn",
    "bbc-news",
    "financial-times",
    "the-guardian-uk",
    "the-economist",
    # --- Display names (lowercase) ---
    "associated press",
    "the wall street journal",
    "the new york times",
    "the washington post",
    "bbc news",
    "financial times",
    "the guardian",
    "the economist",
    # --- Israeli & Middle East outlets ---
    "kan 11",
    "the times of israel",
    "times of israel",
    "jerusalem post",
    "the jerusalem post",
    "ynetnews",
    "ynet",
    "i24 news",
    "i24news",
})


# ==========================================================
# Keyword Sniper — General Category Pre-filter (Section B.4)
# ==========================================================
# A General-category article is emitted to Bronze only if its title OR
# description contains at least one of these keywords (case-insensitive).
# This matches the spec keywords exactly.
# Full relevance scoring (density + position weights) is handled by
# processing/keyword_sniper.py at the Silver Flink layer (Section 4.1A).

GENERAL_KEYWORDS: frozenset[str] = frozenset({
    "conflict",
    "sanctions",
    "crude oil",
    "opec",
    "missile defense",
    "interest rates",
    "ai regulation",
    "nato",
    "central bank",
})


# ==========================================================
# Impact Boost Detection (Section B.4)
# ==========================================================
# Articles matching any of these terms get impact_level +1 in the Gold Job.
# The flag is computed here (cheap string scan) and embedded in raw_payload
# so the Gold Job stays stateless (Section 3.3 Service Isolation).

IMPACT_BOOST_TERMS: frozenset[str] = frozenset({
    # Israeli / Middle East security
    "israel", "israeli", "middle east", "gaza", "west bank",
    "hezbollah", "hamas", "iran", "beirut", "lebanon",
    # Energy
    "crude oil", "opec", "oil price", "petroleum", "energy crisis",
})


# ==========================================================
# Backfill Tier Parameters (Section B.4)
# ==========================================================

# Tier-1 source IDs used for the 6-24 month backfill window.
# Subset of the full authority whitelist — highest-reliability sources only.
TIER_ONE_SOURCE_IDS: list[str] = [
    "reuters",
    "associated-press",
    "bloomberg",
    "the-wall-street-journal",
    "the-new-york-times",
    "the-washington-post",
    "bbc-news",
    "financial-times",
]

BACKFILL_FULL_MONTHS    = 6    # Tier 1: full density window (months from today)
BACKFILL_TIER_ONE_MONTHS = 24  # Tier 2: tier-1-only window (months from today)
BACKFILL_MAX_YEARS       = 5   # Tier 3: anomaly scan upper bound (years from today)


# ==========================================================
# Producer
# ==========================================================

class NewsAPIProducer:
    """
    Scheduled REST producer for NewsAPI top headlines.

    Pulse mode polls /v2/top-headlines every 15-30 min for each category.
    Backfill mode crawls /v2/everything across three date-range tiers.

    Both modes apply the authority whitelist; only the General category
    additionally requires a Keyword Sniper hit before Bronze emission.

    Emits to: ingest.bronze.newsapi (BRONZE_NEWSAPI)
    """

    def __init__(self) -> None:
        if not NEWS_API_KEY:
            raise ValueError(
                "[newsapi] NEWS_API_KEY not set in .env — cannot authenticate. "
                "Set NEWS_API_KEY to your Massive.com Stocks Starter Plan key (Section B.4)."
            )
        self._producer = make_producer()
        # Running totals for the summary log (reset each run_pulse / run_backfill call)
        self._emitted            = 0
        self._filtered_whitelist = 0
        self._filtered_keyword   = 0

    # ----------------------------------------------------------
    # Filters (Section 2.2, B.4)
    # ----------------------------------------------------------

    def _passes_whitelist(self, article: dict) -> bool:
        """
        Return True if the article's source is on the authority whitelist.

        Why check both source.id and source.name: see AUTHORITY_WHITELIST note above.
        The check is case-insensitive and trims whitespace to guard against minor
        formatting differences across API responses.
        """
        source     = article.get("source") or {}
        source_id  = (source.get("id")   or "").strip().lower()
        source_name = (source.get("name") or "").strip().lower()
        return source_id in AUTHORITY_WHITELIST or source_name in AUTHORITY_WHITELIST

    def _passes_keyword_sniper(self, article: dict) -> bool:
        """
        Return True if the article's title or description contains at least one
        GENERAL_KEYWORDS term. Applied to General category only (Section B.4).

        Why title + description (not full content): the producer pre-filter needs
        only a fast yes/no decision. Article content may be truncated on lower
        NewsAPI plan tiers, making it an unreliable field for matching at this layer.
        The Silver Job's keyword_sniper.py (Task 3.2) applies full relevance scoring
        on the complete document body inside Flink (Section 4.1A).
        """
        text = " ".join(filter(None, [
            article.get("title")       or "",
            article.get("description") or "",
        ])).lower()
        return any(kw in text for kw in GENERAL_KEYWORDS)

    def _impact_boost_info(self, article: dict) -> tuple[bool, str]:
        """
        Return (needs_boost, matched_term) based on IMPACT_BOOST_TERMS scan.

        Why first-match only: the Gold Job needs a boolean flag and a single
        human-readable reason for audit trails; scanning all terms is unnecessary.
        If multiple terms match, the first encountered is stored as boost_reason.
        """
        text = " ".join(filter(None, [
            article.get("title")       or "",
            article.get("description") or "",
        ])).lower()
        for term in IMPACT_BOOST_TERMS:
            if term in text:
                return True, term
        return False, ""

    # ----------------------------------------------------------
    # NewsAPI REST Fetch
    # ----------------------------------------------------------

    def _fetch_top_headlines(
        self,
        category: str,
        page: int = 1,
    ) -> tuple[list[dict], int, int]:
        """
        Fetch one page of top-headlines for a given category.

        Why no country filter: the authority whitelist provides source-level
        filtering. Adding country=us would drop Israeli/UK sources that are
        explicitly on the whitelist (Section B.4).

        Args:
            category: Valid newsapi.org category string.
            page:     1-indexed page number.

        Returns:
            (articles, total_results, duration_ms)

        Raises:
            requests.HTTPError: On non-2xx responses.
            requests.RequestException: On network failures.
        """
        params: dict = {
            "category": category,
            "pageSize": PAGE_SIZE,
            "page":     page,
            "apiKey":   NEWS_API_KEY,
        }
        response, duration_ms = timed_request(
            lambda: requests.get(TOP_HEADLINES_ENDPOINT, params=params, timeout=20)
        )
        response.raise_for_status()

        data          = response.json()
        articles      = data.get("articles") or []
        total_results = data.get("totalResults") or 0

        logger.debug(
            "[newsapi] top-headlines?category=%s page=%d — %d articles "
            "(totalResults=%d, %dms)",
            category, page, len(articles), total_results, duration_ms,
        )
        return articles, total_results, duration_ms

    def _fetch_everything(
        self,
        from_date: str,
        to_date: str,
        sources: Optional[list[str]] = None,
        keywords: Optional[str] = None,
        page: int = 1,
    ) -> tuple[list[dict], int, int]:
        """
        Fetch one page from /v2/everything — used for tiered backfill.

        Why sortBy=publishedAt: ensures the oldest articles in each page come
        first, so Kafka Bronze receives messages in chronological order within
        each batch. This mirrors the oldest-first pattern used by the FRED
        backfill for correct keyed-state population.

        Why language=en: all downstream NLP (embedding, summarisation) operates
        on English. Non-English articles are handled by the Translation path
        (Section 4.1D) which is scoped to Telegram only — not NewsAPI.

        Args:
            from_date: ISO date "YYYY-MM-DD" (inclusive).
            to_date:   ISO date "YYYY-MM-DD" (inclusive).
            sources:   List of NewsAPI source IDs. None = no API-level source
                       filter (authority whitelist applied client-side).
            keywords:  Optional keyword query string (e.g., "crude oil OR NATO").
            page:      1-indexed page number.

        Returns:
            (articles, total_results, duration_ms)
        """
        params: dict = {
            "from":     from_date,
            "to":       to_date,
            "sortBy":   "publishedAt",
            "pageSize": PAGE_SIZE,
            "page":     page,
            "apiKey":   NEWS_API_KEY,
            "language": "en",
        }
        if sources:
            params["sources"] = ",".join(sources)
        if keywords:
            params["q"] = keywords

        response, duration_ms = timed_request(
            lambda: requests.get(EVERYTHING_ENDPOINT, params=params, timeout=30)
        )
        response.raise_for_status()

        data          = response.json()
        articles      = data.get("articles") or []
        total_results = data.get("totalResults") or 0

        logger.debug(
            "[newsapi] everything from=%s to=%s page=%d — %d articles "
            "(totalResults=%d, %dms)",
            from_date, to_date, page, len(articles), total_results, duration_ms,
        )
        return articles, total_results, duration_ms

    # ----------------------------------------------------------
    # Payload Builder
    # ----------------------------------------------------------

    def _build_raw_payload(
        self,
        article: dict,
        category: str,
        fetch_mode: str,
    ) -> dict:
        """
        Construct the raw_payload dict for one NewsAPI article.

        All fields are stored verbatim (Bronze layer is immutable, Section 2.3).
        Producer-injected fields (category, fetch_mode, impact_boost,
        impact_boost_reason) are prefixed with no special marker but are
        documented here so Silver/Gold Jobs can reliably read them.

        Why store article_id as URL: NewsAPI has no stable numeric article ID.
        The URL is the canonical dedup key for SHA-256 hashing in the Silver Job
        (Section 4.1C). The Silver Job's hash_document(full_text, url) call
        produces the document_hash stored in the Knowledge Vault.

        Why content may be truncated: newsapi.org truncates the 'content' field
        at 200 characters on some plan tiers. The Silver Job stores the
        description as inverted_pyramid_lead and the content field as the
        best available body text — truncation is acceptable at Bronze layer.

        Args:
            article:    Raw article dict from the NewsAPI response.
            category:   Category string used to fetch this article ("business",
                        "general", etc.). Empty string for backfill articles
                        (fetched via /v2/everything with no category parameter).
            fetch_mode: "pulse" | "backfill_full" | "backfill_tier_one" |
                        "backfill_anomaly"
        """
        source        = article.get("source") or {}
        boost, reason = self._impact_boost_info(article)

        return {
            # Core article fields (as received from NewsAPI — never modified)
            "article_id":    article.get("url") or "",
            "title":         article.get("title") or "",
            "description":   article.get("description") or "",
            "url":           article.get("url") or "",
            "url_to_image":  article.get("urlToImage") or "",
            "published_at":  article.get("publishedAt") or "",
            "content":       article.get("content") or "",
            "author":        article.get("author") or "",
            "source": {
                "id":   source.get("id")   or "",
                "name": source.get("name") or "",
            },
            # Producer-injected context (consumed by Silver / Gold Jobs)
            "category":            category,
            "fetch_mode":          fetch_mode,
            "impact_boost":        boost,
            "impact_boost_reason": reason,
        }

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(self, raw_payload: dict, duration_ms: int = 0) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to ingest.bronze.newsapi.

        Partition key: source.id — falls back to source.name if id is absent.
        All articles from the same publisher land in the same Kafka partition,
        ensuring the Silver Job's dedup logic sees them together (consistent
        with series_id keying in the FRED producer).

        Args:
            raw_payload:  Dict produced by _build_raw_payload().
            duration_ms:  HTTP request duration to include in Bronze metadata.
        """
        source = raw_payload["source"]
        partition_key = source["id"] or source["name"] or "unknown"

        endpoint = (
            f"{TOP_HEADLINES_ENDPOINT}?category={raw_payload['category']}"
            if raw_payload["fetch_mode"] == "pulse"
            else EVERYTHING_ENDPOINT
        )

        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=endpoint,
            raw_payload=raw_payload,
            http_status_code=200,
            request_duration_ms=duration_ms,
        )
        self._producer.send(BRONZE_NEWSAPI, value=msg, key=partition_key)
        self._emitted += 1

    # ----------------------------------------------------------
    # Shared Filter + Emit Loop
    # ----------------------------------------------------------

    def _process_and_emit(
        self,
        articles: list[dict],
        category: str,
        fetch_mode: str,
        duration_ms: int,
    ) -> int:
        """
        Apply whitelist + keyword sniper, build payload, emit passing articles.

        Keyword Sniper is applied only when category == GENERAL_CATEGORY (Section B.4).
        All other categories — including the empty-string category used for backfill
        articles — pass through the whitelist check only.

        Returns:
            Count of articles emitted to Bronze in this call.
        """
        emitted = 0
        for article in articles:
            # Gate 1 — Authority whitelist (Section B.4, always applied)
            if not self._passes_whitelist(article):
                self._filtered_whitelist += 1
                continue

            # Gate 2 — Keyword Sniper on General category only (Section B.4)
            if category == GENERAL_CATEGORY and not self._passes_keyword_sniper(article):
                self._filtered_keyword += 1
                continue

            raw_payload = self._build_raw_payload(article, category, fetch_mode)
            self._emit(raw_payload, duration_ms)
            emitted += 1

        return emitted

    # ----------------------------------------------------------
    # Pulse Mode — 15-30 min polling
    # ----------------------------------------------------------

    def run_pulse(self) -> int:
        """
        Fetch and emit one page of top-headlines for all pulse categories.

        Designed to be called every 15-30 min by an Airflow DAG (Section 2.3 / B.4).
        pageSize=100 per category covers the typical inter-run article volume.
        No pagination in pulse mode: the most recent 100 articles per category
        are sufficient for a 15-min window; any overflow is picked up in the
        next run due to overlapping article recency windows.

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = self._filtered_whitelist = self._filtered_keyword = 0

        logger.info(
            "[newsapi] Pulse run starting — %d standard categories + general (keyword-sniped)",
            len(PULSE_CATEGORIES),
        )

        all_categories = PULSE_CATEGORIES + [GENERAL_CATEGORY]
        for category in all_categories:
            try:
                articles, _, duration_ms = self._fetch_top_headlines(category)
                emitted = self._process_and_emit(articles, category, "pulse", duration_ms)
                logger.info(
                    "[newsapi] category=%-12s fetched=%d  emitted=%d  "
                    "wl_filtered=%d  kw_filtered=%d",
                    category, len(articles), emitted,
                    self._filtered_whitelist, self._filtered_keyword,
                )
            except requests.HTTPError as exc:
                logger.error(
                    "[newsapi] HTTP error for category=%s: %s — skipping", category, exc
                )
            except requests.RequestException as exc:
                logger.error(
                    "[newsapi] Network error for category=%s: %s — skipping", category, exc
                )
            except (KeyError, ValueError) as exc:
                logger.error(
                    "[newsapi] Parse error for category=%s: %s — skipping", category, exc
                )
            finally:
                time.sleep(REQUEST_DELAY_SEC)

        self._producer.flush()
        logger.info(
            "[newsapi] Pulse run complete — emitted=%d  "
            "wl_filtered=%d  kw_filtered=%d",
            self._emitted, self._filtered_whitelist, self._filtered_keyword,
        )
        return self._emitted

    # ----------------------------------------------------------
    # Backfill Mode — tiered historical load
    # ----------------------------------------------------------

    def _backfill_date_range(
        self,
        from_date: date,
        to_date: date,
        sources: Optional[list[str]],
        tier_name: str,
        keywords: Optional[str] = None,
    ) -> int:
        """
        Paginate through /v2/everything for one date range and emit passing articles.

        Pagination continues until either the last page is reached (fewer articles
        returned than PAGE_SIZE) or the NewsAPI 100-page hard limit is hit
        (10,000 articles per query). If a range exceeds 10,000 articles, the
        remaining tail is silently dropped — this is acceptable for backfill since
        the most-recent 10,000 articles in any single range are sufficient for
        the RAG context window.

        Args:
            from_date:  Inclusive start date.
            to_date:    Inclusive end date.
            sources:    List of source IDs to filter by (API-level filter).
                        None = no API filter, authority whitelist applied client-side.
            tier_name:  Label for log messages ("full", "tier_one", "anomaly").
            keywords:   Optional q-parameter keyword string.

        Returns:
            Number of articles emitted for this date range.
        """
        from_str   = from_date.isoformat()
        to_str     = to_date.isoformat()
        fetch_mode = f"backfill_{tier_name}"
        page       = 1
        range_emitted = 0

        while True:
            try:
                articles, total_results, duration_ms = self._fetch_everything(
                    from_date=from_str,
                    to_date=to_str,
                    sources=sources,
                    keywords=keywords,
                    page=page,
                )
            except requests.HTTPError as exc:
                logger.error(
                    "[newsapi] Backfill HTTP error (tier=%s from=%s page=%d): %s — "
                    "stopping pagination for this range",
                    tier_name, from_str, page, exc,
                )
                break
            except requests.RequestException as exc:
                logger.error(
                    "[newsapi] Backfill network error (tier=%s from=%s page=%d): %s — "
                    "stopping pagination for this range",
                    tier_name, from_str, page, exc,
                )
                break

            if not articles:
                break

            # Category is empty string for backfill — no keyword sniper applied
            emitted = self._process_and_emit(articles, "", fetch_mode, duration_ms)
            range_emitted += emitted

            logger.info(
                "[newsapi] Backfill tier=%-10s from=%s page=%3d — "
                "emitted=%d  range_total=%d",
                tier_name, from_str, page, emitted, range_emitted,
            )

            # Stop if this is the last page
            is_last_page = len(articles) < PAGE_SIZE or page * PAGE_SIZE >= total_results
            if is_last_page:
                break

            page += 1
            time.sleep(REQUEST_DELAY_SEC)

        return range_emitted

    def run_backfill(self, keywords: Optional[str] = None) -> int:
        """
        Execute the tiered historical backfill (Section B.4).

        Tier 1 — Full density (0-6 months):
            All authority sources, no API-level source filter.
            Client-side whitelist applied. Covers recent high-density period.

        Tier 2 — Tier-1 sources (6-24 months):
            Restricted to TIER_ONE_SOURCE_IDS (top 8 global wire/financial sources).
            Reduces data volume for the older period while preserving
            authoritative coverage of major events.

        Tier 3 — Anomaly events (2-5 years):
            Only executed if `keywords` argument is supplied.
            Uses tier-1 source list + keyword query to surface major geopolitical
            or market-anomaly events (e.g., "oil crisis OR NATO expansion").
            Skipped if no keywords provided — anomaly dates vary by use case
            and should be supplied by the operator at backfill invocation time.

        Args:
            keywords: Keyword query string for the anomaly-tier scan (Tier 3).
                      If None, Tier 3 is skipped and a warning is logged.

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = self._filtered_whitelist = self._filtered_keyword = 0
        today = date.today()
        total = 0

        # --- Tier 1: Full density (0–6 months) ---
        full_start = today - timedelta(days=BACKFILL_FULL_MONTHS * 30)
        logger.info(
            "[newsapi] Backfill Tier 1 (full density): %s → %s",
            full_start.isoformat(), today.isoformat(),
        )
        total += self._backfill_date_range(
            full_start, today, sources=None, tier_name="full"
        )
        time.sleep(REQUEST_DELAY_SEC)

        # --- Tier 2: Tier-1 sources (6–24 months) ---
        tier1_end   = full_start - timedelta(days=1)
        tier1_start = today - timedelta(days=BACKFILL_TIER_ONE_MONTHS * 30)
        logger.info(
            "[newsapi] Backfill Tier 2 (tier-1 sources): %s → %s — %d source IDs",
            tier1_start.isoformat(), tier1_end.isoformat(), len(TIER_ONE_SOURCE_IDS),
        )
        total += self._backfill_date_range(
            tier1_start, tier1_end,
            sources=TIER_ONE_SOURCE_IDS,
            tier_name="tier_one",
        )
        time.sleep(REQUEST_DELAY_SEC)

        # --- Tier 3: Anomaly events (2–5 years) — optional ---
        if keywords:
            anomaly_end   = tier1_start - timedelta(days=1)
            anomaly_start = today - timedelta(days=BACKFILL_MAX_YEARS * 365)
            logger.info(
                "[newsapi] Backfill Tier 3 (anomaly events): %s → %s — keywords='%s'",
                anomaly_start.isoformat(), anomaly_end.isoformat(), keywords,
            )
            total += self._backfill_date_range(
                anomaly_start, anomaly_end,
                sources=TIER_ONE_SOURCE_IDS,
                tier_name="anomaly",
                keywords=keywords,
            )
        else:
            logger.info(
                "[newsapi] Backfill Tier 3 (anomaly events) skipped — "
                "supply --keywords='<query>' to enable the 2-5 year anomaly scan."
            )

        self._producer.flush()
        logger.info(
            "[newsapi] Backfill complete — emitted=%d  wl_filtered=%d  kw_filtered=%d",
            self._emitted, self._filtered_whitelist, self._filtered_keyword,
        )
        return self._emitted

    # ----------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------

    def close(self) -> None:
        """
        Flush and close the Kafka producer connection cleanly.

        Must be called in the finally block of main() to prevent message loss —
        kafka-python-ng buffers messages in memory and flush() forces a
        synchronous drain before exit (same pattern as fred_producer.py).
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[newsapi] Producer closed cleanly.")


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(mode: str = "pulse", keywords: Optional[str] = None) -> None:
    """
    Entry point for Docker container and Airflow BashOperator invocation.

    Modes:
        pulse    (default) — 15-30 min top-headlines polling.
        backfill           — tiered historical load (Tier 1+2 always; Tier 3 if --keywords).

    Usage:
        python -m ingestion.newsapi_producer                                  # pulse
        python -m ingestion.newsapi_producer backfill                         # backfill T1+T2
        python -m ingestion.newsapi_producer backfill --keywords "oil crisis" # backfill T1+T2+T3
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = NewsAPIProducer()
    try:
        if mode == "backfill":
            producer.run_backfill(keywords=keywords)
        else:
            producer.run_pulse()
    finally:
        producer.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="NewsAPI Bronze Producer")
    parser.add_argument(
        "mode",
        nargs="?",
        default="pulse",
        choices=["pulse", "backfill"],
        help="pulse (default) or backfill",
    )
    parser.add_argument(
        "--keywords",
        default=None,
        help="Keyword query for anomaly-tier backfill (Tier 3). "
             "Example: 'oil crisis OR NATO expansion'",
    )
    args = parser.parse_args()
    main(args.mode, keywords=args.keywords)
