-- Migration 003 — Filter observability & LLM cost instrumentation (Phase 7B.5-I, T2)
--
-- Justification: Phase 7B.5 (filter-threshold calibration) needs data that the
-- pipeline never persisted — articles rejected by the two-stage filter and the
-- semantic-rescue cosine scores — and the pipeline's OpenAI spend is silent (no
-- call site routes through any cost utility). This migration adds:
--   1. filter_rejects        — flag-gated reject retention (REJECT_CAPTURE_ENABLED)
--   2. knowledge_vault.rescue_cosine — populated ONLY on the rescue-promote path;
--                              NULL = passed the sniper directly
--   3. llm_cost_events       — one row per runtime OpenAI call (permanent, always on)
--      + llm_cost_daily_summary / llm_cost_run_summary ROLLUP views (derived,
--        never a second write path)
--
-- Idempotent: IF NOT EXISTS / OR REPLACE / guarded ALTER throughout — safe to
-- re-run. Forward-only migration. No rollback path (matches 002 convention).
-- Applied to local dev Postgres in T6 (Gate 3 / E2E); applied to cloud Postgres
-- in T7 (same migration window as the pending reactive_triggers_log DDL).
--
-- References: docs/A_pipeline/plans/phase7b5i_filter_observability_and_cost.md
--             §2.1–§2.3 (schemas), §1 D2/D3/D5 (decisions)

-- ----------------------------------------------------------
-- 1. filter_rejects (plan §2.1)
-- ----------------------------------------------------------
-- No reject_stage column (D5): exactly one clean-reject path exists (failed
-- sniper AND failed rescue), so the field would be a constant. NOT DLQ
-- traffic — a filter reject is a valid low-signal article, not an error.

CREATE TABLE IF NOT EXISTS filter_rejects (
    reject_id             UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id                TEXT,
    source_name           TEXT NOT NULL,           -- newsapi / arxiv / telegram
    original_url          TEXT,
    title                 TEXT,
    inverted_pyramid_lead TEXT,
    full_text_raw         TEXT,                    -- FULL text, not truncated (T7B.1 manual review)
    relevance_score       REAL NOT NULL,           -- sniper score (< DEFAULT_THRESHOLD by construction)
    sniper_keywords       JSONB,
    rescue_cosine         REAL NOT NULL,           -- rescue similarity (< threshold; 0.0 for the empty-text edge)
    rejected_at           TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_filter_rejects_run
    ON filter_rejects (run_id, rejected_at);

-- ----------------------------------------------------------
-- 2. knowledge_vault.rescue_cosine (plan §2.2)
-- ----------------------------------------------------------
-- Nullable REAL; populated ONLY on the rescue-promote path. All existing
-- rows stay NULL — pre-instrumentation survivors are indistinguishable by
-- design (no backfill is possible; the score was never persisted).

ALTER TABLE knowledge_vault
    ADD COLUMN IF NOT EXISTS rescue_cosine REAL;

-- ----------------------------------------------------------
-- 3. llm_cost_events + summary views (plan §2.3)
-- ----------------------------------------------------------

CREATE TABLE IF NOT EXISTS llm_cost_events (
    event_id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id            TEXT,
    site              TEXT NOT NULL,        -- gold_enrich / gold_consensus / translate / gold_embed / rescue_embed
    source_name       TEXT,                 -- newsapi / arxiv / telegram / polymarket / hackernews / googletrends
    model             TEXT NOT NULL,        -- gpt-4o / gpt-3.5-turbo / text-embedding-3-small
    prompt_tokens     INTEGER NOT NULL,
    completion_tokens INTEGER NOT NULL,
    total_tokens      INTEGER NOT NULL,
    cost_usd          NUMERIC(12,6) NOT NULL,
    trace_id          TEXT,                 -- canonical_event_id (joins rescue_embed → filter_rejects)
    created_at        TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE INDEX IF NOT EXISTS idx_llm_cost_run
    ON llm_cost_events (run_id, created_at);

CREATE OR REPLACE VIEW llm_cost_daily_summary AS
SELECT
    date_trunc('day', created_at)::date AS day,
    COALESCE(source_name, 'ALL')        AS source_name,
    COALESCE(site,        'ALL')        AS usage_type,
    count(*)                            AS calls,
    sum(prompt_tokens)                  AS prompt_tokens,
    sum(completion_tokens)              AS completion_tokens,
    sum(total_tokens)                   AS total_tokens,
    round(sum(cost_usd)::numeric, 4)    AS cost_usd
FROM llm_cost_events
GROUP BY date_trunc('day', created_at), ROLLUP (source_name, site);

CREATE OR REPLACE VIEW llm_cost_run_summary AS
SELECT
    run_id,
    COALESCE(source_name, 'ALL')        AS source_name,
    COALESCE(site,        'ALL')        AS usage_type,
    count(*)                            AS calls,
    sum(prompt_tokens)                  AS prompt_tokens,
    sum(completion_tokens)              AS completion_tokens,
    sum(total_tokens)                   AS total_tokens,
    round(sum(cost_usd)::numeric, 4)    AS cost_usd
FROM llm_cost_events
GROUP BY run_id, ROLLUP (source_name, site);

-- ----------------------------------------------------------
-- Verification (visible when run via psql)
-- ----------------------------------------------------------

DO $$
BEGIN
    RAISE NOTICE '=== Migration 003 applied ===';
    RAISE NOTICE 'filter_rejects        : %', (SELECT to_regclass('public.filter_rejects'));
    RAISE NOTICE 'llm_cost_events       : %', (SELECT to_regclass('public.llm_cost_events'));
    RAISE NOTICE 'kv.rescue_cosine      : %', (
        SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'knowledge_vault' AND column_name = 'rescue_cosine'
    );
    RAISE NOTICE '==============================';
END $$;
