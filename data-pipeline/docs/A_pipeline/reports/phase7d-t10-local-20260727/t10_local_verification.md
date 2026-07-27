# Phase 7D — T10 Local Verification Report (2026-07-27)

> HARD STOP task. Local verification against real Postgres is complete. **No cloud
> work has been done and none will be until Ron gives explicit approval.**

## Environment

- **Real local Postgres** brought up from `infrastructure/docker-compose.yml`
  (`anizai-postgres`, timescaledb-ha:pg16, host `localhost:5432`), against Ron's
  **pre-existing `postgres_data` volume** — a DB that **predates this sprint**.
- Confirmed pre-migration state: `filter_rejects` **exists** (migration 003 /
  7B.5-I) but **`canonical_event_id` is ABSENT** → the closest available analogue to
  the cloud database, which also lacks the column. This is what makes migration
  004's `ALTER TABLE` path genuinely execute (a fresh init.sql build would already
  have the column and never exercise the ALTER — rejected as Option A).

## Results

### 1. Migration 004 idempotency (applied twice, real ALTER path)
- `column present BEFORE any apply: False` → **apply #1 ADDed the column** (the ALTER
  path executed — the cloud path).
- After #1: `canonical_event_id` columns = 1, `idx_filter_rejects_cei` = 1.
- **apply #2** (re-apply): **no error**, still exactly 1 column and 1 index (no
  duplicate). **IDEMPOTENT.**

### 2. Full affected suite WITH the DB up — 343 passed, 0 skipped
- The **five DB-gated tests all executed and PASSED** (no longer skipping):
  - `test_filter_rejects_gate3.py` ×4 — canonical_event_id round-trip; NULL for a
    missing key; `""` → NULL; HackerNews `social_reject_doc`-shaped reject round-trip
    (source_name='hackernews', url→original_url, instance key).
  - `test_knowledge_vectors_dedup_gate3.py` ×1 — the ON CONFLICT proof (below).
- All oracles + Phase 7D units + the four updated existing tests green with the DB up.

### 3. ON CONFLICT guard PROVEN by execution (not by reading DDL)
- Inserted the same Gold record **twice** into `knowledge_vectors` against real
  Postgres: **no exception**, and `COUNT(*) == COUNT(DISTINCT signal_id) == 1`.
- This is **T5's effect, reported separately** (row count vs distinct signal_id after
  a deliberate re-delivery): the deterministic signal_id + PRIMARY KEY +
  `ON CONFLICT (signal_id) DO NOTHING` collapse a re-delivery to exactly one row.

### 4. Schema convergence (init.sql ≡ migration 004)
- Diffed `filter_rejects` on the migrated DB vs a fresh **init.sql** build (throwaway
  instance on 5433, real password — not trust): **12 columns identical**
  (name/type/nullability/default), **3 indexes identical**
  (`filter_rejects_pkey`, `idx_filter_rejects_cei`, `idx_filter_rejects_run`).
- Only the **physical column order** differs (migration appends `canonical_event_id`;
  init.sql places it after `source_name`) — immaterial; access is by name. An
  upgraded cloud DB will match a fresh cloud install.

### 5. Before/after — the GATE's effect, isolated by the flag (not by git)
Identical duplicated stream, `ENRICHMENT_DEDUP_GATE_ENABLED` false vs true, via the
real `dedup_skip_enrichment` over a stateful archive model:

| Stream (day-run-matched) | Gate OFF | Gate ON |
|---|---|---|
| newsapi-like (day-run 2.11/item) | 2.10 calls/item | **1.00** |
| arxiv-like (day-run 8.4/item) | 8.40 calls/item | **1.00** |
| telegram control (no duplicates) | 1.00 | 1.00 (unchanged) |

## Stated plainly (per Ron's directive — not softened)

- **This before/after isolates the GATE only.** T4's `story_id` key and T5's
  deterministic id are active in **both** runs; the comparison measures the gate's
  enrichment-call effect, **not the sprint's total effect**. T5's effect is reported
  separately (§3 above).
- **The wired code was NOT executed locally.** Local Flink is broken (KG-A-10) and
  `process_element` lives under `if PYFLINK_AVAILABLE:`, so the T3 gate inside
  `GlobalNewsGoldFunction.process_element` and the T4/T6 social branch **have not run
  and cannot run locally**. T9 proved the extracted helpers and the branch ORDER by
  AST; T10 proved the schema, the DB semantics (ON CONFLICT, idempotency,
  convergence), and the helpers against real Postgres. **Neither executes the wiring
  — the first hour in the cloud is the first EXECUTION of that wiring, not a
  re-verification of it.**

## Teardown
- Throwaway 5433 instance removed (`docker rm -f`).
- `anizai-postgres` compose container removed; **`postgres_data` volume preserved**
  (no `-v`, no destructive SQL — §8.4.3 respected).
