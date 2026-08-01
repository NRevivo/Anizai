# V5 cloud verification — 2026-08-01

> Domain: A — Pipeline
> Type: Report — point in time
> Sprint: `polymarket_completion.md` (Tracks P / S / A)
> TL;DR: V0 purge, cold Flink bring-up on a new image, and V5's six-criteria
> acceptance — **1,282 / 1,282 rows passed all six**. V6 deferred. Written during
> the session per `bringup_profiles.md` §4 item 2: Prometheus retention is
> evaluated on startup, so numbers not written down during a session can be
> purged minutes into the next one.

## 1. Images

| Image | Tag | Digest |
|---|---|---|
| Flink (new, deployed this session) | `1.19.1-pmcov` | `sha256:963cbf9dea97d3470b084a9411ca84281bfb1c7222708886aa4cb816c0238c52` |
| Flink (previous) | `1.19.1-7d` | `sha256:9a73a780ea7eae000a39cd3c20d9b6f94b223559e92feec41766c4faebced303` |
| Producer (new, deployed this session) | `0.4.0-coverage` | `sha256:1d27ec0591f83f71a3c91998d68e321cb54d622f0e9e98a8d66259df6c43d758` |
| Producer (previous) | `0.3.0-price` | `sha256:2ae00dae1f9ab896fcc456611e247063b2a1f81c95b1e491e37192b8d899cac8` |
| Producer (rollback) | `0.2.0-p95` | `sha256:a2a3e82eea47353d4f29f41c1b5e59f15ed50028c6d86f929bfacdc6e93cfd5c` |

**KG-A-15 gate:** `pip freeze` inside `1.19.1-pmcov` vs the deployed digest —
**118 packages, byte-identical, 0 moved.** Compared by digest, not by tag.

## 2. V0 — backlog purge

Sized as `--time -1` minus `--time -2`. A cumulative end offset is not a backlog.

| Topic | Retained before | After |
|---|---:|---:|
| `ingest.bronze.polymarket` | 1,800 | 0 |
| `process.silver.structured_metrics` | 700 | 0 |
| `ingest.bronze.arxiv` | 2,800 | 0 |
| `ingest.bronze.hackernews` | 300 | 0 |
| `ingest.bronze.newsapi` | 226 | 0 |
| **purged** | **5,826** | **0** |

Preserved deliberately: `serve.gold.structured_metrics` (700, terminal),
`dead-letter-queue` (30, diagnostic value), `ingestion_triggers` (8).

**The Silver-topic finding.** `process.silver.structured_metrics` held 700 records
written by the OLD mapper — one stage past the entrance and one step from the
vault. A Bronze-only purge would have produced exactly the contamination V0
exists to prevent, by a route the step did not cover. Sources start from
`KafkaOffsetsInitializer.earliest()`, so a resubmitted job replays whatever is
retained. **Rule: size every topic in the path, not just the entrance.**

## 3. V3 — the HA recovery hazard, confirmed live

Listing 1, immediately after the JobManager came up on the **new** image:

```
03ad8689c39da094790bef8846a30189 : anizai-silver-polymarket  (RUNNING)
785ea9a1e70fc1bf30e26f2c5868e756 : anizai-gold-all-sources   (RUNNING)
```

Both JobIDs match the HA ConfigMap names in the 2026-07-30 teardown record
character for character. These were the pre-teardown job graphs resurrected from
HA state, running **old compiled code on a pod carrying the new image**, RUNNING
with 0 restarts. Nothing in the pod status, image reference or job state would
have revealed it.

Listing 2, after cancel + resubmit:

```
Running:    d1b1df405646b219b21fdaaed8f456f4 : anizai-silver-polymarket
            78ab7fe489e934a634f27b464b0950f4 : anizai-gold-all-sources
Terminated: 03ad8689…  (CANCELED)      785ea9a1…  (CANCELED)
```

**A Flink bring-up after an image change does not run the new image's code unless
the recovered jobs are cancelled first, and the failure is silent.** Without the
five steps, V5 would have shown rows missing catalog fields and the hunt would
have gone to `silver_job.py` — a file already fixed.

`LOG_INFO_SAMPLE_RATE` confirmed **absent** from both pods' actual environment via
`printenv`, not merely from the manifests.

## 4. V5 — the funnel, live

```
[polymarket] Discovery funnel: 372 events fetched -> 230 passed tags -> 177 passed endDate
             -> 2547 nested markets -> 1282 collectable (skipped 265 closed, 1000 never-traded)
[polymarket] Price sweep: 1282 emitted, 0 skipped, 1282 total in 3.0s (next sweep in 3600s)
```

| Stage | Local 2026-07-30 | Cloud 2026-08-01 | Δ |
|---|---:|---:|---:|
| events fetched | 386 | 372 | −14 |
| passed tags | 240 | 230 | −10 |
| passed endDate | 192 | 177 | −15 |
| nested markets | 2,712 | 2,547 | −165 |
| collectable | 1,396 | 1,282 | −114 (−8.2%) |

Fully explained by D6 churn over two days (~−8 events/day predicted, −14 observed
over two). `0 skipped` on the sweep confirms the never-traded pre-filter holds in
production — G3's no-WARNING-storm requirement is structural, not incidental.
3.0 s for 1,282 markets confirms the sweep makes no per-market HTTP call.

## 5. V5 acceptance — PASSED

```
rows_passing_all_six = 1282   (of 1282 landed)

c1 non-zero current_value  1282     c4 market_id present      1282
c2 clob_token_ids obj+Yes  1282     c5 unit = probability     1282
c3 real status             1282     c6 event_title populated  1282

out_of_range (outside [0,1]) = 0
```

Status distribution: **1,171 active / 108 inactive / 3 archived**. Under the old
hardcoded `"active"`, 111 rows (8.7%) would have asserted a false market state.

## 6. Open finding — the 0.5000 rows (V6-blocking, undecided)

All 108 `inactive` rows carry **exactly 0.5000** — `distinct_prices = 1`, against
305 distinct values across the 1,171 active rows. Their questions are unnamed
placeholder legs:

```
Will Company A be the largest company in the world b…  {'Yes': 0.5, 'No': 0.5}
Will Company B be the largest company in the world b…  {'Yes': 0.5, 'No': 0.5}
```

These are template slots with no entity bound, not markets with a stale price.
Polymarket writes 0.5/0.5 because there is nothing to price. Independently
corroborated by the frontend's `trending.repository.ts`, which already filters
`active !== false` and measured it removing 521 of 1,039 non-closed markets with
zero legitimate losses.

**`archived` is NOT the same case** — its 3 rows carry real distinct prices
(0.49, 0.255, 0.25) and real questions. A filter must target `inactive`, not
"not active".

**Nothing in the agent path branches on status.** `market_status` is carried on
the payload and read by no consumer; A4's guard keys on `end_date_iso`, and
`_apply_resolved_market_guard` flips `has_ended` only on `closed` or a past end
date. An `inactive` row resolves normally and renders "Market Consensus 50%" —
invented, confident, beside a real forecast.

Options recorded, decision pending: **(a)** drop `inactive` at producer
selection; **(b)** refuse resolution against a non-`active` row, falling to A5's
refusal. Neither is free — both need an image rebuild the next window needs
anyway.

## 7. Second window (same day) — V4 and V6

**V4.** The deployed agent was `anizai-agent:0.5.0-sprint26`, built 2026-07-23 —
a week BEFORE Track A landed in `d603450`. Bringing it up would have tested an
agent with no A1/A3/A4/A5 and emitted the OLD `NO_MARKET_CAPTION`, looking like a
plausible refusal while proving nothing. Rebuilt as
**`anizai-agent:0.6.0-trackA`, digest `sha256:937dfed1…471d9aee`**, from
`35c343b`, with all four verified inside the image before push.

**The `inactive` filter, live.** Funnel:
`374 events -> 230 tags -> 177 endDate -> 2547 nested -> 1173 collectable
(skipped 266 closed, 1106 inactive, 2 never-traded)`. Acceptance **1,173/1,173**
on six criteria; **0 rows at exactly 0.5000** (was 108); status distribution
`active 1170 / archived 3 / inactive 0`.

**Note on the skip attribution.** The prediction was ~108 inactive / ~1000
never-traded; the reality was 1,106 / 2. `_market_skip_reason` checks `inactive`
before `never_traded`, and the never-traded population turned out to BE the same
placeholder legs. So the placeholder population was never 108 — it was ~1,106,
of which only the 108 that had acquired an `["0.5","0.5"]` array could reach the
vault. Collectable matched the prediction exactly (1,173).

**V6 — three of four pass.** No wrong-market resolution (August 3 against seven
one-word-apart siblings), YES side correct (0.245 / 0.77 against cards of 25% /
76%), CLOB history real (743 points, 30d 23h, 60.1-min gaps, 0.14s, vault
fallback unused). **Open: `via=question-match` on both — see KG-A-22.**

### predictionSeries point counts are NOT a quality metric

Forecast 1 returned **743** points; forecast 2 returned **15**. The 15 is not
degradation and must not be "fixed": that market opened the previous day, so 15
hourly points is its *entire* history. Both were served from CLOB (0.14s /
0.15s) with the vault fallback holding 2 rows and going unused in both cases.
The system showed what exists rather than padding it to look complete — which is
the same principle as refusing rather than fabricating a benchmark. A future
reader seeing "only 15 points" should check the market's age before treating it
as a fault.

### A4 verified against data, not exercised live

Forecast 2's sibling markets at ~100% are ones whose dates have passed:
`through July 29?` (0.9995, `end_date_iso 2026-07-29`), `July 30?` (0.9995,
`2026-07-30`), `July 31?` (0.9985, `2026-07-31`). All three carry
`status='active'` in the vault, so **status alone would not have flagged any of
them** — which is exactly why A4 keys on the end date. Had one been picked, the
guard's live-confirmation branch would have fired.

## 8. Not done

The database wipe follows, and is a separate operation on its own timing.
