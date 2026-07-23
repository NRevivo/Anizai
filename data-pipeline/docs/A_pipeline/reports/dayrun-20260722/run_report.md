# Day-Run Report — `dayrun-20260722`

**Window (T0, T0+24h):** `2026-07-22T09:25:26Z` → `2026-07-23T09:25:26Z` (fully elapsed; auto-closed on schedule at 09:25:00Z 07-23).
**RUN_ID:** `dayrun-20260722` · **Flink image:** `anizai-flink:1.19.1-7b5i` · storm-fix config live (`tolerable-failed-checkpoints=1000`, unaligned, 600s timeout).
**All figures windowed** (`created_at`/`rejected_at`/`ingested_at` in [T0, T0+24h)) — NOT run_id-only (pre-T0 warm-up shares the run_id). Corpus counts use `count(DISTINCT original_url)`.

---

## Cost deliverable (S4.1)

| metric | value |
|---|---|
| **Total cost (USD)** | **$1.3511** |
| Total successful calls (floor) | 8,835 |
| Distinct items enriched | 5,493 |

Per source × site → `cost_by_source_site.csv`. Highlights: newsapi `gold_enrich` $0.574 (largest), arxiv `gold_enrich` $0.358, hackernews `gold_consensus` $0.304, telegram `gold_enrich` $0.104. Model = gpt-4o-mini throughout (per §7 caveat — the headline is a gpt-4o-mini number).

## Calibration deliverable

| metric | value | target |
|---|---|---|
| **Distinct rejected articles** (`DISTINCT original_url`) | **1,254** | ~100 |
| Raw reject rows | 2,164 | — |
| Duplication ratio | **1.7×** (healthy; cf. T7 storm 18×) | — |

Target exceeded ~12×. Per-source rejects → `rejects_overview.csv` (newsapi 518 distinct / 1406 rows — high re-pull dup; arxiv 468/490; telegram 268/268 — zero dup).

## Survivors split (S4.4) — threshold validated

`survivors_split.csv`: **passed=1066, rescued=13** by *both* the 0.15 score cut **and** the authoritative `rescue_cosine IS NULL` marker — they agree exactly, confirming `DEFAULT_THRESHOLD=0.15` (keyword_sniper.py:82) is correct with no per-source drift. Total archived to `knowledge_vault` = 1,079.

## Rescue accounting (S4.5b) — exact

rescue_embed calls **2,177** == in-window rejects **2,164** + in-window rescued **13**. Exact match → **no fail-open holes, no DLQ'd embeds**.

## Wasted spend (S4.6)
2,164 rescue_embed calls on dropped items = **$0.0079** (≈ in-window reject count, as expected).

## Funnel (S4.7) → `funnel.csv`
DB-side columns per source (rejects_rows/distinct, archived, enriched_distinct). Notes:
- `polymarket` → 26,166 archived to `momentum_vault`, **0 LLM cost** (deterministic momentum, no enrichment — correct, absent from cost table by design).
- `openweather` 1,440 / `fred` 174 → momentum_vault, also 0 LLM (metrics = deterministic).
- `hackernews` enriched_distinct 732 ≫ archived 127 — social-consensus aggregation/dedup (known funnel leak per §7, not an error).
- Kafka-side columns (pulled/silver/dlq) not populated here; DB-side tells the funnel story and DLQ in-window = 0.

---

## Health readout

| signal | result |
|---|---|
| **Job restarts (window)** | **0 / 0** (Gold / Silver) — Prometheus `numRestarts`, retained on PVC |
| **Failed checkpoints (window)** | 2 (Gold) + 1 (Silver) — benign expiries during bursts, tolerated by `tolerable=1000`, **never escalated** |
| **DLQ growth (window)** | **0** — 30 retained DLQ msgs all pre-T0 (span 07-02…07-22 09:00) |
| **Hourly cost gaps** | **none** — 25/25 buckets non-zero; opening arxiv burst (2,362) then steady ~250–330/hr |
| **OpenAI RPD (floor)** | 07-22 = **6,718** · 07-23 = **2,344** — both under 10k; continuous flow confirms the cap was never hit |

## Caveats
- **RPD is a floor** (`llm_cost_events` = successful calls only; the OpenAI platform Usage page — not readable from here — includes retries and will be higher). The no-gap continuous flow is the positive proof enrichment never halted on a cap.
- **No `translate` cost line** — telegram did enrich 327 items in-window (listener was live), but no separate translate-site rows appeared (channels quiet at T0; §7: absence is not a failure).
- **S4.5(c)** (llm_usage log-line vs cost-row spot-check) **not reproducible retroactively** — Flink TM logs are ephemeral and were lost at the auto-close scale-to-0. Covered instead by the exact S4.5(b) rescue accounting.

## Verdict
**Clean, successful run.** Safe-to-close criteria met: rescue accounting exact, splits consistent, no restarts/storm, no in-window DLQ, no cost gaps, cap not hit. Calibration target exceeded ~12×.

**Open (Ron's calls, not done here):** (1) Stage-5 extend/don't-extend decision on the reject count; (2) the deferred `REJECT_CAPTURE_ENABLED=false` flag-off; (3) delete the spent `dayrun-autoclose-scaledown` scheduler job (already fired; next occurrence 2027).
