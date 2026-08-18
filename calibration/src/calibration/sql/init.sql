-- ==========================================================
-- Calibration Schema — Phase 10A (plan §4)
-- ==========================================================
-- Applies to the DEDICATED calibration database only (plan P6/B1).
-- This file must never be run against the pipeline's `anizai` database:
-- calibration/config.py::validate() refuses that connection URL outright,
-- and calibration/db.py re-checks before opening a connection.
--
-- Idempotent: every object uses IF NOT EXISTS, so re-applying is a no-op.
-- Apply with:  python -m calibration.cli init-db
--
-- Design notes that are not obvious from the DDL:
--   * All timestamps are TIMESTAMPTZ and all times are UTC (plan B3).
--     Polymarket resolves in UTC, Cloud Scheduler fires in UTC, and cohort
--     window arithmetic is in UTC. Local time exists only at render time.
--   * Probabilities are NUMERIC(5,4) with a 0-1 CHECK (plan B4) rather than
--     DOUBLE PRECISION: the 0-1 contract is worth enforcing in the database,
--     and exact decimal avoids float drift in Brier aggregations.
--   * There are no views and no materialized views. Aggregations are computed
--     in Python (Phase 10C) and snapshotted as JSONB (plan B2).
-- ==========================================================

CREATE EXTENSION IF NOT EXISTS pgcrypto;   -- gen_random_uuid()


-- ==========================================================
-- Table 1: calibration_questions
-- ==========================================================
-- The Polymarket-anchored questions under measurement.
--
-- polymarket_condition_id is the stable resolution key and carries the UNIQUE
-- constraint — slugs can change upstream, condition ids do not. That UNIQUE is
-- also what makes discovery idempotent: re-running it inserts nothing new.
CREATE TABLE IF NOT EXISTS calibration_questions (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    question_text               TEXT            NOT NULL,
    polymarket_slug             TEXT            NOT NULL,
    polymarket_condition_id     TEXT            NOT NULL,
    category                    TEXT            NOT NULL
                                                CHECK (category IN ('geopolitical','financial','ai','other')),
    cohort                      TEXT            NOT NULL
                                                CHECK (cohort IN ('7d','14d','30-45d')),
    expected_resolution_date    DATE            NOT NULL,
    liquidity_usd_at_pickup     NUMERIC(14,2),
    -- The market's own probability at the moment we picked the question up.
    --
    -- This is the comparison that makes a Brier score mean something. "The
    -- agent scored 0.25" is unreadable on its own; "the agent scored 0.25 and
    -- the market scored 0.03 on the same questions" is a result. Polymarket
    -- prices move continuously and are not recoverable after settlement
    -- without a price-history call, so it has to be captured at pickup or not
    -- at all.
    --
    -- Nullable, and null for every question added before 2026-08-18: those
    -- were picked up before this column existed and their pickup prices are
    -- gone. Backfilling them with a later price would be inventing data.
    market_probability_at_pickup NUMERIC(5,4)
                                                CHECK (market_probability_at_pickup IS NULL
                                                       OR (market_probability_at_pickup >= 0
                                                           AND market_probability_at_pickup <= 1)),
    status                      TEXT            NOT NULL
                                                CHECK (status IN ('open','resolved','archived'))
                                                DEFAULT 'open',
    added_by                    TEXT            NOT NULL
                                                CHECK (added_by IN ('auto','manual')),
    added_by_operator           TEXT,           -- email when manual; NULL when auto
    created_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    updated_at                  TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    UNIQUE (polymarket_condition_id)
);

CREATE INDEX IF NOT EXISTS idx_questions_status_cohort
    ON calibration_questions(status, cohort);

-- Partial index: the resolver only ever asks "what is about to resolve", and
-- only about open questions. Excluding resolved/archived rows keeps the index
-- small as the archive grows.
CREATE INDEX IF NOT EXISTS idx_questions_expected_resolution
    ON calibration_questions(expected_resolution_date)
    WHERE status = 'open';


-- ==========================================================
-- Table 4: calibration_runs
-- ==========================================================
-- Declared before calibration_forecasts because that table references it.
--
-- Operational/audit table: "this week's run dispatched 25, completed 23,
-- failed 1, timed out 1". A run is finished when every forecast it dispatched
-- has reached a terminal state.
CREATE TABLE IF NOT EXISTS calibration_runs (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    run_type                    TEXT            NOT NULL
                                                CHECK (run_type IN ('initial_seed','weekly_reforecast','manual','single_question')),
    triggered_at                TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    triggered_by                TEXT            NOT NULL,   -- 'cloud_scheduler' | 'cli' | operator email
    questions_dispatched        INTEGER,
    forecasts_completed         INTEGER,
    forecasts_failed            INTEGER,
    finished_at                 TIMESTAMPTZ,
    run_metadata                JSONB
);

CREATE INDEX IF NOT EXISTS idx_runs_triggered_at
    ON calibration_runs(triggered_at DESC);


-- ==========================================================
-- Table 2: calibration_forecasts
-- ==========================================================
-- One row per forecast the agent produced (or failed to produce).
--
-- Phase 10A creates this table but writes to it only in tests — dispatch and
-- harvest arrive in Phase 10B. It is created now so the schema is reviewable
-- and migratable as one unit.
--
-- session_id is NOT NULL: we mint the Firestore sessionId ourselves at
-- dispatch (plan A6) and use it as the document id for BOTH documents, so it
-- is always known before the row is inserted. Making it nullable would turn
-- "we lost track of which session this row dispatched" from an insert-time
-- error into a silent data state.
--
-- status includes 'needs_clarification': the agent can legitimately end a run
-- at awaiting_clarification with no SessionResult. That is not a failure and
-- must not be counted as one (plan §7).
CREATE TABLE IF NOT EXISTS calibration_forecasts (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id                 UUID            NOT NULL
                                                REFERENCES calibration_questions(id) ON DELETE CASCADE,
    run_id                      UUID            NOT NULL
                                                REFERENCES calibration_runs(id),
    forecast_run_index          INTEGER         NOT NULL,   -- 0 = initial seed, 1 = first re-forecast, ...
    session_id                  TEXT            NOT NULL,   -- Firestore sessionId, minted by us (plan A6)
    query_doc_id                TEXT            NOT NULL,   -- forecastQueries doc id; equals session_id today
    idempotency_key             TEXT            NOT NULL,
    agent_version               TEXT,                       -- from SessionResult.agentVersion, at harvest
    final_probability           NUMERIC(5,4)    CHECK (final_probability >= 0 AND final_probability <= 1),
    confidence                  NUMERIC(5,4)    CHECK (confidence >= 0 AND confidence <= 1),
    tier                        TEXT            CHECK (tier IN ('tier_1','tier_2')),
    status                      TEXT            NOT NULL
                                                CHECK (status IN ('dispatched','completed','failed','timed_out','needs_clarification'))
                                                DEFAULT 'dispatched',
    error_message               TEXT,
    agent_evidence_summary      JSONB,                      -- E5 projection
    brier_score                 NUMERIC(8,7),               -- NULL until the question resolves
    forecast_dispatched_at      TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    forecast_completed_at       TIMESTAMPTZ,
    UNIQUE (question_id, forecast_run_index),
    UNIQUE (idempotency_key)
);

CREATE INDEX IF NOT EXISTS idx_forecasts_question
    ON calibration_forecasts(question_id);

-- The harvester's every-5-minutes scan. 'needs_clarification' is deliberately
-- NOT in this predicate: it is terminal, so the harvester never rescans it.
CREATE INDEX IF NOT EXISTS idx_forecasts_status
    ON calibration_forecasts(status)
    WHERE status IN ('dispatched','timed_out');

CREATE INDEX IF NOT EXISTS idx_forecasts_query_doc
    ON calibration_forecasts(query_doc_id);


-- ==========================================================
-- Table 3: calibration_resolutions
-- ==========================================================
-- Ground truth from Polymarket. One row per question, ever.
--
-- UNIQUE (question_id) is what makes the hourly resolver naturally idempotent:
-- second detection is an INSERT ... ON CONFLICT DO NOTHING.
--
-- outcome_numeric is denormalized so Brier computation needs no join, and the
-- CHECK guarantees it is NULL exactly when the outcome is AMBIGUOUS — an
-- ambiguous market has no ground truth and must not silently score as 0.
CREATE TABLE IF NOT EXISTS calibration_resolutions (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    question_id                 UUID            NOT NULL UNIQUE
                                                REFERENCES calibration_questions(id) ON DELETE CASCADE,
    resolved_at                 TIMESTAMPTZ     NOT NULL,   -- as reported by Polymarket
    detected_at                 TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    outcome                     TEXT            NOT NULL
                                                CHECK (outcome IN ('YES','NO','AMBIGUOUS')),
    outcome_numeric             NUMERIC(2,1)    CHECK (outcome_numeric IN (0.0, 1.0)),
    resolution_source           TEXT            NOT NULL DEFAULT 'polymarket_clob',
    raw_resolution_data         JSONB           NOT NULL,
    CONSTRAINT ck_resolution_outcome_numeric_agreement CHECK (
        (outcome = 'YES'       AND outcome_numeric = 1.0) OR
        (outcome = 'NO'        AND outcome_numeric = 0.0) OR
        (outcome = 'AMBIGUOUS' AND outcome_numeric IS NULL)
    )
);


-- ==========================================================
-- Table 5: calibration_metrics_snapshots
-- ==========================================================
-- Point-in-time metric payloads (Phase 10C writes these).
--
-- payload shape varies by metric_type and is documented in
-- calibration/metrics/snapshots.py when that module lands.
CREATE TABLE IF NOT EXISTS calibration_metrics_snapshots (
    id                          UUID            PRIMARY KEY DEFAULT gen_random_uuid(),
    snapshot_at                 TIMESTAMPTZ     NOT NULL DEFAULT NOW(),
    metric_type                 TEXT            NOT NULL
                                                CHECK (metric_type IN
                                                    ('aggregate_brier','calibration_curve','cohort_brier',
                                                     'improvement_curve','source_contribution')),
    cohort                      TEXT            CHECK (cohort IS NULL OR cohort IN ('7d','14d','30-45d','all')),
    payload                     JSONB           NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_metrics_type_at
    ON calibration_metrics_snapshots(metric_type, snapshot_at DESC);
