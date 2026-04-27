"""
ArXiv Producer — REST API, daily polling.

Fetches up to 200 papers/category/day from cs.AI, cs.LG, econ.GN,
q-fin.ST, q-bio.PE, stat.AP, cs.CY via the public Atom XML feed
(export.arxiv.org/api/query). No API key required. Mandatory 3-second
delay between requests per ArXiv Terms of Service (Section B.1).

Two operating modes (Section 2.3 / B.1):
  - pulse    (default) — up to PULSE_MAX_RESULTS papers/category, sorted
                         by submittedDate descending. Designed to be called
                         daily by an Airflow DAG.
  - backfill            — paginated, up to BACKFILL_MAX_PER_CATEGORY papers/
                         category, last BACKFILL_MONTHS months. sortBy=relevance
                         to surface the highest-signal papers first. Stops
                         pagination when papers older than the cutoff are
                         encountered (date-based early exit, simpler than
                         arXiv's date-range query syntax).

Why API-level SNR keyword filter (not purely client-side):
    Section 2.2 mandates "Only high-signal data enters Bronze Kafka topics."
    For arXiv, the category filter already provides strong domain relevance
    (cs.AI, econ.GN, etc.), but adding SNR keyword terms (Regulation, Policy,
    Market Impact, Outbreak, Intervention, Trend) to the API search_query
    further restricts results to papers with direct market/policy relevance,
    reducing Bronze volume without requiring a client-side scan. This is the
    arXiv equivalent of the General category keyword sniper in
    newsapi_producer.py (Section B.4). The Silver Job's keyword_sniper.py
    applies full relevance scoring on the abstract body inside Flink (4.1A).

Why no authority whitelist:
    All arXiv papers originate from the same canonical source (arxiv.org).
    The newsapi_producer whitelist solves fragmentation across many publishers —
    a problem that does not exist for arXiv (Section B.1 note).

Why no Impact Boost:
    Impact Boost (+1 to impact_level) is reserved for Israeli/Middle East
    security and energy articles from live news feeds (Section B.4). arXiv
    papers receive their impact_level purely from the Gold Job's Cognitive
    Metadata Extraction prompt (Section 4.2A).

Why url is the canonical dedup key (not arxiv_id):
    Section 4.1C uses hash_document(full_text, url) to produce document_hash.
    The canonical abstract URL (https://arxiv.org/abs/{base_id}, without
    version suffix) is consistent across versions of the same paper, making
    it the correct dedup anchor. A paper re-submitted as v2 will share the
    same canonical URL and will be correctly deduplicated by the Silver Job.

Partition key: base arxiv_id (e.g. "2301.12345", without version suffix) —
    all versions of the same paper land in the same Kafka partition, ensuring
    the Silver Job's SHA-256 dedup logic sees them together (consistent with
    the series_id keying in fred_producer.py and source.id in newsapi_producer.py).

Kafka Target: ingest.bronze.arxiv (BRONZE_ARXIV)
Sprint Priority: 4 — follows the established daily REST polling pattern.

References:
    - Section 2.1:  Producer Matrix (ArXiv row)
    - Section 2.2:  SNR Optimization — Domain Filtering Strategy
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section B.1:  ArXiv technical parameters, Categories, SNR Keywords
    - Section C.1:  Bronze Schema (build_bronze_message wraps every payload)
    - Section C.3:  Silver Full-Text Document Store — fields this producer
                    populates (abstract → full_text_raw, etc.)
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.1A: Keyword Sniper Filter (Silver-level; producer pre-filters only)
    - Section 4.1C: SHA-256 deduplication — canonical_url used as dedup key
    - Section 9.1:  Environment parity — no API key required (public Atom feed)
"""

from __future__ import annotations

import logging
import re
import sys
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from typing import Optional

import requests

from config.kafka_topics import BRONZE_ARXIV
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# ArXiv API Constants (Section B.1)
# ==========================================================

SOURCE_NAME = "arxiv"

ARXIV_API_URL = "https://export.arxiv.org/api/query"

# Daily pulse: up to 200 papers/category (Section B.1).
# One request per category — no pagination in pulse mode.
PULSE_MAX_RESULTS = 200

# Backfill: up to 1,000 top-relevance papers/category, last 12 months (Section B.1).
BACKFILL_MAX_PER_CATEGORY = 1_000
BACKFILL_MONTHS = 12

# arXiv recommends <= 200 results per request to avoid timeouts.
PAGE_SIZE = 200

# Mandatory 3-second delay between ANY two API requests (Section B.1 TOS).
REQUEST_DELAY_SEC = 3

# XML Atom feed namespaces
NS = {
    "atom":       "http://www.w3.org/2005/Atom",
    "opensearch": "http://a9.com/-/spec/opensearch/1.1/",
    "arxiv":      "http://arxiv.org/schemas/atom",
}


# ==========================================================
# Categories (Section B.1)
# ==========================================================
# Seven arXiv categories covering AI, ML, economics, quantitative
# finance, biology, statistics, and cybersecurity — the domains most
# relevant to Anizai's forecasting use cases.

CATEGORIES: list[str] = [
    "cs.AI",     # Artificial Intelligence
    "cs.LG",     # Machine Learning
    "econ.GN",   # General Economics
    "q-fin.ST",  # Statistical Finance
    "q-bio.PE",  # Populations and Evolution (outbreak signals)
    "stat.AP",   # Applications (cross-domain applied statistics)
    "cs.CY",     # Computers and Society (regulation, policy)
]


# ==========================================================
# SNR Keywords — API-level pre-filter (Section B.1, 2.2)
# ==========================================================
# Applied to both title (ti:) and abstract (abs:) via the arXiv
# search_query parameter. Multi-word phrases use arXiv's double-quote
# phrase syntax. Section 4.1A keyword_sniper.py applies full
# relevance scoring at the Silver Flink layer.

SNR_KEYWORDS: tuple[str, ...] = (
    "regulation",
    "policy",
    "market impact",
    "outbreak",
    "intervention",
    "trend",
)


# ==========================================================
# Helpers
# ==========================================================

def _clean_text(text: Optional[str]) -> str:
    """
    Collapse internal whitespace (newlines, tabs, repeated spaces) to a
    single space and strip leading/trailing whitespace.

    Why needed: arXiv Atom XML embeds abstract and title fields with
    indentation newlines. Storing raw multi-line text in Bronze creates
    inconsistent NDJSON lines (each \\n would break the NDJSON format
    contract, Section 3.4). Clean once here; Silver/Gold layers receive
    consistent single-line strings.
    """
    if not text:
        return ""
    return re.sub(r"\s+", " ", text).strip()


def _extract_base_arxiv_id(entry_url: str) -> str:
    """
    Extract the base (versionless) arXiv ID from an Atom entry URL.

    Atom entry id format: "http://arxiv.org/abs/2301.12345v2"
    Returns:               "2301.12345"

    Why strip version: the canonical abstract URL
    (https://arxiv.org/abs/2301.12345) is stable across updates.
    Using it as the dedup key means v1 and v2 of the same paper share
    the same hash and are not double-ingested (Section 4.1C).
    """
    match = re.search(r"/abs/([^v]+)", entry_url)
    if not match:
        return entry_url  # fallback: return full URL if pattern unexpected
    return match.group(1)


def _extract_versioned_arxiv_id(entry_url: str) -> str:
    """
    Extract the versioned arXiv ID (e.g. "2301.12345v2") from the Atom URL.

    Stored in Bronze raw_payload for complete audit trail. The Silver Job
    uses the versionless canonical_url for dedup; this field is for
    human-readable identification of the exact version ingested.
    """
    # URL format: "http://arxiv.org/abs/2301.12345v2"
    # Split on "/abs/" and take the trailing segment
    parts = entry_url.split("/abs/")
    if len(parts) == 2:
        return parts[1].strip()
    return entry_url


# ==========================================================
# Producer
# ==========================================================

class ArXivProducer:
    """
    Daily REST producer for the arXiv public Atom XML feed.

    Pulse mode fetches the most recently submitted papers for each
    CATEGORIES entry. Backfill mode paginates up to
    BACKFILL_MAX_PER_CATEGORY papers per category over BACKFILL_MONTHS,
    stopping early when papers older than the cutoff are encountered.

    Both modes apply the SNR keyword pre-filter at the API query level
    (Section 2.2). No authority whitelist. No Impact Boost.

    Emits to: ingest.bronze.arxiv (BRONZE_ARXIV)
    """

    def __init__(self) -> None:
        self._producer = make_producer()
        # Running totals per run_pulse / run_backfill call
        self._emitted = 0

    # ----------------------------------------------------------
    # Search Query Builder
    # ----------------------------------------------------------

    @staticmethod
    def _build_search_query(category: str) -> str:
        """
        Build the arXiv API search_query string for a given category.

        Combines the category filter with the SNR keyword pre-filter
        so that only papers touching the market/policy/health domain
        enter Bronze (Section 2.2, B.1).

        Multi-word phrases (e.g. "market impact") use arXiv's double-quote
        phrase syntax. Single-word terms use bare ti:/abs: field prefixes.

        Args:
            category: arXiv category string (e.g. "cs.AI").

        Returns:
            search_query string passed as the 'search_query' API parameter.

        Example output:
            cat:cs.AI AND (ti:regulation OR abs:regulation OR
            ti:policy OR abs:policy OR ti:"market impact" OR
            abs:"market impact" OR ...)
        """
        kw_parts: list[str] = []
        for kw in SNR_KEYWORDS:
            if " " in kw:
                kw_parts.append(f'ti:"{kw}" OR abs:"{kw}"')
            else:
                kw_parts.append(f"ti:{kw} OR abs:{kw}")
        keyword_clause = " OR ".join(kw_parts)
        return f"cat:{category} AND ({keyword_clause})"

    # ----------------------------------------------------------
    # Atom XML Fetch & Parse
    # ----------------------------------------------------------

    def _fetch_page(
        self,
        search_query: str,
        start: int,
        max_results: int,
        sort_by: str = "submittedDate",
        sort_order: str = "descending",
    ) -> tuple[list[dict], int, int]:
        """
        Fetch one page of arXiv Atom results and parse entries to dicts.

        Why parse XML here (not in the caller): keeping all Atom namespace
        and parsing concerns inside one method means _fetch_page is the
        only place that knows about XML structure. Callers receive plain
        Python dicts, making run_pulse/run_backfill easier to read and test.

        Why xml.etree.ElementTree (stdlib): no extra dependency; arXiv
        Atom feeds are well-formed XML that ET handles cleanly. lxml would
        add a C-extension dependency for no practical benefit at this payload size.

        Args:
            search_query: arXiv API search_query parameter string.
            start:        0-indexed offset for pagination.
            max_results:  Number of results to request (≤ PAGE_SIZE).
            sort_by:      "submittedDate" (pulse) | "relevance" (backfill).
            sort_order:   "descending" | "ascending".

        Returns:
            (entries, total_results, duration_ms)
            entries: list of parsed entry dicts (see _parse_entry).
            total_results: total papers matching the query (for pagination).
            duration_ms: HTTP request round-trip time.

        Raises:
            requests.HTTPError: on non-2xx responses (e.g. 503 rate limit).
            requests.RequestException: on network failures.
            ET.ParseError: if the response body is not valid XML.
        """
        params = {
            "search_query": search_query,
            "start":        start,
            "max_results":  max_results,
            "sortBy":       sort_by,
            "sortOrder":    sort_order,
        }
        response, duration_ms = timed_request(
            lambda: requests.get(ARXIV_API_URL, params=params, timeout=30)
        )
        response.raise_for_status()

        root = ET.fromstring(response.text)

        # Total count from OpenSearch metadata (may exceed max_results)
        total_elem = root.find("opensearch:totalResults", NS)
        total_results = int(total_elem.text) if total_elem is not None else 0

        entries = [
            self._parse_entry(entry)
            for entry in root.findall("atom:entry", NS)
        ]

        logger.debug(
            "[arxiv] search_query=%s start=%d — %d entries (total=%d, %dms)",
            search_query[:60], start, len(entries), total_results, duration_ms,
        )
        return entries, total_results, duration_ms

    @staticmethod
    def _parse_entry(entry: ET.Element) -> dict:
        """
        Extract all relevant fields from a single Atom <entry> element.

        Why canonical_url strips version: ensures SHA-256 dedup in the
        Silver Job treats v1 and v2 of the same paper as the same document
        (Section 4.1C). The versioned ID is kept separately for audit.

        Why abstract is cleaned: Atom <summary> contains embedded newlines
        and leading spaces from XML indentation. _clean_text() collapses
        these to a single space so the Bronze NDJSON line remains valid
        (Section 3.4).

        Args:
            entry: xml.etree.ElementTree.Element for one <entry> block.

        Returns:
            Dict with all fields needed by the Silver Job's
            map_arxiv_paper_to_silver() (Task 4A.2).
        """
        # Entry URL — "http://arxiv.org/abs/2301.12345v2"
        entry_url = (entry.findtext("atom:id", default="", namespaces=NS) or "").strip()

        base_id      = _extract_base_arxiv_id(entry_url)
        versioned_id = _extract_versioned_arxiv_id(entry_url)
        canonical_url = f"https://arxiv.org/abs/{base_id}"

        # PDF link — <link title="pdf" href="...">
        pdf_url = ""
        for link in entry.findall("atom:link", NS):
            if link.get("title") == "pdf":
                pdf_url = link.get("href", "")
                break

        # Authors — may be multiple <author><name> elements
        authors: list[str] = [
            _clean_text(a.findtext("atom:name", default="", namespaces=NS))
            for a in entry.findall("atom:author", NS)
        ]

        # Categories — all <category term="..."> attributes
        categories: list[str] = [
            cat.get("term", "")
            for cat in entry.findall("atom:category", NS)
            if cat.get("term")
        ]

        # Primary category — <arxiv:primary_category term="...">
        primary_cat_elem = entry.find("arxiv:primary_category", NS)
        primary_category = (
            primary_cat_elem.get("term", "") if primary_cat_elem is not None else ""
        )

        return {
            "arxiv_id":        versioned_id,
            "canonical_url":   canonical_url,
            "pdf_url":         pdf_url,
            "title":           _clean_text(entry.findtext("atom:title",   default="", namespaces=NS)),
            "abstract":        _clean_text(entry.findtext("atom:summary", default="", namespaces=NS)),
            "authors":         authors,
            "published":       (entry.findtext("atom:published", default="", namespaces=NS) or "").strip(),
            "updated":         (entry.findtext("atom:updated",   default="", namespaces=NS) or "").strip(),
            "categories":      categories,
            "primary_category": primary_category,
        }

    # ----------------------------------------------------------
    # Payload Builder
    # ----------------------------------------------------------

    @staticmethod
    def _build_raw_payload(
        entry_data: dict,
        query_category: str,
        fetch_mode: str,
        search_query: str,
    ) -> dict:
        """
        Construct the raw_payload dict for one arXiv paper.

        All entry fields are stored verbatim (Bronze layer is immutable,
        Section 2.3). Producer-injected context fields (query_category,
        fetch_mode, search_query) are documented here so the Silver Job
        can read them without guessing.

        Why url = canonical_url (not pdf_url):
            Section C.3 and hash_document() in the Silver Job use `url`
            as the canonical dedup key. The abstract page URL is stable;
            the PDF URL may change across versions. Keeping the same
            contract as newsapi_producer.py (_build_raw_payload returns
            a `url` field) makes the Silver Job's validation logic reusable.

        Why search_query is stored:
            Enables Bronze-layer replay audits to reconstruct exactly which
            API query produced each batch. Useful when diagnosing gaps in
            category coverage after a query-parameter change.

        Args:
            entry_data:     Dict from _parse_entry().
            query_category: Category string that generated this result
                            (e.g. "cs.AI") — for Silver routing context.
            fetch_mode:     "pulse" | "backfill".
            search_query:   The full arXiv API search_query string used.

        Returns:
            raw_payload dict for build_bronze_message().
        """
        return {
            # Core paper fields (as received from arXiv — never modified)
            "arxiv_id":         entry_data["arxiv_id"],
            "url":              entry_data["canonical_url"],   # dedup key (Section 4.1C)
            "pdf_url":          entry_data["pdf_url"],
            "title":            entry_data["title"],
            "abstract":         entry_data["abstract"],
            "authors":          entry_data["authors"],
            "published":        entry_data["published"],
            "updated":          entry_data["updated"],
            "categories":       entry_data["categories"],
            "primary_category": entry_data["primary_category"],
            # Producer-injected context (consumed by Silver / Gold Jobs)
            "query_category":   query_category,
            "fetch_mode":       fetch_mode,
            "search_query":     search_query,
        }

    # ----------------------------------------------------------
    # Kafka Emission
    # ----------------------------------------------------------

    def _emit(
        self,
        raw_payload: dict,
        source_endpoint: str,
        duration_ms: int,
    ) -> None:
        """
        Wrap raw_payload in a Bronze envelope and publish to BRONZE_ARXIV.

        Partition key: base arxiv_id (versionless, e.g. "2301.12345") —
        ensures the Silver Job sees all versions of the same paper on
        the same consumer partition for consistent SHA-256 dedup (Section 4.1C).

        source_endpoint captures the full query URL (including search_query,
        start offset, and sort parameters) so Bronze records are fully
        replayable from the Atom API with identical parameters (Section C.1).

        Args:
            raw_payload:     Dict from _build_raw_payload().
            source_endpoint: Full API URL string (including query params).
            duration_ms:     HTTP request round-trip duration.
        """
        # Versionless base ID as partition key (e.g. "2301.12345")
        base_id = _extract_base_arxiv_id(raw_payload.get("url", ""))
        partition_key = base_id or raw_payload.get("arxiv_id", "unknown")

        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
            http_status_code=200,
            request_duration_ms=duration_ms,
        )
        self._producer.send(BRONZE_ARXIV, value=msg, key=partition_key)
        self._emitted += 1

    # ----------------------------------------------------------
    # Pulse Mode — daily fetch
    # ----------------------------------------------------------

    def run_pulse(self) -> int:
        """
        Fetch and emit up to PULSE_MAX_RESULTS papers for each category.

        Designed to be called daily by an Airflow DAG (Section 2.3 / B.1).
        Papers are sorted by submittedDate descending so the most recently
        submitted papers are fetched first. No pagination — PULSE_MAX_RESULTS
        (200) covers a full day's intake for each category.

        The 3-second inter-category delay is mandated by arXiv TOS (Section B.1).

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = 0

        logger.info(
            "[arxiv] Pulse run starting — %d categories, up to %d papers each",
            len(CATEGORIES), PULSE_MAX_RESULTS,
        )

        for category in CATEGORIES:
            search_query = self._build_search_query(category)
            source_endpoint = (
                f"{ARXIV_API_URL}?search_query={requests.utils.quote(search_query)}"
                f"&start=0&max_results={PULSE_MAX_RESULTS}"
                f"&sortBy=submittedDate&sortOrder=descending"
            )
            try:
                entries, total_results, duration_ms = self._fetch_page(
                    search_query=search_query,
                    start=0,
                    max_results=PULSE_MAX_RESULTS,
                    sort_by="submittedDate",
                    sort_order="descending",
                )
                for entry_data in entries:
                    raw_payload = self._build_raw_payload(
                        entry_data, category, "pulse", search_query
                    )
                    self._emit(raw_payload, source_endpoint, duration_ms)

                logger.info(
                    "[arxiv] Pulse category=%-12s fetched=%d  emitted=%d  total_api=%d  %dms",
                    category, len(entries), len(entries), total_results, duration_ms,
                )
            except requests.HTTPError as exc:
                logger.error(
                    "[arxiv] HTTP error for category=%s: %s — skipping", category, exc
                )
            except requests.RequestException as exc:
                logger.error(
                    "[arxiv] Network error for category=%s: %s — skipping", category, exc
                )
            except ET.ParseError as exc:
                logger.error(
                    "[arxiv] XML parse error for category=%s: %s — skipping", category, exc
                )
            finally:
                # Mandatory TOS delay between every request (Section B.1)
                time.sleep(REQUEST_DELAY_SEC)

        self._producer.flush()
        logger.info(
            "[arxiv] Pulse run complete — emitted=%d across %d categories",
            self._emitted, len(CATEGORIES),
        )
        return self._emitted

    # ----------------------------------------------------------
    # Backfill Mode — paginated historical load
    # ----------------------------------------------------------

    def _backfill_category(
        self,
        category: str,
        cutoff: date,
    ) -> int:
        """
        Paginate through arXiv results for one category, up to the backfill
        limit and date cutoff.

        Papers are sorted by relevance (best proxy for "top-cited" since
        arXiv API does not expose citation counts, Section B.1). Pagination
        stops when:
            (a) total emitted reaches BACKFILL_MAX_PER_CATEGORY, or
            (b) a paper older than `cutoff` is encountered (date-based
                early exit — simpler and more reliable than arXiv's
                date-range query syntax, which has inconsistent behaviour
                across API versions), or
            (c) the API returns no more results.

        The 3-second inter-page delay is mandatory per arXiv TOS (Section B.1).

        Args:
            category: arXiv category string (e.g. "cs.AI").
            cutoff:   Earliest acceptable published date. Papers older
                      than this date trigger pagination stop.

        Returns:
            Number of papers emitted for this category.
        """
        search_query    = self._build_search_query(category)
        start           = 0
        category_emitted = 0

        while category_emitted < BACKFILL_MAX_PER_CATEGORY:
            remaining  = BACKFILL_MAX_PER_CATEGORY - category_emitted
            batch_size = min(PAGE_SIZE, remaining)

            source_endpoint = (
                f"{ARXIV_API_URL}?search_query={requests.utils.quote(search_query)}"
                f"&start={start}&max_results={batch_size}"
                f"&sortBy=relevance&sortOrder=descending"
            )
            try:
                entries, total_results, duration_ms = self._fetch_page(
                    search_query=search_query,
                    start=start,
                    max_results=batch_size,
                    sort_by="relevance",
                    sort_order="descending",
                )
            except requests.HTTPError as exc:
                logger.error(
                    "[arxiv] Backfill HTTP error (category=%s start=%d): %s — "
                    "stopping pagination for this category",
                    category, start, exc,
                )
                break
            except requests.RequestException as exc:
                logger.error(
                    "[arxiv] Backfill network error (category=%s start=%d): %s — "
                    "stopping pagination for this category",
                    category, start, exc,
                )
                break
            except ET.ParseError as exc:
                logger.error(
                    "[arxiv] Backfill XML parse error (category=%s start=%d): %s — "
                    "stopping pagination for this category",
                    category, start, exc,
                )
                break

            if not entries:
                break

            cutoff_reached = False
            for entry_data in entries:
                # Date-based early exit (Section B.1 "last 12 months")
                published_str = entry_data.get("published", "")
                if published_str:
                    try:
                        published_dt = datetime.fromisoformat(
                            published_str.replace("Z", "+00:00")
                        )
                        published_d = published_dt.date()
                        if published_d < cutoff:
                            logger.info(
                                "[arxiv] Backfill category=%s — published %s < cutoff %s, "
                                "stopping pagination",
                                category, published_d, cutoff,
                            )
                            cutoff_reached = True
                            break
                    except (ValueError, AttributeError):
                        # Malformed date: emit the paper anyway, let Silver validate
                        pass

                raw_payload = self._build_raw_payload(
                    entry_data, category, "backfill", search_query
                )
                self._emit(raw_payload, source_endpoint, duration_ms)
                category_emitted += 1

                if category_emitted >= BACKFILL_MAX_PER_CATEGORY:
                    break

            logger.info(
                "[arxiv] Backfill category=%-12s start=%4d  batch=%d  "
                "category_total=%d  api_total=%d",
                category, start, len(entries), category_emitted, total_results,
            )

            is_last_page = len(entries) < batch_size or (start + len(entries)) >= total_results
            if cutoff_reached or is_last_page:
                break

            start += len(entries)
            # Mandatory TOS delay between paginated requests (Section B.1)
            time.sleep(REQUEST_DELAY_SEC)

        return category_emitted

    def run_backfill(self) -> int:
        """
        Execute the 12-month historical backfill for all categories.

        Fetches up to BACKFILL_MAX_PER_CATEGORY (1,000) papers/category,
        sorted by relevance, stopping when papers older than 12 months
        are encountered (date-based early exit in _backfill_category).

        One-time operation; safe to re-run — the Silver Job's SHA-256
        dedup (Section 4.1C) prevents double-ingestion of papers already
        in the Knowledge Vault.

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = 0
        cutoff = date.today() - timedelta(days=BACKFILL_MONTHS * 30)

        logger.info(
            "[arxiv] Backfill starting — %d categories, up to %d papers each, "
            "cutoff=%s",
            len(CATEGORIES), BACKFILL_MAX_PER_CATEGORY, cutoff.isoformat(),
        )

        for category in CATEGORIES:
            cat_emitted = self._backfill_category(category, cutoff)
            logger.info(
                "[arxiv] Backfill category=%s complete — emitted=%d",
                category, cat_emitted,
            )
            # Mandatory TOS delay between categories (Section B.1)
            time.sleep(REQUEST_DELAY_SEC)

        self._producer.flush()
        logger.info(
            "[arxiv] Backfill complete — emitted=%d across %d categories",
            self._emitted, len(CATEGORIES),
        )
        return self._emitted

    # ----------------------------------------------------------
    # Shutdown
    # ----------------------------------------------------------

    def close(self) -> None:
        """
        Flush and close the Kafka producer connection cleanly.

        Must be called in the finally block of main() to prevent message
        loss — kafka-python-ng buffers messages in memory and flush() forces
        a synchronous drain before exit (same pattern as newsapi_producer.py).
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[arxiv] Producer closed cleanly.")


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(mode: str = "pulse") -> None:
    """
    Entry point for Docker container and Airflow BashOperator invocation.

    Modes:
        pulse    (default) — daily top-results polling per category.
        backfill           — 12-month paginated historical load.

    Usage:
        python -m ingestion.arxiv_producer            # pulse
        python -m ingestion.arxiv_producer backfill   # backfill
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = ArXivProducer()
    try:
        if mode == "backfill":
            producer.run_backfill()
        else:
            producer.run_pulse()
    finally:
        producer.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="ArXiv Bronze Producer")
    parser.add_argument(
        "mode",
        nargs="?",
        default="pulse",
        choices=["pulse", "backfill"],
        help="pulse (default) — daily polling; backfill — 12-month historical load",
    )
    args = parser.parse_args()
    main(args.mode)
