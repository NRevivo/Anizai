"""
NewsAPI Producer — newsapi.ai (Event Registry) REST, 15-30 min polling.

Phase 7A migration (2026-05-09): replaced TheNewsAPI (api.thenewsapi.com) with
newsapi.ai (eventregistry.org). The producer is the API-shape boundary (Section
3.3 Service Isolation): all changes are confined to this file and its config/tests.
Wire-protocol source_name="newsapi" and Kafka topic BRONZE_NEWSAPI are preserved —
no schema migration, no downstream churn.

Why newsapi.ai:
    TheNewsAPI's `snippet` field is at most 60 chars, providing only a truncated
    excerpt. newsapi.ai returns the full article body (articleBodyLen=-1),
    which is required for Phase 7B's semantic rescue calibration and for the
    hub's RAG drill-down path (knowledge_vault.full_text_raw, Section 8.3.1).

Category model (Phase 7A):
    news/Politics added as an explicit category (was not available on TheNewsAPI;
    political content was previously captured via the "general" bucket + the
    GENERAL_KEYWORDS pre-filter, which raised both noise and false positives).
    The "news" root category was tested live (T7A.2) and returns 0 results on
    newsapi.ai — it is not a valid standalone category filter and is omitted.
    GENERAL_KEYWORDS pre-filter removed from the producer: it existed only to
    gate the noisy "news" root bucket, which no longer exists. All 5 categories
    now pass through the authority whitelist uniformly (no per-category gating).
    The GENERAL_KEYWORDS list itself is preserved in keyword_sniper.py for
    reference; its removal from MASTER_KEYWORD_LIST is a Phase 7B decision.

API shape changes (TheNewsAPI → newsapi.ai, decision M1-M9):
    Endpoint:   /v1/news/top + /v1/news/all → single /api/v1/article/getArticles
    Auth:       api_token= → apiKey=
    Source:     domains= (csv) → sourceUri= (csv)
                source field: bare domain string → {uri, title} dict
    Categories: categories= (slug) → categoryUri= (URI notation)
    Pagination: page/limit → articlesPage/articlesCount
    Body:       snippet (60 char) → body (full text via articleBodyLen=-1)
    Date:       published_at → dateTime
    Image:      image_url → image
    Authors:    absent → authors[] (list of objects with name/uri keys)

Internal raw_payload contract (unchanged — Silver/Gold consume these verbatim):
    article_id, title, description, url, url_to_image, published_at, content,
    author, source.id, source.name, category, fetch_mode, impact_boost,
    impact_boost_reason — all field names identical to the pre-7A contract.

Two operating modes (Section 2.3 / B.4):
  - pulse    (default) — one page of getArticles per category per run.
                         Called every 15-30 min by an Airflow DAG.
  - backfill            — tiered historical load via date range params:
                           Tier 1: full density, 0-6 months, all authority domains
                           Tier 2: tier-1 domains only, 6-24 months
                           Tier 3: tier-1 domains + keyword search, 2-5 years

Why pre-filter at the producer (not only in Flink):
    Section 2.2 mandates "Only high-signal data enters Bronze Kafka topics."
    The authority whitelist is a cheap domain-set lookup; running it here costs
    nothing and prevents junk from consuming Kafka retention. Silver's
    keyword_sniper.py applies full relevance scoring at the Flink layer (§4.1A).

Why send sourceUri= server-side:
    The API returns articles only from the declared source URIs — every returned
    article is from a whitelisted publisher. The defensive client-side
    _passes_whitelist recheck guards against any API-side slip.

Why embed impact_boost in raw_payload:
    Section B.4 requires +1 to impact_level for Israeli/Middle East security or
    energy articles. Embedding the boolean flag at Bronze time makes the Gold Job
    stateless — it reads the flag rather than re-scanning text (Section 3.3).

Why source.id is derived by stripping the TLD from source.uri:
    newsapi.ai's source.uri is a bare domain (e.g. "reuters.com", "bbc.co.uk").
    The Silver Job's SHA-256 dedup keys on article URL, not source.id, so the
    derivation rule has no effect on dedup. TLD-stripping yields stable lowercase
    ids that group articles by publisher into the same Kafka partition.

Partition key: source.id — groups articles by publisher into the same Kafka
    partition. Falls back to source.name if id is absent.

Kafka Target: ingest.bronze.newsapi (BRONZE_NEWSAPI)
Source name on the wire: "newsapi" — preserved across all migrations so that
    Silver routing, knowledge_vault rows, and knowledge_vectors.source_platform
    stay stable; no schema migration required.

References:
    - Section 2.1:  Producer Matrix (NewsAPI row)
    - Section 2.2:  SNR Optimization — Domain Filtering Strategy
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section B.4:  NewsAPI technical parameters, Authority Whitelist,
                    Tiered Backfill, Impact Boost
    - Section C.1:  Bronze Schema (build_bronze_message wraps every payload)
    - Section C.3:  Silver Full-Text Document Store — fields this producer populates
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.1A: Keyword Sniper Filter (Silver-level; producer no longer pre-filters)
    - Section 9.1:  Environment parity — NEWSAI_API_KEY from .env
"""

from __future__ import annotations
import logging
import sys
import time
from datetime import date, timedelta
from typing import Optional

import requests

from config.kafka_topics import BRONZE_NEWSAPI
from config.settings import NEWSAI_API_KEY, NEWSAI_BASE_URL, NEWSAI_PAGE_SIZE
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# newsapi.ai Constants (Section B.4, Phase 7A)
# ==========================================================

# Wire-protocol source name. Preserved as "newsapi" across all migrations so
# Silver routing and all downstream knowledge_vault / knowledge_vectors rows
# stay stable; no schema migration required.
SOURCE_NAME = "newsapi"

NEWSAI_GETARTICLES_URL = f"{NEWSAI_BASE_URL}/article/getArticles"

# Articles returned per request. Driven by env var NEWSAI_PAGE_SIZE.
# Default 10 — newsapi.ai free tier tolerates this. Raise in prod .env.
PAGE_SIZE = NEWSAI_PAGE_SIZE

# Inter-request delay. Matches the previous cadence so DAG behavior is unchanged.
REQUEST_DELAY_SEC = 1.0

# Category URIs confirmed via T7A.2 live validation (all 5 return articles
# with full body). "news" root omitted — returns 0 results on newsapi.ai (T7A.2).
# GENERAL_KEYWORDS pre-filter removed — it gated the "news" root bucket only,
# which no longer exists. All 5 categories pass through uniformly.
PULSE_CATEGORIES: list[str] = [
    "news/Business",
    "news/Technology",
    "news/Health",
    "news/Science",
    "news/Politics",
]


# ==========================================================
# Authority Whitelist — Domains (Section B.4)
# ==========================================================
# Matched against newsapi.ai's source.uri field (a bare domain string) and
# also sent server-side via sourceUri= so the API only returns articles from
# whitelisted publishers. The client-side recheck is a defensive integrity
# guard — see _passes_whitelist and module docstring.

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
# Used as a fallback to populate raw_payload.source.name when newsapi.ai's
# source.title field is absent. Preserves human-readable publisher names for
# knowledge_vault rows and RAG citations. Prefer source.title when present (M7).

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
# Highest-reliability global wire/financial sources only.

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
# newsapi.ai's source.uri is the bare publisher domain (e.g. "reuters.com",
# "bbc.co.uk"). We strip the public TLD to produce a stable lowercase id
# (reuters.com → reuters, bbc.co.uk → bbc, ynet.co.il → ynet) that groups
# articles by publisher into the same Kafka partition.

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
    return domain.split(".", 1)[0]


# ==========================================================
# Producer
# ==========================================================

class NewsAPIProducer:
    """
    Scheduled REST producer for newsapi.ai (Event Registry) articles.

    Pulse mode polls /api/v1/article/getArticles every 15-30 min for each of
    the 5 explicit category URIs, with the full authority whitelist sent
    server-side via sourceUri=. All categories are treated uniformly — no
    per-category keyword pre-filtering (Phase 7A finding: the "news" root
    bucket that warranted the GENERAL_KEYWORDS gate no longer exists).

    Backfill mode crawls the same endpoint across three date-range tiers.

    Both modes apply the authority whitelist (server-side filter + defensive
    client-side recheck via _passes_whitelist).

    Emits to: ingest.bronze.newsapi (BRONZE_NEWSAPI)
    """

    def __init__(self) -> None:
        if not NEWSAI_API_KEY:
            raise ValueError(
                "[newsapi] NEWSAI_API_KEY not set in .env — cannot authenticate. "
                "Set NEWSAI_API_KEY to your eventregistry.org api key (Section B.4)."
            )
        self._producer = make_producer()
        # Running totals — reset each run_pulse / run_backfill call
        self._emitted            = 0
        self._filtered_whitelist = 0

    # ----------------------------------------------------------
    # Filters (Section 2.2, B.4)
    # ----------------------------------------------------------

    def _passes_whitelist(self, article: dict) -> bool:
        """
        Return True if the article's source.uri is on the authority whitelist.

        newsapi.ai returns source as a dict with a uri key (e.g. "reuters.com").
        This differs from TheNewsAPI's bare domain string — the check now reads
        article["source"]["uri"] (M3). Case-insensitive; strips whitespace.
        Defensive client-side recheck of the server-side sourceUri= filter.
        """
        source = article.get("source") or {}
        domain = (source.get("uri") or "").strip().lower()
        return domain in AUTHORITY_WHITELIST

    def _impact_boost_info(self, article: dict) -> tuple[bool, str]:
        """
        Return (needs_boost, matched_term) based on IMPACT_BOOST_TERMS scan.

        Reads title and description — same field names in newsapi.ai as in
        TheNewsAPI (T7A.7 verification: no code change needed). First-match
        only: the Gold Job needs a boolean flag and a single human-readable
        reason for audit trails.
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
    # newsapi.ai REST Fetch (Section B.4, M1, M5, M6)
    # ----------------------------------------------------------

    def _fetch_articles(
        self,
        category: str,
        page: int = 1,
        date_start: Optional[str] = None,
        date_end: Optional[str] = None,
        domains: Optional[list[str]] = None,
        keywords: Optional[str] = None,
    ) -> tuple[list[dict], int, int]:
        """
        Fetch one page from eventregistry.org/api/v1/article/getArticles.

        Single method replaces _fetch_top_headlines + _fetch_everything (M1).
        The endpoint and all required params are the same regardless of mode;
        only date range, articlesSortBy, and optional keyword search differ.

        Why single endpoint: newsapi.ai uses getArticles for all query types.
        Pulse mode adds articlesSortBy=date for recency-ordered results.
        Backfill mode adds dateStart/dateEnd; articlesSortBy is omitted so the
        API applies its natural date-range pagination order (M5).

        Why lang=eng (ISO 639-3): ensures non-English wire copies of the same
        article do not leak into Bronze. Downstream NLP operates on English.

        Why articleBodyLen=-1: requests the full body. Free tier may silently
        cap this; document as a known gap per M9 if so.

        Why sourceUri as multiple separate params (not CSV): newsapi.ai does not
        accept a comma-separated string for sourceUri — each domain must be sent
        as a separate query parameter. Validated via diagnostic call (T7A.14).
        This is why params is a list-of-tuples rather than a dict.

        Args:
            category:   newsapi.ai categoryUri string (e.g. "news/Business").
                        Empty string for backfill — omits categoryUri from params.
            page:       1-indexed page number.
            date_start: ISO date "YYYY-MM-DD" for backfill (dateStart param).
            date_end:   ISO date "YYYY-MM-DD" for backfill (dateEnd param).
            domains:    Domain list for sourceUri override (Tier 2/3 backfill).
                        None = full authority whitelist.
            keywords:   Optional keyword query string for backfill anomaly tier.

        Returns:
            (articles, total_results, duration_ms)
        """
        domain_list = domains if domains is not None else sorted(AUTHORITY_WHITELIST)

        # List-of-tuples so requests sends one sourceUri param per domain.
        # newsapi.ai requires this format — CSV is not accepted (T7A.14 finding).
        params: list[tuple] = [
            ("apiKey",             NEWSAI_API_KEY),
            ("lang",               "eng"),
            ("articlesPage",       page),
            ("articlesCount",      PAGE_SIZE),
            ("resultType",         "articles"),
            ("articleBodyLen",     -1),
            ("includeArticleBody", "true"),
        ]
        for domain in domain_list:
            params.append(("sourceUri", domain))
        if category:
            params.append(("categoryUri", category))
        # Pulse mode: sort by date for recency; backfill: omit and let API paginate
        if date_start is None and date_end is None:
            params.append(("articlesSortBy", "date"))
        if date_start:
            params.append(("dateStart", date_start))
        if date_end:
            params.append(("dateEnd", date_end))
        if keywords:
            params.append(("keyword", keywords))

        response, duration_ms = timed_request(
            lambda: requests.get(NEWSAI_GETARTICLES_URL, params=params, timeout=20)
        )
        response.raise_for_status()

        data           = response.json()
        articles_block = data.get("articles") or {}
        articles       = articles_block.get("results") or []
        total_results  = articles_block.get("totalResults") or 0

        logger.debug(
            "[newsapi] getArticles category=%r page=%d — %d articles "
            "(found=%d, %dms)",
            category or "<all>", page, len(articles), total_results, duration_ms,
        )
        return articles, total_results, duration_ms

    # ----------------------------------------------------------
    # Payload Builder — newsapi.ai → Internal raw_payload Contract
    # ----------------------------------------------------------

    def _build_raw_payload(
        self,
        article: dict,
        category: str,
        fetch_mode: str,
    ) -> dict:
        """
        Construct the raw_payload dict for one newsapi.ai article.

        This method is the API-shape boundary (Section 3.3 Service Isolation):
        newsapi.ai's response shape is normalized into the same internal contract
        Silver/Gold consumed before the Phase 7A migration — preserving all
        downstream logic verbatim.

        Field mapping (newsapi.ai → internal raw_payload):
            url           → article_id (canonical SHA-256 dedup key, §4.1C)
            title         → title
            description   → description
            url           → url
            image         → url_to_image
            dateTime      → published_at (ISO8601 pass-through)
            body          → content (full text — key improvement over TheNewsAPI
                            snippet; populates knowledge_vault.full_text_raw)
            authors[]     → author (name/uri extracted per element; joined ", ")
            source.uri    → source.id (TLD-stripped via _domain_to_source_id)
            source.title  → source.name (prefer source.title from API; fall back
                            to DOMAIN_DISPLAY_NAMES lookup; final fallback: bare
                            domain — preserves human-readable publisher for RAG
                            citations on any article the lookup table misses)

        Why prefer source.title over DOMAIN_DISPLAY_NAMES:
            newsapi.ai returns the publisher's display name directly in the API
            response. DOMAIN_DISPLAY_NAMES is retained as a fallback for the
            rare case where source.title is absent (e.g. obscure resyndication).

        Why authors[] extraction handles both dicts and strings:
            Event Registry's authors field returns a list of author objects
            ({name, uri, ...}), but the schema is loose — a plain string may
            appear in edge cases. Handling both avoids a TypeError on join().

        Args:
            article:    Raw article dict from newsapi.ai's articles.results[].
            category:   categoryUri used to fetch this article (e.g.
                        "news/Business"). Empty string for backfill articles.
            fetch_mode: "pulse" | "backfill_full" | "backfill_tier_one" |
                        "backfill_anomaly"
        """
        source_dict  = article.get("source") or {}
        domain       = (source_dict.get("uri") or "").strip().lower()
        source_id    = _domain_to_source_id(domain)
        source_name  = (
            source_dict.get("title")
            or DOMAIN_DISPLAY_NAMES.get(domain)
            or domain
        )

        # Extract author names — authors[] may contain dicts or bare strings
        authors_raw  = article.get("authors") or []
        author_parts: list[str] = []
        for a in authors_raw:
            if isinstance(a, dict):
                name = a.get("name") or a.get("uri") or ""
                if name:
                    author_parts.append(name)
            elif isinstance(a, str) and a:
                author_parts.append(a)
        author = ", ".join(author_parts)

        boost, reason = self._impact_boost_info(article)

        return {
            # Core article fields (normalized from newsapi.ai shape)
            "article_id":    article.get("url") or "",
            "title":         article.get("title") or "",
            "description":   article.get("description") or "",
            "url":           article.get("url") or "",
            "url_to_image":  article.get("image") or "",
            "published_at":  article.get("dateTime") or "",
            "content":       article.get("body") or "",
            "author":        author,
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
        Endpoint logged in metadata reflects the actual API call (M1: single
        endpoint for all modes).
        """
        source        = raw_payload["source"]
        partition_key = source["id"] or source["name"] or "unknown"
        category      = raw_payload.get("category", "")

        endpoint = (
            f"{NEWSAI_GETARTICLES_URL}?categoryUri={category}"
            if raw_payload["fetch_mode"] == "pulse"
            else NEWSAI_GETARTICLES_URL
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
        Apply authority whitelist, build payload, emit passing articles.

        All categories pass through the whitelist check uniformly — no
        per-category keyword pre-filtering. The GENERAL_KEYWORDS gate was
        removed in Phase 7A because the "news" root bucket (which needed it)
        returns 0 results on newsapi.ai (T7A.2 finding). Silver's
        keyword_sniper.py applies full relevance scoring at the Flink layer
        (Section 4.1A).

        Returns:
            Count of articles emitted to Bronze in this call.
        """
        emitted = 0
        for article in articles:
            # Authority whitelist — defensive client-side recheck of sourceUri= (B.4)
            if not self._passes_whitelist(article):
                self._filtered_whitelist += 1
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
        Fetch and emit one page of getArticles for all 5 pulse categories.

        Designed to be called every 15-30 min by an Airflow DAG (Section 2.3).
        All categories are treated uniformly — the whitelist server-side filter
        ensures every returned article is from a whitelisted publisher.

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = self._filtered_whitelist = 0

        logger.info(
            "[newsapi] Pulse run starting — %d categories; page_size=%d",
            len(PULSE_CATEGORIES), PAGE_SIZE,
        )

        for category in PULSE_CATEGORIES:
            try:
                articles, _, duration_ms = self._fetch_articles(category)
                emitted = self._process_and_emit(articles, category, "pulse", duration_ms)
                logger.info(
                    "[newsapi] category=%-20s fetched=%d  emitted=%d  "
                    "wl_filtered=%d",
                    category, len(articles), emitted, self._filtered_whitelist,
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
            "[newsapi] Pulse run complete — emitted=%d  wl_filtered=%d",
            self._emitted, self._filtered_whitelist,
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
        Paginate through getArticles for one date range and emit passing articles.

        Pagination continues until either the last page is reached (fewer articles
        returned than PAGE_SIZE) or the API returns an empty page.

        Args:
            from_date:  Inclusive start date.
            to_date:    Inclusive end date.
            domains:    Domain list for sourceUri override. None = full whitelist.
            tier_name:  Label for log messages ("full", "tier_one", "anomaly").
            keywords:   Optional keyword query for anomaly tier.

        Returns:
            Number of articles emitted for this date range.
        """
        from_str      = from_date.isoformat()
        to_str        = to_date.isoformat()
        fetch_mode    = f"backfill_{tier_name}"
        page          = 1
        range_emitted = 0

        while True:
            try:
                articles, total_results, duration_ms = self._fetch_articles(
                    category="",   # backfill: no category filter
                    page=page,
                    date_start=from_str,
                    date_end=to_str,
                    domains=domains,
                    keywords=keywords,
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

            emitted = self._process_and_emit(articles, "", fetch_mode, duration_ms)
            range_emitted += emitted

            logger.info(
                "[newsapi] Backfill tier=%-10s from=%s page=%3d — "
                "emitted=%d  range_total=%d",
                tier_name, from_str, page, emitted, range_emitted,
            )

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

        Tier 2 — Tier-1 domains (6-24 months):
            Restricted to TIER_ONE_DOMAINS (top 8 global wire/financial sources).

        Tier 3 — Anomaly events (2-5 years) — optional:
            Only executed if `keywords` argument is supplied.

        Args:
            keywords: Keyword query string for the anomaly-tier scan (Tier 3).
                      If None, Tier 3 is skipped.

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = self._filtered_whitelist = 0
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
                "[newsapi] Backfill Tier 3 (anomaly events): %s → %s — keywords=%r",
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
            "[newsapi] Backfill complete — emitted=%d  wl_filtered=%d",
            self._emitted, self._filtered_whitelist,
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
        synchronous drain before exit.
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[newsapi] Producer closed cleanly.")


# ==========================================================
# Backwards-compatibility exports — removed in T7A.16 cleanup
# ==========================================================
# TIER_ONE_SOURCE_IDS: alias of TIER_ONE_DOMAINS (pre-Phase-7A name).
# TOP_HEADLINES_ENDPOINT: alias of NEWSAI_GETARTICLES_URL — Gate 2 Silver and
#   Gold tests import this constant to construct test endpoint strings. The URL
#   value changes but the import succeeds; endpoint strings are metadata only
#   and are not validated against a pattern by the Silver/Gold processors.
TIER_ONE_SOURCE_IDS   = TIER_ONE_DOMAINS
TOP_HEADLINES_ENDPOINT = NEWSAI_GETARTICLES_URL


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

    parser = argparse.ArgumentParser(description="newsapi.ai (Event Registry) Bronze Producer")
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
