"""
Scraper Runner — Post-Silver article text enrichment cycle (Sprint 15).

Queries knowledge_vault for NewsAPI articles that have not yet had a scrape
attempt, calls article_scraper.scrape_article_text() for each, and persists
the result (or marks as attempted-without-update) via knowledge_vault.update_scraped_text().

Design contract:
    - This module does NOT interact with Kafka or Flink.
    - All DB operations are delegated to persistence/knowledge_vault.py.
    - All scraping logic is delegated to utils/article_scraper.py.
    - scrape_attempted is set TRUE on every article regardless of outcome,
      preventing infinite retry on unreachable URLs.

Execution model:
    Called by orchestration/dags/scraper_dag.py via Airflow PythonOperator
    on a 30-minute schedule. Also safe to call directly for manual runs
    or testing (Section 4.1 no-side-effects-on-import pattern).

Architecture position:
    Silver (knowledge_vault.archive())
        ↓  [this runner, every 30 min]
    knowledge_vault.fetch_unscraped_articles()
        ↓
    article_scraper.scrape_article_text(url)
        ↓
    knowledge_vault.update_scraped_text(doc_id, text)

Stats dict returned by run():
    {
        "attempted": int,   # total articles processed this cycle
        "succeeded": int,   # articles where full text was retrieved
        "skipped":   int,   # domain not in SCRAPABLE_BASE_DOMAINS
        "failed":    int,   # domain scrapable but newspaper4k raised exception
    }

References:
    - Sprint 15: Post-Silver scraping layer specification
    - Section 5.1: Knowledge Vault — full_text_raw enrichment path
    - Section 3.3: Service Isolation — no direct DB calls here; delegated to
                   persistence/knowledge_vault.py
    - Section 3.2: DRY — scraping logic in utils/article_scraper.py only
"""

from __future__ import annotations

import logging
import sys
from typing import Optional

from utils.logging_config import setup_logging

logger = logging.getLogger(__name__)
setup_logging()

# Maximum articles to scrape per execution cycle.
# Keeps each Airflow task run bounded and predictable; backfill of historical
# articles is distributed across many 30-minute windows rather than one large
# burst (Section 4.1 Sprint 15 pulse-only constraint).
MAX_SCRAPE_PER_RUN: int = 20


def run(max_articles: int = MAX_SCRAPE_PER_RUN) -> dict:
    """
    Execute one scraping cycle: fetch → scrape → persist.

    Imports are deferred to call time (not module level) to prevent Airflow
    DAG parser from triggering DB connections or library side effects during
    file parsing (Section 6A, Design Decision D3).

    Args:
        max_articles: Upper bound on articles scraped this cycle.
                      Defaults to MAX_SCRAPE_PER_RUN (20).

    Returns:
        Stats dict with keys: attempted, succeeded, skipped, failed.
        All values are non-negative integers that sum to attempted.
    """
    from persistence import knowledge_vault                         # noqa: PLC0415
    from utils.article_scraper import is_scrapable_domain           # noqa: PLC0415
    from utils.article_scraper import scrape_article_text           # noqa: PLC0415

    stats: dict[str, int] = {
        "attempted": 0,
        "succeeded": 0,
        "skipped":   0,
        "failed":    0,
    }

    articles = knowledge_vault.fetch_unscraped_articles(limit=max_articles)
    logger.info("[scraper_runner] Cycle start — %d articles to process", len(articles))

    for row in articles:
        doc_id: str       = row["doc_id"]
        url:    Optional[str] = row.get("original_url") or ""

        stats["attempted"] += 1

        scraped_text = scrape_article_text(url)

        if scraped_text is not None:
            stats["succeeded"] += 1
        else:
            # Distinguish skip (domain not whitelisted) from failure
            # (domain whitelisted but scrape raised an exception).
            # article_scraper.scrape_article_text() returns None for both cases;
            # is_scrapable_domain() is the authoritative classifier.
            if is_scrapable_domain(url):
                stats["failed"] += 1
            else:
                stats["skipped"] += 1

        # Always persist the attempt to prevent future retry, regardless of outcome.
        # update_scraped_text preserves existing full_text_raw when scraped_text=None.
        knowledge_vault.update_scraped_text(doc_id, scraped_text)

    logger.info(
        "[scraper_runner] Cycle complete — attempted=%d  succeeded=%d  "
        "skipped=%d  failed=%d",
        stats["attempted"],
        stats["succeeded"],
        stats["skipped"],
        stats["failed"],
    )
    return stats


if __name__ == "__main__":
    # Allow direct invocation for manual runs and live validation (T8).
    # Usage: python orchestration/scraper_runner.py [max_articles]
    import os

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s — %(message)s",
        stream=sys.stdout,
    )

    # Load .env so DB credentials are available without Docker
    try:
        from dotenv import load_dotenv  # type: ignore[import]
        env_path = os.path.join(os.path.dirname(__file__), "..", ".env")
        load_dotenv(env_path)
    except ImportError:
        pass

    limit = int(sys.argv[1]) if len(sys.argv) > 1 else MAX_SCRAPE_PER_RUN
    result = run(max_articles=limit)
    print(f"\nScraping cycle complete: {result}")
