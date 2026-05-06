"""
NewsAPI Producer — TheNewsAPI REST, 15-30 min polling.

Fetches top headlines from authority-whitelisted domains across Business,
Tech, Health, Science, and General categories. Applies the Keyword Sniper
pre-filter on the 'general' category before Bronze emission — only articles
matching at least one keyword in GENERAL_KEYWORDS are forwarded. All other
categories pass through the domain whitelist check only (server-side via
TheNewsAPI's `domains` parameter, plus a defensive client-side recheck).

Sprint 21.5 migration (2026-05-06): replaced newsapi.org with thenewsapi.com.
Why: newsapi.org imposes a 24-hour delay on free/paid articles, blocking
same-day signal for the RAG agent. TheNewsAPI returns real-time articles.
The producer is the API-shape boundary (Section 3.3 Service Isolation):
TheNewsAPI's response (data[] of articles with a domain-string `source` field
and a `categories[]` array) is normalized into the same internal raw_payload
contract Silver/Gold consumed before — preserving all downstream logic.

Note on 'politics' category: TheNewsAPI does not expose a 'politics' category.
Political content is captured via the 'general' category combined with the
Keyword Sniper filter (Section 2.2).

Two operating modes (Section 2.3 / B.4):
  - pulse    (default) — one page of /v1/news/top per category per pulse run.
                         Designed to be called every 15-30 min by an Airflow DAG.
  - backfill            — tiered historical load via /v1/news/all:
                           Tier 1: full density, 0-6 months, all authority domains
                           Tier 2: tier-1 domains only, 6-24 months
                           Tier 3: tier-1 + keyword search, 2-5 years (anomaly events;
                                   only executed if --keywords is supplied)

Why pre-filter at the producer (not only in Flink):
    Section 2.2 mandates "Only high-signal data enters Bronze Kafka topics."
    Pushing noise into Bronze wastes Kafka retention, Silver processing cycles,
    and Silver DLQ capacity. The whitelist + General Keyword Sniper gates are
    cheap string operations; running them here costs nothing. The Silver Job's
    keyword_sniper.py module applies a more sophisticated relevance-scoring pass
    inside Flink (Section 4.1A).

Why send `domains=<whitelist>` server-side instead of client-side filtering:
    TheNewsAPI Free tier returns only 3 articles/request; client-side filtering
    after the fact would waste the entire request budget on rejected articles.
    Server-side `domains=` filter at the API layer guarantees every returned
    article is from a whitelisted publisher — every article counts toward
    high-signal Bronze emission. The defensive client-side `_passes_whitelist`
    check is retained as an integrity guard (in case TheNewsAPI ever returns
    an article whose `source` field is not on the whitelist).

Why embed impact_boost flag in raw_payload:
    Section B.4 requires a +1 boost to impact_level for Israeli/Middle East
    security or energy articles. Embedding the boolean flag and matched keyword
    at Bronze time makes the Gold Job stateless — it reads the flag rather than
    re-scanning article text, keeping Section 3.3 Service Isolation intact.

Why source.id is derived by stripping the TLD from the domain:
    TheNewsAPI's `source` is a bare domain string (e.g. "reuters.com"). The
    Silver Job's SHA-256 dedup keys on the article URL, not on source.id, so
    changing the source.id derivation rule does not break dedup. The TLD-strip
    rule yields stable lowercase ids that group articles by publisher into the
    same Kafka partition (matching the FRED producer's series_id keying pattern).

Partition key: source.id — groups articles by publisher into the same Kafka
    partition. The Silver Job's SHA-256 dedup sees all articles from the same
    source on the same consumer, making hash collision detection consistent.
    Falls back to source.name if id is somehow absent.

Kafka Target: ingest.bronze.newsapi (BRONZE_NEWSAPI)
Source name on the wire: "newsapi" — preserved across the migration so that
    Silver routing, knowledge_vault rows, and knowledge_vectors.source_platform
    stay stable; no schema migration required.

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
    - Section 9.1:  Environment parity — THE_NEWS_API_KEY from .env
"""

from __future__ import annotations
import logging
import sys
import time
from datetime import date, timedelta
from typing import Optional

import requests

from config.kafka_topics import BRONZE_NEWSAPI
from config.settings import THE_NEWS_API_KEY, THE_NEWS_API_PAGE_SIZE
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# TheNewsAPI Constants (Section B.4)
# ==========================================================

# Wire-protocol source name. Preserved as "newsapi" across the
# newsapi.org → thenewsapi.com migration so Silver routing and all
# downstream knowledge_vault / knowledge_vectors rows stay stable.
SOURCE_NAME = "newsapi"

NEWSAPI_BASE_URL       = "https://api.thenewsapi.com/v1/news"
TOP_HEADLINES_ENDPOINT = f"{NEWSAPI_BASE_URL}/top"   # /v1/news/top — replaces /v2/top-headlines
EVERYTHING_ENDPOINT    = f"{NEWSAPI_BASE_URL}/all"   # /v1/news/all — replaces /v2/everything

# Articles returned per request. Driven by env var:
#   THE_NEWS_API_PAGE_SIZE=3   → Free tier (dev/testing)
#   THE_NEWS_API_PAGE_SIZE=100 → Basic tier (production)
PAGE_SIZE = THE_NEWS_API_PAGE_SIZE

# Inter-request delay. TheNewsAPI Basic plan tolerates ~1 req/s sustained;
# matches the previous newsapi.org cadence so the DAG behavior is unchanged.
REQUEST_DELAY_SEC = 1.0

# Categories fetched in pulse mode via /v1/news/top.
# TheNewsAPI uses 'tech' (not 'technology'); other names match newsapi.org's.
PULSE_CATEGORIES = ["business", "tech", "health", "science"]

# The 'general' category is fetched separately and also keyword-sniped
# because it mixes high- and low-signal content.
GENERAL_CATEGORY = "general"


# ==========================================================
# Authority Whitelist — Domains (Section B.4)
# ==========================================================
# Matched against TheNewsAPI's `source` field (a bare domain string), and
# also passed server-side via the `domains` query parameter so the API only
# returns articles already on the whitelist. The client-side recheck is a
# defensive integrity guard — see module docstring.
#
# Removed in Sprint 21.5: i24news.tv (not on TheNewsAPI), timesofisrael.com
# (not on TheNewsAPI), kan11/Kan 11 (no domain on TheNewsAPI).

AUTHORITY_WHITELIST: frozenset[str] = frozenset({
    # --- Global wire & financial press ---
    "reuters.com",
    "apnews.com",
    "wsj.com",
    "bloomberg.com",
    "nytimes.com",
    "washingtonpost.com",
    "cnbc.com",
    "cnn.com",
    "bbc.co.uk",
    "ft.com",
    "theguardian.com",
    "economist.com",
    # --- Israeli & Middle East outlets ---
    "jpost.com",
    "ynetnews.com",
    "ynet.co.il",
})


# ==========================================================
# Domain → Display Name Mapping (Section B.4)
# ==========================================================
# Used to populate `raw_payload.source.name` so the Silver Job's
# knowledge_vault rows retain a human-readable publisher field.
# Why this lookup: TheNewsAPI exposes only the domain — there is no display
# name in its response shape, unlike newsapi.org which returned source.name.
# Keeping the display-name string preserves UX downstream (RAG citations,
# knowledge_vault.source_publisher rendering).

DOMAIN_DISPLAY_NAMES: dict[str, str] = {
    "reuters.com":         "Reuters",
    "apnews.com":          "Associated Press",
    "wsj.com":             "The Wall Street Journal",
    "bloomberg.com":       "Bloomberg",
    "nytimes.com":         "The New York Times",
    "washingtonpost.com":  "The Washington Post",
    "cnbc.com":            "CNBC",
    "cnn.com":             "CNN",
    "bbc.co.uk":           "BBC News",
    "ft.com":              "Financial Times",
    "theguardian.com":     "The Guardian",
    "economist.com":       "The Economist",
    "jpost.com":           "Jerusalem Post",
    "ynetnews.com":        "Ynetnews",
    "ynet.co.il":          "Ynet",
}


# ==========================================================
# Tier-1 Backfill Domains (Section B.4)
# ==========================================================
# Subset of the full whitelist used for the 6-24 month backfill window.
# Highest-reliability global wire/financial sources only. Direct domain
# translation of the previous TIER_ONE_SOURCE_IDS list (Sprint 21.5).

TIER_ONE_DOMAINS: list[str] = [
    "reuters.com",
    "apnews.com",
    "bloomberg.com",
    "wsj.com",
    "nytimes.com",
    "washingtonpost.com",
    "bbc.co.uk",
    "ft.com",
]


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

BACKFILL_FULL_MONTHS     = 6     # Tier 1: full density window (months from today)
BACKFILL_TIER_ONE_MONTHS = 24    # Tier 2: tier-1-only window (months from today)
BACKFILL_MAX_YEARS       = 5     # Tier 3: anomaly scan upper bound (years from today)


# ==========================================================
# Domain → source.id Derivation
# ==========================================================
# Why TLD-stripping: TheNewsAPI's `source` field is the bare publisher domain
# (e.g. "reuters.com", "bbc.co.uk"). The Silver Job's SHA-256 dedup keys on
# the article URL — not on source.id — so the chosen derivation rule has no
# functional effect on dedup. We strip the public TLD to produce a stable
# lowercase id (reuters.com → reuters, bbc.co.uk → bbc, ynet.co.il → ynet)
# that groups articles by publisher into the same Kafka partition.

def _domain_to_source_id(domain: str) -> str:
    """
    Convert a publisher domain to a stable lowercase source.id.

    Rule: take the leftmost label of the domain. Handles compound TLDs
    (.co.uk, .co.il) implicitly because the leftmost label is what we want.
    Returns "" for empty input. Caller is responsible for ensuring the input
    has already been lowercased and stripped.
    """
    if not domain:
        return ""
    # Leftmost label is the brand: "bbc.co.uk" → "bbc", "reuters.com" → "reuters".
    return domain.split(".", 1)[0]


# ==========================================================
# Producer
# ==========================================================

class NewsAPIProducer:
    """
    Scheduled REST producer for TheNewsAPI top headlines.

    Pulse mode polls /v1/news/top every 15-30 min for each category, with the
    full authority whitelist sent server-side via the `domains` parameter.
    Backfill mode crawls /v1/news/all across three date-range tiers.

    Both modes apply the authority whitelist (server-side filter + defensive
    client-side recheck); only the General category additionally requires a
    Keyword Sniper hit before Bronze emission.

    Emits to: ingest.bronze.newsapi (BRONZE_NEWSAPI)
    """

    def __init__(self) -> None:
        if not THE_NEWS_API_KEY:
            raise ValueError(
                "[newsapi] THE_NEWS_API_KEY not set in .env — cannot authenticate. "
                "Set THE_NEWS_API_KEY to your thenewsapi.com api_token (Section B.4)."
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
        Return True if the article's source domain is on the authority whitelist.

        TheNewsAPI's `source` field is a bare domain string. The check is
        case-insensitive and trims whitespace to guard against minor formatting
        differences across API responses. This is a defensive recheck of the
        server-side `domains=` filter (see module docstring).
        """
        domain = (article.get("source") or "").strip().lower()
        return domain in AUTHORITY_WHITELIST

    def _passes_keyword_sniper(self, article: dict) -> bool:
        """
        Return True if the article's title or description contains at least one
        GENERAL_KEYWORDS term. Applied to General category only (Section B.4).

        Why title + description (not full content): the producer pre-filter needs
        only a fast yes/no decision. TheNewsAPI's `snippet` field is a short
        excerpt, not full content, making it less reliable than title+description
        for this gate. The Silver Job's keyword_sniper.py applies full relevance
        scoring on the complete document body inside Flink (Section 4.1A).
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
    # TheNewsAPI REST Fetch
    # ----------------------------------------------------------

    def _whitelist_domains_param(self) -> str:
        """
        Build the comma-separated `domains=` parameter from AUTHORITY_WHITELIST.

        Why pre-built once: TheNewsAPI accepts the full domain list per request,
        and serializing the frozenset on every call would be wasteful. Sorted
        deterministically so log output and request signatures are stable.
        """
        return ",".join(sorted(AUTHORITY_WHITELIST))

    def _fetch_top_headlines(
        self,
        category: str,
        page: int = 1,
    ) -> tuple[list[dict], int, int]:
        """
        Fetch one page of /v1/news/top for a given category.

        Why no `locale` filter: the authority whitelist already constrains the
        publisher set to English-language outlets we trust. Adding locale=us
        would drop UK and Israeli sources that are explicitly on the whitelist.

        Why `language=en`: ensures non-English wire copies of the same article
        (e.g. Hebrew Ynet articles) do not leak into Bronze; downstream NLP
        operates on English. Translation is scoped to Telegram only (§4.1D).

        Args:
            category: TheNewsAPI category string ("business", "tech", "health",
                      "science", "general").
            page:     1-indexed page number.

        Returns:
            (articles, total_results, duration_ms)

        Raises:
            requests.HTTPError: On non-2xx responses.
            requests.RequestException: On network failures.
        """
        params: dict = {
            "api_token":  THE_NEWS_API_KEY,
            "categories": category,
            "domains":    self._whitelist_domains_param(),
            "language":   "en",
            "limit":      PAGE_SIZE,
            "page":       page,
        }
        response, duration_ms = timed_request(
            lambda: requests.get(TOP_HEADLINES_ENDPOINT, params=params, timeout=20)
        )
        response.raise_for_status()

        data          = response.json()
        articles      = data.get("data") or []
        meta          = data.get("meta") or {}
        total_results = meta.get("found") or 0

        logger.debug(
            "[newsapi] /top categories=%s page=%d — %d articles "
            "(found=%d, %dms)",
            category, page, len(articles), total_results, duration_ms,
        )
        return articles, total_results, duration_ms

    def _fetch_everything(
        self,
        from_date: str,
        to_date: str,
        domains: Optional[list[str]] = None,
        keywords: Optional[str] = None,
        page: int = 1,
    ) -> tuple[list[dict], int, int]:
        """
        Fetch one page from /v1/news/all — used for tiered backfill.

        Why `published_after` / `published_before`: TheNewsAPI's date-range
        parameters on the /all endpoint. Replaces newsapi.org's `from` / `to`.

        Why `search` (not `q`): TheNewsAPI's keyword query parameter name.
        Same Boolean OR/AND syntax as before, e.g. "oil crisis OR NATO".

        Why `language=en`: see _fetch_top_headlines.

        Args:
            from_date: ISO date "YYYY-MM-DD" (inclusive).
            to_date:   ISO date "YYYY-MM-DD" (inclusive).
            domains:   List of TheNewsAPI domains. None = full authority whitelist
                       sent server-side.
            keywords:  Optional keyword query string for the `search` parameter.
            page:      1-indexed page number.

        Returns:
            (articles, total_results, duration_ms)
        """
        domains_param = ",".join(domains) if domains else self._whitelist_domains_param()
        params: dict = {
            "api_token":        THE_NEWS_API_KEY,
            "domains":          domains_param,
            "published_after":  from_date,
            "published_before": to_date,
            "language":         "en",
            "limit":            PAGE_SIZE,
            "page":             page,
        }
        if keywords:
            params["search"] = keywords

        response, duration_ms = timed_request(
            lambda: requests.get(EVERYTHING_ENDPOINT, params=params, timeout=30)
        )
        response.raise_for_status()

        data          = response.json()
        articles      = data.get("data") or []
        meta          = data.get("meta") or {}
        total_results = meta.get("found") or 0

        logger.debug(
            "[newsapi] /all from=%s to=%s page=%d — %d articles "
            "(found=%d, %dms)",
            from_date, to_date, page, len(articles), total_results, duration_ms,
        )
        return articles, total_results, duration_ms

    # ----------------------------------------------------------
    # Payload Builder — TheNewsAPI → Internal raw_payload Contract
    # ----------------------------------------------------------

    def _build_raw_payload(
        self,
        article: dict,
        category: str,
        fetch_mode: str,
    ) -> dict:
        """
        Construct the raw_payload dict for one TheNewsAPI article.

        This method is the API-shape boundary (Section 3.3 Service Isolation):
        TheNewsAPI's response shape (data[i] with `image_url`, `snippet`,
        `source` as a domain string, `categories[]` as an array) is normalized
        into the same internal contract Silver/Gold consumed before the Sprint
        21.5 migration — preserving downstream logic verbatim (Decision D1).

        Field mapping (TheNewsAPI → internal):
            url           → article_id (canonical SHA-256 dedup key, §4.1C)
            title         → title
            description   → description
            url           → url
            image_url     → url_to_image
            published_at  → published_at (ISO8601, pass-through)
            snippet       → content (short excerpt; TheNewsAPI has no full body)
            (none)        → author = "" (TheNewsAPI does not expose author)
            source domain → source.id (TLD-stripped) + source.name (display lookup)
            categories[0] → category (override only if no fetch-time category)

        Why pass-through behavior on `categories`: TheNewsAPI returns an array
        of category labels per article. We use the fetch-time category from the
        producer call (which is the gate that selected this article) and store
        it in the same internal `category` field; this preserves the contract
        used by the Silver Job and Gate 2 tests.

        Why content may be a short snippet: TheNewsAPI's `snippet` field is a
        truncated excerpt, similar to newsapi.org's truncated `content`. The
        Silver Job stores the description as inverted_pyramid_lead and uses
        the URL for the full-body scraper that runs downstream (Sprint 15+).

        Args:
            article:    Raw article dict from TheNewsAPI's data[] array.
            category:   Category string used to fetch this article ("business",
                        "general", etc.). Empty string for backfill articles
                        (fetched via /v1/news/all with no category filter).
            fetch_mode: "pulse" | "backfill_full" | "backfill_tier_one" |
                        "backfill_anomaly"
        """
        domain        = (article.get("source") or "").strip().lower()
        source_id     = _domain_to_source_id(domain)
        source_name   = DOMAIN_DISPLAY_NAMES.get(domain, domain)
        boost, reason = self._impact_boost_info(article)

        return {
            # Core article fields (normalized from TheNewsAPI shape)
            "article_id":    article.get("url") or "",
            "title":         article.get("title") or "",
            "description":   article.get("description") or "",
            "url":           article.get("url") or "",
            "url_to_image":  article.get("image_url") or "",
            "published_at":  article.get("published_at") or "",
            "content":       article.get("snippet") or "",
            "author":        "",  # TheNewsAPI does not expose author
            "source": {
                "id":   source_id,
                "name": source_name,
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
            f"{TOP_HEADLINES_ENDPOINT}?categories={raw_payload['category']}"
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
            # Gate 1 — Authority whitelist (defensive client-side recheck of
            # the server-side `domains=` filter; Section B.4)
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
        Fetch and emit one page of /v1/news/top for all pulse categories.

        Designed to be called every 15-30 min by an Airflow DAG (Section 2.3 / B.4).
        PAGE_SIZE per category covers the typical inter-run article volume on the
        Basic plan (limit=100). On the Free plan (limit=3) the volume is small but
        sufficient for dev/testing — overflow is implicitly captured on the next run.

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = self._filtered_whitelist = self._filtered_keyword = 0

        logger.info(
            "[newsapi] Pulse run starting — %d standard categories + general "
            "(keyword-sniped); page_size=%d",
            len(PULSE_CATEGORIES), PAGE_SIZE,
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
        domains: Optional[list[str]],
        tier_name: str,
        keywords: Optional[str] = None,
    ) -> int:
        """
        Paginate through /v1/news/all for one date range and emit passing articles.

        Pagination continues until either the last page is reached (fewer articles
        returned than PAGE_SIZE) or TheNewsAPI returns an empty page. TheNewsAPI's
        `meta.found` total is used to detect the final page deterministically.

        Args:
            from_date:  Inclusive start date.
            to_date:    Inclusive end date.
            domains:    List of TheNewsAPI domains to filter by. None = full
                        authority whitelist (server-side).
            tier_name:  Label for log messages ("full", "tier_one", "anomaly").
            keywords:   Optional `search` query string.

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
                    domains=domains,
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
            All authority domains via the full whitelist server-side.
            Covers recent high-density period.

        Tier 2 — Tier-1 domains (6-24 months):
            Restricted to TIER_ONE_DOMAINS (top 8 global wire/financial sources).
            Reduces data volume for the older period while preserving
            authoritative coverage of major events.

        Tier 3 — Anomaly events (2-5 years):
            Only executed if `keywords` argument is supplied.
            Uses tier-1 domain list + `search` query to surface major
            geopolitical or market-anomaly events (e.g. "oil crisis OR NATO").
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

        # --- Tier 1: Full density (0-6 months) ---
        full_start = today - timedelta(days=BACKFILL_FULL_MONTHS * 30)
        logger.info(
            "[newsapi] Backfill Tier 1 (full density): %s → %s",
            full_start.isoformat(), today.isoformat(),
        )
        total += self._backfill_date_range(
            full_start, today, domains=None, tier_name="full"
        )
        time.sleep(REQUEST_DELAY_SEC)

        # --- Tier 2: Tier-1 domains (6-24 months) ---
        tier1_end   = full_start - timedelta(days=1)
        tier1_start = today - timedelta(days=BACKFILL_TIER_ONE_MONTHS * 30)
        logger.info(
            "[newsapi] Backfill Tier 2 (tier-1 domains): %s → %s — %d domains",
            tier1_start.isoformat(), tier1_end.isoformat(), len(TIER_ONE_DOMAINS),
        )
        total += self._backfill_date_range(
            tier1_start, tier1_end,
            domains=TIER_ONE_DOMAINS,
            tier_name="tier_one",
        )
        time.sleep(REQUEST_DELAY_SEC)

        # --- Tier 3: Anomaly events (2-5 years) — optional ---
        if keywords:
            anomaly_end   = tier1_start - timedelta(days=1)
            anomaly_start = today - timedelta(days=BACKFILL_MAX_YEARS * 365)
            logger.info(
                "[newsapi] Backfill Tier 3 (anomaly events): %s → %s — keywords='%s'",
                anomaly_start.isoformat(), anomaly_end.isoformat(), keywords,
            )
            total += self._backfill_date_range(
                anomaly_start, anomaly_end,
                domains=TIER_ONE_DOMAINS,
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
# Backwards-compatibility export — TIER_ONE_SOURCE_IDS
# ==========================================================
# Retained as an alias of TIER_ONE_DOMAINS to avoid breaking any test or
# import that still references the pre-Sprint-21.5 name. New code should
# import TIER_ONE_DOMAINS directly. Drop in a follow-up sprint.
TIER_ONE_SOURCE_IDS = TIER_ONE_DOMAINS


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(mode: str = "pulse", keywords: Optional[str] = None) -> None:
    """
    Entry point for Docker container and Airflow PythonOperator invocation.

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

    parser = argparse.ArgumentParser(description="TheNewsAPI Bronze Producer")
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
