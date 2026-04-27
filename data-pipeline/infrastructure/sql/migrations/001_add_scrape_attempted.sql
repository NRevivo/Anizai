-- ==========================================================
-- Migration 001 — Add scrape_attempted to knowledge_vault
-- Sprint 15: Post-Silver article scraping layer
-- ==========================================================
-- Applied to live databases that were initialized before Sprint 15.
-- New databases initialized from init.sql already include this column.
--
-- Why DEFAULT FALSE: all existing rows have not been scraped.
-- scraper_runner.py will pick them up in order of ingested_at DESC
-- on its next execution cycle.
-- ==========================================================

ALTER TABLE knowledge_vault
    ADD COLUMN IF NOT EXISTS scrape_attempted BOOLEAN NOT NULL DEFAULT FALSE;
