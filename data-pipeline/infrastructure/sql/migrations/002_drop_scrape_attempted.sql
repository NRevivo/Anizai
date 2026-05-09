-- Migration 002 — Drop scrape_attempted from knowledge_vault (Phase 7C)
--
-- Justification: the scraper (scraper_runner.py, article_scraper.py) has been
-- retired in Phase 7C. newsapi.ai's body field (Phase 7A) provides full article
-- text at Bronze time, making the post-Silver scrape unnecessary. The
-- scrape_attempted column is now permanent-FALSE garbage. The full_text_raw
-- column is retained — it is the hub RAG drill-down source (§8.3.1).
--
-- Forward-only migration. No rollback path.
-- Applied to local dev Postgres after T7C.2 deletions (T7C.5).
-- Applied to cloud Postgres as part of Phase C5 preparation (T7C.11).
--
-- References: Phase 7C decision C2 (phase7_intelligent_filtering.md)

ALTER TABLE knowledge_vault
    DROP COLUMN IF EXISTS scrape_attempted;
