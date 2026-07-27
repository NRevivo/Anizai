-- Migration 004 — Enrichment gating & social-path reject coverage (Phase 7D, T2)
--
-- Justification: Phase 7D gates Gold enrichment on the dedup check that already
-- runs one step earlier (KG-A-7) and extends reject capture to the social path
-- (KG-A-12). To make a captured reject traceable back to the instance that
-- produced it, filter_rejects needs the canonical_event_id it currently lacks
-- (dayrun_analysis.md §6). This migration adds:
--   1. filter_rejects.canonical_event_id — instance key, populated at BOTH the
--      global_news drop branch and the new social drop branch from
--      _cost_trace_id(record) (Phase 7D, T7). Joins a reject row back to its
--      llm_cost_events / vault rows. Nullable: rows written before this
--      migration have no key and stay NULL (no backfill is possible — the key
--      was never stored).
--
-- No CHECK to widen: filter_rejects.source_name carries NO CHECK constraint
-- (only a column comment), so the 'hackernews' rows T6 writes are already
-- admissible — nothing needs altering for the social path.
--
-- No signal_id / knowledge_vectors DDL: KG-A-8 (T5) reuses the existing
-- knowledge_vectors PRIMARY KEY (signal_id) + ON CONFLICT (signal_id) DO NOTHING
-- guard, and the social path reuses social_vault's caller-managed
-- exists_by_content_hash idempotency — both already present. This migration is
-- therefore additive to filter_rejects only.
--
-- Idempotent: ADD COLUMN IF NOT EXISTS / CREATE INDEX IF NOT EXISTS — safe to
-- re-run. Forward-only migration. No rollback path (matches 002/003 convention).
-- Applied to local dev Postgres in T9/T10 (Gate 3 / E2E); applied to cloud
-- Postgres in T11 (§8.3 step 1, same window as the deployment).
--
-- References: docs/A_pipeline/plans/phase7d_enrichment_gating.md §4.1 (schema),
--             §5 T2/T7; dayrun_analysis.md §6 (the instance-key gap)

-- ----------------------------------------------------------
-- 1. filter_rejects.canonical_event_id (plan §4.1)
-- ----------------------------------------------------------
-- The canonical_event_id (or bronze_ref fallback) of the rejected object,
-- resolved by _cost_trace_id() at the caller and passed through insert_reject().
-- TEXT (not UUID): _cost_trace_id may return a non-UUID bronze_ref for some
-- social records, mirroring llm_cost_events.trace_id which is also TEXT.

ALTER TABLE filter_rejects
    ADD COLUMN IF NOT EXISTS canonical_event_id TEXT;

CREATE INDEX IF NOT EXISTS idx_filter_rejects_cei
    ON filter_rejects (canonical_event_id);

-- ----------------------------------------------------------
-- Verification (visible when run via psql)
-- ----------------------------------------------------------

DO $$
BEGIN
    RAISE NOTICE '=== Migration 004 applied ===';
    RAISE NOTICE 'filter_rejects.canonical_event_id : %', (
        SELECT count(*) FROM information_schema.columns
        WHERE table_name = 'filter_rejects' AND column_name = 'canonical_event_id'
    );
    RAISE NOTICE 'idx_filter_rejects_cei            : %', (
        SELECT to_regclass('public.idx_filter_rejects_cei')
    );
    RAISE NOTICE '=============================';
END $$;
