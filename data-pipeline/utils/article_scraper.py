"""
Article Scraper — Post-Silver full-text enrichment (Sprint 15).

Fetches full article text from scrapable news sources using newspaper4k.
Called by orchestration/scraper_runner.py after articles have passed
Silver filtering and been persisted to knowledge_vault.

Architecture position:
    Bronze → Silver → knowledge_vault.archive()
                              ↓
                    [scraper_runner.py runs periodically]
                              ↓
                    article_scraper.scrape_article_text(url)
                              ↓
                    knowledge_vault.update_scraped_text(doc_id, text)

Source Classification Table
============================
Determined by T1 investigation (Sprint 15, 2026-04-11).
Tested with newspaper4k 0.9.5 against live URLs.

  Source ID             Base Domain          Status      Chars (T1)  Notes
  --------------------- -------------------- ----------  ----------  ----------------------------------
  bbc-news              bbc.com              SCRAPABLE       2,333   BBC World / News article pages
  the-guardian-uk       theguardian.com      SCRAPABLE       4,089   Full article text accessible
  times-of-israel       timesofisrael.com    SCRAPABLE       8,518   Incl. blogs.timesofisrael.com
  jerusalem-post        jpost.com            SCRAPABLE       2,180   Incl. subdomain article URLs
  ynetnews              ynetnews.com         SCRAPABLE       6,913   Breaking news category pages
  i24news               i24news.tv           SCRAPABLE       3,040   News index and article pages
  reuters               reuters.com          SKIP                0   HTTP 401
  associated-press      apnews.com           SKIP                0   HTTP 403
  cnbc                  cnbc.com             SKIP                0   HTTP 403
  cnn                   edition.cnn.com      SKIP              452*  JS-rendered; returns survey form
  kan-11                kan.org.il           SKIP                0   HTTP 403
  wall-street-journal   wsj.com              SKIP                0   Hard paywall (subscriber only)
  bloomberg             bloomberg.com        SKIP                0   Hard paywall (subscriber only)
  new-york-times        nytimes.com          SKIP                0   Metered paywall (JS-gated)
  washington-post       washingtonpost.com   SKIP                0   Metered paywall (JS-gated)
  financial-times       ft.com               SKIP                0   Hard paywall (subscriber only)
  the-economist         economist.com        SKIP                0   Hard paywall (subscriber only)

  * CNN 452 chars was a survey/feedback form — not article content. Excluded.

Why base-domain matching (not exact netloc):
    knowledge_vault.original_url contains article-level URLs, which may use
    content delivery or section subdomains (e.g., blogs.timesofisrael.com,
    defense-and-tech.jpost.com). Matching on the last two domain segments
    catches all subdomains of a scrapable outlet without whitelisting each one.

Why newspaper4k (not requests + BeautifulSoup):
    newspaper4k handles boilerplate removal, multi-page article stitching,
    and anti-bot header mimicry automatically. It is already installed in the
    venv. A custom BS4 parser would require per-site CSS selectors that break
    on redesigns (Section 4.1 Sprint 15 scraping spec).

References:
    - Sprint 15: Post-Silver scraping layer specification
    - Section 5.1: Knowledge Vault — full_text_raw field
    - Section 3.2: DRY — all scraping logic centralised here
    - Section 3.3: Service Isolation — scraper does NOT write to DB;
                   all persistence is done by scraper_runner.py via
                   knowledge_vault.update_scraped_text()
"""

from __future__ import annotations

import logging
from typing import Optional
from urllib.parse import urlparse

from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# ==========================================================
# Scrapable base-domain registry
# ==========================================================
# Last two domain segments only (e.g. 'bbc.com', not 'www.bbc.com').
# Subdomain variants are handled by _extract_base_domain().
# Update this set if a new outlet is confirmed scrapable via T1 investigation.
SCRAPABLE_BASE_DOMAINS: frozenset[str] = frozenset({
    "bbc.com",
    "theguardian.com",
    "timesofisrael.com",
    "jpost.com",
    "ynetnews.com",
    "i24news.tv",
})


# ==========================================================
# Domain helpers
# ==========================================================

def _extract_base_domain(url: str) -> str:
    """
    Extract the registrable base domain (last two segments) from a URL.

    Examples:
        "https://www.bbc.com/news/world" → "bbc.com"
        "https://blogs.timesofisrael.com/..." → "timesofisrael.com"
        "https://defense-and-tech.jpost.com/..." → "jpost.com"

    Why last two segments: content subdomains (blogs., defense-and-tech.)
    are transparent to scraping; we want to match on the outlet root, not
    the delivery subdomain (Section 3.2 DRY — single normalisation point).
    """
    try:
        netloc = urlparse(url).netloc.lower().split(":")[0]  # strip port if present
        parts = netloc.split(".")
        if len(parts) >= 2:
            return ".".join(parts[-2:])
        return netloc
    except Exception:
        return ""


def is_scrapable_domain(url: str) -> bool:
    """
    Return True if the URL's outlet is in SCRAPABLE_BASE_DOMAINS.

    Used by scraper_runner.run() to classify skips vs. failures in the stats
    dict — a skip means the domain is not on the whitelist, a failure means
    scraping was attempted but newspaper4k raised an exception.

    Args:
        url: Article URL from knowledge_vault.original_url.

    Returns:
        True if the outlet is whitelisted for scraping.
    """
    return _extract_base_domain(url) in SCRAPABLE_BASE_DOMAINS


# ==========================================================
# Core scraping function
# ==========================================================

def scrape_article_text(url: str) -> Optional[str]:
    """
    Fetch and return the full article text for a single URL.

    Returns None (without raising) in three cases:
        1. Domain is not in SCRAPABLE_BASE_DOMAINS — no request made.
        2. newspaper4k raises ArticleException (HTTP error, parse failure).
        3. Parsed article text is empty after stripping whitespace.

    Callers (scraper_runner.py) interpret None as "no text available" and
    call knowledge_vault.update_scraped_text(doc_id, None) to mark the row
    as attempted without overwriting the existing full_text_raw.

    Why download() + parse() instead of Article(url, fetch_images=False):
        Explicit two-step call makes it testable — unit tests can mock
        download() independently from parse() (Section 4.3 mock-driven dev).

    Args:
        url: Article URL from knowledge_vault.original_url.

    Returns:
        Full article text string (non-empty), or None on any failure/skip.
    """
    if not url:
        logger.debug("[article_scraper] Empty URL — skip")
        return None

    if not is_scrapable_domain(url):
        logger.debug("[article_scraper] Non-scrapable domain for url=%s — skip", url)
        return None

    try:
        # newspaper4k lazy import — not installed in all environments;
        # the scraper service should always have it, but tests mock this call.
        from newspaper import Article  # type: ignore[import]

        article = Article(url)
        article.download()
        article.parse()

        text = article.text.strip() if article.text else ""
        if not text:
            logger.warning("[article_scraper] Empty text after parse url=%s", url)
            return None

        logger.debug(
            "[article_scraper] Scraped %d chars from url=%s",
            len(text),
            url,
        )
        return text

    except Exception as exc:  # noqa: BLE001
        # ArticleException, ConnectionError, timeout, etc.
        logger.warning(
            "[article_scraper] Scrape failed for url=%s: %s: %s",
            url,
            type(exc).__name__,
            exc,
        )
        return None
