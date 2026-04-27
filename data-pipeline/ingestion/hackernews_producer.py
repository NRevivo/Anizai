"""
HackerNews Producer — Algolia Search API, scheduled polling.

Fetches high-signal stories (points > 50) from Hacker News via the public
Algolia Search API. No API key required — fully public endpoint. For each
story, the top 10 first-level comments are fetched via the items endpoint and
embedded in the Bronze payload to support the Social Vault's Story-Centric
storage pattern (Section C.3).

Two operating modes (Section 2.3 / B.2):
  - pulse    (default) — latest PULSE_HITS_PER_PAGE stories with points > 50,
                         sorted chronologically (newest first). Designed to be
                         called every 15-30 min by an Airflow DAG. Uses the
                         Algolia search_by_date endpoint (Section B.2).
  - backfill            — relevance-ranked stories for 3 keyword groups
                         (Polymarket / AI Regulation / Geopolitics), last 12
                         months, points > 100. Uses the Algolia search
                         endpoint (Section B.2). Paginated per keyword.

Why no API key:
    The Algolia HackerNews Search API is fully public. No authentication is
    required (Section B.2 note). The .env file does not need a HN_API_KEY.
    This distinguishes HackerNews from all other REST producers (NewsAPI,
    FRED, ArXiv) which require credentials.

Why comments require a separate fetch (items endpoint):
    The Algolia search_by_date and search endpoints return story metadata but
    not comment trees. Section C.3 mandates "top_comments (up to 10 first-
    level comments per story)" in the Bronze payload. A second GET to
    /api/v1/items/{story_id} is required per story to retrieve children.
    Comment fetch failures are non-fatal — the story is emitted with
    top_comments: [] to preserve Bronze completeness (Section 2.3).

Why first N non-deleted children are taken (not sorted by points):
    The Algolia /items/{id} endpoint does not expose comment points/scores.
    HN's own platform ordering for children follows its ranking algorithm.
    Preserving this natural order is preferable to imposing an arbitrary
    secondary sort on an unavailable field.

Why story_text is included in Bronze:
    Ask HN and Show HN stories carry their body in story_text (url is empty
    for Ask HN). Storing it in Bronze keeps the layer immutable and complete
    (Section 2.3). The Silver Job uses title + url for link stories and
    story_text for Ask/Show HN bodies when building the social_vault entry.

Why story_type is derived at Bronze time:
    Algolia's _tags field contains "ask_hn" / "show_hn" markers. Extracting
    story_type here keeps downstream Silver/Gold jobs stateless — they read
    the flag rather than re-parsing _tags. This follows the same pattern as
    the impact_boost flag in newsapi_producer.py (Section 3.3 Service
    Isolation).

Partition key: story_id — HN's stable object ID. Keying by story_id ensures
    any re-ingestion of the same story from overlapping pulse windows lands in
    the same Kafka partition, where the Silver Job's SHA-256 dedup can detect
    and discard duplicates (Section 4.1C).

Kafka Target: ingest.bronze.hackernews (BRONZE_HACKERNEWS)
Sprint Priority: 6 — follows the Social Vault pattern established by Polymarket.

References:
    - Section 2.1:  Producer Matrix (Hacker News row)
    - Section 2.2:  SNR Optimization — points > 50 is the Bronze-level filter
    - Section 2.3:  Scheduled pollers — Airflow DAGs manage invocation
    - Section B.2:  HackerNews technical parameters, endpoints, thresholds
    - Section C.1:  Bronze Schema (build_bronze_message wraps every payload)
    - Section C.3:  Silver Social Discourse Store — Story-Centric pattern
    - Section C.6:  Gold Social Pulse Schema — social_vectors, entry_type
    - Section 3.3:  Service Isolation — no transformation, no DB writes here
    - Section 3.4:  NDJSON serialisation (handled by kafka_utils.py)
    - Section 4.1C: SHA-256 deduplication — story_id used as partition key
    - Section 9.1:  Environment parity — no API key required
"""

from __future__ import annotations

import html
import logging
import re
import sys
import time
from datetime import datetime, timedelta, timezone
from typing import Optional

import requests

from config.kafka_topics import BRONZE_HACKERNEWS
from utils.kafka_utils import build_bronze_message, make_producer, timed_request
from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()


# ==========================================================
# HackerNews / Algolia API Constants (Section B.2)
# ==========================================================

SOURCE_NAME = "hackernews"

ALGOLIA_BASE_URL = "https://hn.algolia.com/api/v1"

# search_by_date: chronological order (newest first) — pulse endpoint (Section B.2)
# search:         relevance-ranked — backfill endpoint (Section B.2)
# items:          full item tree including first-level comment children
ALGOLIA_SEARCH_BY_DATE_URL = f"{ALGOLIA_BASE_URL}/search_by_date"
ALGOLIA_SEARCH_URL         = f"{ALGOLIA_BASE_URL}/search"
ALGOLIA_ITEMS_URL          = f"{ALGOLIA_BASE_URL}/items"

# Pulse: stories with points > 50 (Section B.2 baseline threshold).
# One page per pulse call — SHA-256 dedup in Silver handles overlap between
# consecutive 15-30 min windows (Section 4.1C).
PULSE_MIN_POINTS    = 50
PULSE_HITS_PER_PAGE = 50

# Backfill: last 12 months, points > 100, relevance-sorted (Section B.2).
# Cap at BACKFILL_MAX_PAGES per keyword (20 x 50 = 1,000 stories / keyword).
BACKFILL_MIN_POINTS    = 100
BACKFILL_MONTHS        = 12
BACKFILL_HITS_PER_PAGE = 50
BACKFILL_MAX_PAGES     = 20

# Three keyword groups for backfill (Section B.2)
BACKFILL_KEYWORDS: tuple[str, ...] = (
    "Polymarket",
    "AI Regulation",
    "Geopolitics",
)

# Top N first-level comments per story (Section B.2, C.3)
TOP_COMMENTS_LIMIT = 10

# Courtesy delay between per-story item fetches.
# No official Algolia rate limit is documented, but the items endpoint is
# called once per story — 0.25 s keeps total pulse time reasonable for
# PULSE_HITS_PER_PAGE stories (~13 s overhead) while being respectful of a
# free public API.
INTER_FETCH_DELAY_SEC = 0.25


# ==========================================================
# Helpers
# ==========================================================

def _strip_html(text: Optional[str]) -> str:
    """
    Strip HTML tags and unescape HTML entities from a string.

    Why needed: Algolia returns story_text and comment text fields with
    embedded HTML markup (e.g. <p>, <a href="...">, &#x27;). Storing raw
    HTML in Bronze would break the NDJSON format contract if tags contain
    newlines, and would burden the Silver Job with HTML parsing. Cleaning
    once here follows the same rationale as _clean_text() in
    arxiv_producer.py (Section 3.4).

    Args:
        text: Raw HTML string from Algolia, or None.

    Returns:
        Plain-text string with tags removed and entities decoded, or ""
        if input is None/empty.
    """
    if not text:
        return ""
    text = html.unescape(text)
    text = re.sub(r"<[^>]+>", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def _classify_story_type(tags: list[str]) -> str:
    """
    Derive the story_type label from the Algolia _tags list.

    Algolia attaches "ask_hn" or "show_hn" tags to the corresponding post
    types. Any story without these tags is a standard external link story.
    Classifying at Bronze time keeps Gold's platform_logic.story_type field
    (Section C.6) populated without requiring the Gold Job to re-parse tags.

    Args:
        tags: List of Algolia tag strings from the hit's _tags field.

    Returns:
        "ask_hn" | "show_hn" | "story"
    """
    if "ask_hn" in tags:
        return "ask_hn"
    if "show_hn" in tags:
        return "show_hn"
    return "story"


# ==========================================================
# Producer
# ==========================================================

class HackerNewsProducer:
    """
    Scheduled REST producer for the HackerNews Algolia Search API.

    Pulse mode fetches the latest PULSE_HITS_PER_PAGE stories with
    points > PULSE_MIN_POINTS, sorted by date. Backfill mode paginates
    relevance-ranked results for each BACKFILL_KEYWORDS entry over the
    last BACKFILL_MONTHS, stopping when nb_pages is exhausted or
    BACKFILL_MAX_PAGES is reached.

    For every story, the items endpoint is called to retrieve the top
    TOP_COMMENTS_LIMIT first-level non-deleted comments. Comment fetch
    failures are non-fatal — the story is always emitted.

    No API key required (Section B.2 / Section 9.1).
    Emits to: ingest.bronze.hackernews (BRONZE_HACKERNEWS)
    """

    def __init__(self) -> None:
        self._producer = make_producer()
        self._emitted  = 0

    # ----------------------------------------------------------
    # Algolia Fetch Helpers
    # ----------------------------------------------------------

    def _fetch_stories_page(
        self,
        url: str,
        params: dict,
    ) -> tuple[list[dict], int, int]:
        """
        Fetch one page of story hits from an Algolia search endpoint.

        Why generic (accepts both search_by_date and search URLs):
            Both endpoints share the same response shape — hits[], nbPages,
            nbHits. A single method avoids duplicating HTTP/JSON parsing for
            pulse vs backfill callers.

        Args:
            url:    Full Algolia endpoint URL (ALGOLIA_SEARCH_BY_DATE_URL or
                    ALGOLIA_SEARCH_URL).
            params: Query-parameter dict (tags, numericFilters, page, etc.).

        Returns:
            (hits, nb_pages, duration_ms)
            hits:        List of story hit dicts from "hits" key.
            nb_pages:    Total pages available for this query (pagination cap).
            duration_ms: HTTP round-trip time.

        Raises:
            requests.HTTPError:        on non-2xx responses.
            requests.RequestException: on network failures.
        """
        response, duration_ms = timed_request(
            lambda: requests.get(url, params=params, timeout=30)
        )
        response.raise_for_status()
        data = response.json()
        return data.get("hits", []), data.get("nbPages", 1), duration_ms

    def _fetch_story_item(self, story_id: str) -> tuple[dict, int]:
        """
        Fetch the full item record for a story, including comment children.

        The items endpoint returns the story with its children (first-level
        comments) nested under the "children" key. This is the only Algolia
        endpoint that returns comment trees (Section B.2 / C.3).

        Args:
            story_id: HN story object ID string (e.g. "40185065").

        Returns:
            (item_data, duration_ms)
            item_data:   Full item dict with "children" list.
            duration_ms: HTTP round-trip time.

        Raises:
            requests.HTTPError:        on non-2xx responses.
            requests.RequestException: on network failures.
        """
        item_url = f"{ALGOLIA_ITEMS_URL}/{story_id}"
        response, duration_ms = timed_request(
            lambda: requests.get(item_url, timeout=30)
        )
        response.raise_for_status()
        return response.json(), duration_ms

    def _extract_top_comments(self, item_data: dict) -> list[dict]:
        """
        Extract the top TOP_COMMENTS_LIMIT first-level comments from an item.

        Filters out deleted and dead comments (HN marks these with
        deleted: true / dead: true and strips the text field).
        Preserves the platform's natural child ordering — Algolia /items
        does not expose comment points, so no secondary sort is applied
        (see module docstring for rationale).

        HTML tags and entities are stripped from comment text via
        _strip_html() before storage, maintaining the same NDJSON safety
        guarantee as story_text cleaning (Section 3.4).

        Args:
            item_data: Full item dict from _fetch_story_item().

        Returns:
            List of up to TOP_COMMENTS_LIMIT comment dicts with keys:
            comment_id, author, text (HTML-stripped), created_at.
        """
        children = item_data.get("children") or []
        valid = [
            c for c in children
            if c.get("text")
            and not c.get("deleted")
            and not c.get("dead")
        ]
        return [
            {
                "comment_id": c.get("id"),
                "author":     c.get("author") or "",
                "text":       _strip_html(c.get("text")),
                "created_at": c.get("created_at") or "",
            }
            for c in valid[:TOP_COMMENTS_LIMIT]
        ]

    # ----------------------------------------------------------
    # Payload Builder
    # ----------------------------------------------------------

    @staticmethod
    def _build_raw_payload(
        hit: dict,
        top_comments: list[dict],
        fetch_mode: str,
    ) -> dict:
        """
        Construct the raw_payload dict for one HackerNews story.

        All hit fields are stored verbatim (Bronze is immutable, Section 2.3).
        Producer-injected fields (story_type, fetch_mode) are documented here
        so the Silver Job can consume them without guessing.

        Why story_type is producer-injected:
            See _classify_story_type() docstring — classifying at Bronze time
            keeps Gold's platform_logic.story_type (Section C.6) stateless.

        Why url may be empty string:
            Ask HN stories have no external URL; their content lives in
            story_text. The Silver Job handles both cases. Storing "" is
            preferable to omitting the key for schema consistency.

        Why top_comments may be []:
            If the items endpoint call fails, the story is still emitted to
            preserve Bronze completeness. The Silver Job stores an empty
            top_comments list in the social_vault entry.

        Args:
            hit:          Story hit dict from an Algolia search response.
            top_comments: List from _extract_top_comments(), or [] on failure.
            fetch_mode:   "pulse" | "backfill" — consumed by Silver/Gold Jobs.

        Returns:
            raw_payload dict for build_bronze_message().
        """
        tags = hit.get("_tags") or []
        return {
            # Core story fields — stored as received from Algolia
            "story_id":     hit.get("objectID") or "",
            "title":        hit.get("title") or "",
            "url":          hit.get("url") or "",           # empty for Ask HN
            "author":       hit.get("author") or "",
            "points":       hit.get("points") or 0,
            "num_comments": hit.get("num_comments") or 0,
            "created_at":   hit.get("created_at") or "",
            "story_text":   _strip_html(hit.get("story_text")),  # Ask/Show HN body
            "tags":         tags,
            # Producer-injected context fields
            "story_type":   _classify_story_type(tags),    # "story" | "ask_hn" | "show_hn"
            "top_comments": top_comments,                   # [] if items fetch failed
            "fetch_mode":   fetch_mode,                     # "pulse" | "backfill"
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
        Wrap raw_payload in a Bronze envelope and publish to BRONZE_HACKERNEWS.

        Partition key: story_id — HN's stable object ID ensures all ingestions
        of the same story land in the same Kafka partition, where the Silver
        Job's SHA-256 dedup can detect overlapping pulse windows (Section 4.1C).

        Args:
            raw_payload:     Dict from _build_raw_payload().
            source_endpoint: Algolia URL string (including query params) for
                             full Bronze replay auditability (Section C.1).
            duration_ms:     HTTP search request round-trip duration.
        """
        partition_key = str(raw_payload.get("story_id") or "unknown")
        msg = build_bronze_message(
            source_name=SOURCE_NAME,
            source_endpoint=source_endpoint,
            raw_payload=raw_payload,
            http_status_code=200,
            request_duration_ms=duration_ms,
        )
        self._producer.send(BRONZE_HACKERNEWS, value=msg, key=partition_key)
        self._emitted += 1

    # ----------------------------------------------------------
    # Per-Story Orchestration (shared by pulse and backfill)
    # ----------------------------------------------------------

    def _fetch_and_emit_story(
        self,
        hit: dict,
        fetch_mode: str,
        source_endpoint: str,
        search_duration_ms: int,
    ) -> None:
        """
        Fetch comments for one story hit, build its payload, and emit.

        Comment fetch is wrapped in try/except so that a 404 or network
        error on a single story does not abort the entire pulse/backfill run.
        INTER_FETCH_DELAY_SEC is always applied in the finally block —
        including on failure — to maintain a respectful request cadence
        regardless of outcome.

        Args:
            hit:                Story hit dict from Algolia search response.
            fetch_mode:         "pulse" | "backfill".
            source_endpoint:    Algolia search URL used to retrieve this hit.
            search_duration_ms: Duration of the parent search call (stored
                                in Bronze metadata alongside story payload).
        """
        story_id = hit.get("objectID")
        if not story_id:
            logger.warning(
                "[hackernews] Skipping hit with no objectID: title=%s",
                hit.get("title"),
            )
            return

        top_comments: list[dict] = []
        try:
            item_data, _ = self._fetch_story_item(story_id)
            top_comments = self._extract_top_comments(item_data)
        except requests.HTTPError as exc:
            logger.warning(
                "[hackernews] Comment fetch HTTP error (story_id=%s): %s "
                "— emitting story without comments",
                story_id, exc,
            )
        except requests.RequestException as exc:
            logger.warning(
                "[hackernews] Comment fetch network error (story_id=%s): %s "
                "— emitting story without comments",
                story_id, exc,
            )
        finally:
            # Always sleep — maintains respectful cadence even on failures
            time.sleep(INTER_FETCH_DELAY_SEC)

        raw_payload = self._build_raw_payload(hit, top_comments, fetch_mode)
        self._emit(raw_payload, source_endpoint, search_duration_ms)

    # ----------------------------------------------------------
    # Pulse Mode — scheduled 15-30 min polling
    # ----------------------------------------------------------

    def run_pulse(self) -> int:
        """
        Fetch and emit the latest stories with points > PULSE_MIN_POINTS.

        Uses the Algolia search_by_date endpoint (chronological, newest first)
        — one page per call. Designed to be invoked every 15-30 min by an
        Airflow DAG (Section 2.3 / B.2).

        No pagination: PULSE_HITS_PER_PAGE stories covers a single polling
        window. SHA-256 dedup in the Silver Job handles stories that reappear
        across consecutive windows (Section 4.1C).

        Returns:
            Total number of Bronze messages emitted in this run.
        """
        self._emitted = 0

        params = {
            "tags":           "story",
            "numericFilters": f"points>{PULSE_MIN_POINTS}",
            "hitsPerPage":    PULSE_HITS_PER_PAGE,
        }
        source_endpoint = (
            f"{ALGOLIA_SEARCH_BY_DATE_URL}"
            f"?tags=story&numericFilters=points%3E{PULSE_MIN_POINTS}"
            f"&hitsPerPage={PULSE_HITS_PER_PAGE}"
        )

        logger.info(
            "[hackernews] Pulse starting — points>%d, hitsPerPage=%d",
            PULSE_MIN_POINTS, PULSE_HITS_PER_PAGE,
        )

        try:
            hits, _, duration_ms = self._fetch_stories_page(
                ALGOLIA_SEARCH_BY_DATE_URL, params
            )
        except requests.HTTPError as exc:
            logger.error("[hackernews] Pulse HTTP error: %s — aborting", exc)
            return 0
        except requests.RequestException as exc:
            logger.error("[hackernews] Pulse network error: %s — aborting", exc)
            return 0

        logger.info(
            "[hackernews] Pulse fetched %d stories (%dms)", len(hits), duration_ms
        )

        for hit in hits:
            self._fetch_and_emit_story(hit, "pulse", source_endpoint, duration_ms)

        self._producer.flush()
        logger.info("[hackernews] Pulse complete — emitted=%d", self._emitted)
        return self._emitted

    # ----------------------------------------------------------
    # Backfill Mode — keyword-paginated historical load
    # ----------------------------------------------------------

    def _backfill_keyword(self, keyword: str, cutoff_ts: int) -> int:
        """
        Paginate through Algolia search results for one backfill keyword.

        Uses the Algolia search endpoint (relevance-ranked) with:
            - points > BACKFILL_MIN_POINTS
            - created_at_i > cutoff_ts  (12-month lookback as Unix timestamp)
            - query = keyword

        Pagination stops when:
            (a) page count reaches BACKFILL_MAX_PAGES, or
            (b) Algolia's nbPages is exhausted, or
            (c) an HTTP / network error occurs (logged, stops this keyword).

        Args:
            keyword:   Backfill search term (e.g. "Polymarket").
            cutoff_ts: Unix timestamp representing the 12-month cutoff.

        Returns:
            Number of stories emitted for this keyword.
        """
        page = 0
        keyword_start = self._emitted

        while page < BACKFILL_MAX_PAGES:
            params = {
                "tags":           "story",
                "query":          keyword,
                "numericFilters": (
                    f"points>{BACKFILL_MIN_POINTS},"
                    f"created_at_i>{cutoff_ts}"
                ),
                "hitsPerPage":    BACKFILL_HITS_PER_PAGE,
                "page":           page,
            }
            source_endpoint = (
                f"{ALGOLIA_SEARCH_URL}"
                f"?tags=story&query={requests.utils.quote(keyword)}"
                f"&numericFilters=points%3E{BACKFILL_MIN_POINTS}"
                f"%2Ccreated_at_i%3E{cutoff_ts}"
                f"&page={page}"
            )

            try:
                hits, nb_pages, duration_ms = self._fetch_stories_page(
                    ALGOLIA_SEARCH_URL, params
                )
            except requests.HTTPError as exc:
                logger.error(
                    "[hackernews] Backfill HTTP error (keyword=%s page=%d): %s "
                    "— stopping pagination for this keyword",
                    keyword, page, exc,
                )
                break
            except requests.RequestException as exc:
                logger.error(
                    "[hackernews] Backfill network error (keyword=%s page=%d): %s "
                    "— stopping pagination for this keyword",
                    keyword, page, exc,
                )
                break

            if not hits:
                break

            for hit in hits:
                self._fetch_and_emit_story(
                    hit, "backfill", source_endpoint, duration_ms
                )

            logger.info(
                "[hackernews] Backfill keyword=%-20s  page=%d/%d  batch=%d  "
                "keyword_total=%d",
                keyword, page + 1, nb_pages,
                len(hits), self._emitted - keyword_start,
            )

            if page + 1 >= nb_pages:
                break

            page += 1

        return self._emitted - keyword_start

    def run_backfill(self) -> int:
        """
        Execute the 12-month historical backfill across all BACKFILL_KEYWORDS.

        For each keyword, paginates through relevance-ranked results filtered
        to points > BACKFILL_MIN_POINTS within the last BACKFILL_MONTHS months.
        One-time operation; safe to re-run — Silver SHA-256 dedup prevents
        double-ingestion (Section 4.1C).

        Returns:
            Total number of Bronze messages emitted.
        """
        self._emitted = 0
        cutoff_ts = int(
            (datetime.now(timezone.utc) - timedelta(days=BACKFILL_MONTHS * 30))
            .timestamp()
        )
        cutoff_date = (
            datetime.fromtimestamp(cutoff_ts, tz=timezone.utc).date().isoformat()
        )

        logger.info(
            "[hackernews] Backfill starting — %d keywords, points>%d, cutoff=%s",
            len(BACKFILL_KEYWORDS), BACKFILL_MIN_POINTS, cutoff_date,
        )

        for keyword in BACKFILL_KEYWORDS:
            kw_emitted = self._backfill_keyword(keyword, cutoff_ts)
            logger.info(
                "[hackernews] Backfill keyword=%s complete — emitted=%d",
                keyword, kw_emitted,
            )

        self._producer.flush()
        logger.info(
            "[hackernews] Backfill complete — emitted=%d across %d keywords",
            self._emitted, len(BACKFILL_KEYWORDS),
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
        a synchronous drain before exit (same pattern as arxiv_producer.py).
        """
        self._producer.flush()
        self._producer.close()
        logger.info("[hackernews] Producer closed cleanly.")


# ==========================================================
# Docker / Airflow Entry Point
# ==========================================================

def main(mode: str = "pulse") -> None:
    """
    Entry point for Docker container and Airflow BashOperator invocation.

    Modes:
        pulse    (default) — latest stories with points > 50 (every 15-30 min).
        backfill           — 12-month historical load for 3 keyword groups.

    Usage:
        python -m ingestion.hackernews_producer            # pulse
        python -m ingestion.hackernews_producer backfill   # backfill
    """
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    producer = HackerNewsProducer()
    try:
        if mode == "backfill":
            producer.run_backfill()
        else:
            producer.run_pulse()
    finally:
        producer.close()


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="HackerNews Bronze Producer")
    parser.add_argument(
        "mode",
        nargs="?",
        default="pulse",
        choices=["pulse", "backfill"],
        help="pulse (default) — 15-30 min polling; backfill — 12-month historical load",
    )
    args = parser.parse_args()
    main(args.mode)
