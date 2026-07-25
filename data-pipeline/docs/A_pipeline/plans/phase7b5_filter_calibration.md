# phase7b5_filter_calibration.md
> Domain: A — Pipeline
> Type: Sprint Plan
> Last updated: 2026-07-23
> TL;DR: The one open unit of Domain-A work — empirically calibrate the Phase 7B filter thresholds (`DEFAULT_THRESHOLD`, `GOLD_SEMANTIC_RESCUE_THRESHOLD`) and confirm the 10 A1 keyword removals against real production data. **The dataset now exists** (7B.5-I day-run `dayrun-20260722`): 1,254 distinct rejected articles with full text and cosine scores, plus the survivor side with `relevance_score` and `rescue_cosine`. Runs offline against the exported dumps — no cluster needed. Self-contained: every concrete value is inline.

> **Assumptions refinable after E1.** The data-source and sampling sections below
> were written before the extraction session's offsets probe ran
> (`plans/dayrun20260722_extraction.md`, E1). If E1 reveals a partially-eaten Silver
> window, materially different volumes, or field behaviour differing from what is
> documented, the sampling strategy and denominators here are open to revision.
> Anticipated and pre-approved — not a deviation.

## Navigation
- §0 — Why this sprint exists — the theoretical-vs-empirical gap Phase 7B left open
- §1 — Scope & entry gate — what is being validated, the concrete values, the (satisfied) entry gate
- §2 — Task table — T7B.1 / T7B.2 / T7B.9 + the deliverable, with `[ ]` checkboxes
- §3 — Gates — how the four-gate model applies to an analysis sprint
- §4 — Skills — which skills drive the work

---

## §0 — Why this sprint exists

Phase 7B (closed 2026-05-09) replaced the single-layer keyword filter with a
two-stage gate — a tighter deterministic keyword sniper plus an embedding-based
semantic rescue. It set every threshold **theoretically**, before production data
existed: the keyword removals, the sniper floor, and the semantic-rescue cutoff
were all chosen from design-session reasoning against pre-production (snippet-era)
estimates.

Phase 7B's own Definition of Done deferred the empirical validation to **Phase
7B.5**: calibrating against pre-production rows would have produced stale
thresholds, because the post-7A full-body article shape shifts the score
distributions (more keyword matches per article; denser embeddings). 7B.5 is that
validation — it confirms or replaces each theoretical value using **real cloud
`knowledge_vault` rows**, and produces the deferred `docs/phase7_filter_analysis.md`
report.

This is the only open unit of work in Domain A; everything else in the pipeline is
operationally closed.

---

## §1 — Scope & entry gate

**Goal.** Validate empirically the filter thresholds Phase 7B set theoretically,
using real production vault data instead of pre-production estimates.

**Entry gate.** — **SATISFIED, and superseded by a better dataset.** The original
gate (≥200 post-7A `knowledge_vault` rows) was written when the survivor side was
all that existed. The 7B.5-I day-run (`dayrun-20260722`, 2026-07-22 → 07-23) has
since produced **both sides of the cut**:

| Dataset | Where | Size | What it gives |
|---|---|---|---|
| Rejected articles | `filter_rejects` → `filter_rejects_full.csv` | **1,254 distinct** (2,164 raw rows, 1.7× dup) | `original_url`, untruncated `full_text_raw`, `relevance_score`, `rescue_cosine` — T7B.1's FN corpus and T7B.9's score sweep |
| Survivors | `knowledge_vault` → `knowledge_vault_full.csv` | 1,079 in-window (1,066 passed / 13 rescued) | `relevance_score`, `rescue_cosine` — the other half of the T7B.2 distribution |

Both arrive as CSV dumps from the extraction session
(`plans/dayrun20260722_extraction.md`, task E4). **This sprint therefore runs
entirely offline** — no cluster, no live Postgres. That decoupling is deliberate:
7B.5 must not be gated on cluster availability.

**Source coverage — hackernews is OUT (KG-A-12).** The social path runs the sniper
but has no semantic rescue and no reject capture, so hackernews rejects leave no
trace at all. It cannot be calibrated from this or any existing dataset. Record
this as an explicit scope exclusion in the deliverable; do NOT read
`funnel.csv`'s `rejects=0` for hackernews as evidence that nothing was rejected.
Calibration covers **newsapi, arxiv, telegram** only.

**Prior observation from 7B.5-I T6 (small, biased — a prior, not a finding):** 9
real sniper-failed articles all failed rescue at cosines 0.14–0.29 against the 0.35
threshold. Suggestive that the threshold is tight. The day-run corpus is the
evidence that decides; this only sets the expectation.

**Day-run signal worth noting before T7B.9 starts:** per `rejects_overview.csv`,
max `rescue_cosine` was 0.345 / 0.343 / 0.349 across the three sources — nothing
approached 0.35 from above — and only 13 of 1,079 archived rows (1.2%) entered via
rescue. Two readings are possible and they lead opposite ways: the threshold is
well-placed, or the whole distribution sits below it and the rescue stage is
near-inert. T7B.9 must distinguish them, not assume either.

**The concrete values under validation (inline — self-contained):**

- **`DEFAULT_THRESHOLD = 0.15`** (raised from `0.09` in Phase 7B decision A2). The
  relevance-score floor of the deterministic sniper. The 0.09 floor was too
  permissive under full-body articles; 0.15 was the theoretical target. The full-body
  distribution may push the empirical optimum higher — T7B.2 picks the value from the
  post-7A distribution.
- **`GOLD_SEMANTIC_RESCUE_THRESHOLD = 0.35`** (Phase 7B decision B3; env-overridable
  via `GOLD_SEMANTIC_RESCUE_THRESHOLD`). The cosine-similarity cutoff for the
  semantic-rescue gate, proposed range **0.30–0.40** (`text-embedding-3-small`
  distributions skew higher than ada-002). Final value = the point where rescued
  items reach ≥80% precision in manual classification.
- **The 10 A1 keyword removals** from `MASTER_KEYWORD_LIST` (Phase 7B decision A1):
  `strike`, `attack`, `odds`, `token`, `inference`, `default`, `revenue`, `vote`,
  `energy`, `defense`. Compound forms are **kept** (`missile defense`, `armed forces`,
  `energy crisis`, `nuclear energy`, `fusion energy`). 7B.5 confirms or trims this
  list against real false-positive/false-negative rates.

**Behavior context (from Phase 7B, for grounding the calibration).** Articles that
fail BOTH the sniper and the semantic-rescue threshold are **dropped entirely** (no
`kv_archive`, no Gold) — the vault becomes strictly higher-signal but loses some
breadth. The drop/promote decisions both log at INFO. The two risks this calibration
must keep in view:
1. **Vault-breadth shrink** — too-strict thresholds drop articles the hub's reactive
   search relied on for breadth. The INFO drop logs + env-overridable rescue
   threshold are the fast-rollback lever.
2. **Reference-vector drift** — `processing/sniper_reference_vector.npy` is a
   mean-pooled embedding of the post-A1 `MASTER_KEYWORD_LIST`. If the keyword list
   changes (e.g. T7B.1 trims it further), the vector must be regenerated via
   `python processing/build_sniper_reference_vector.py` or semantic rescue silently
   degrades.

---

## §2 — Task table

| Task | Description |
|---|---|
| [ ] **T7B.0** | **Sampling frame (do this first).** The corpus is 1,254 distinct rejects — nobody is reading 1,254 articles. Draw a **stratified sample of ~120**: ~40 per source (newsapi / arxiv / telegram), stratified within source by `rescue_cosine` decile, **over-weighting the top decile (≈0.28–0.35)**. That band is where the false negatives and the threshold decision both live; the bottom deciles are almost certainly true negatives and reading them buys little. Draw a matched ~60-article sample of SURVIVORS from `knowledge_vault_full.csv` the same way (by `relevance_score` band) — without it there is no false-positive measurement, only false-negative. Record the sampling seed and the frame so the T7B.6 re-run in §3 is reproducible against the identical sample. |
| [ ] **T7B.1** | Run the `filter-analysis` skill over the T7B.0 sample. Classify TP/TN/FP/FN for each of the 10 A1 candidate terms (`strike, attack, odds, token, inference, default, revenue, vote, energy, defense`); confirm or trim the removal list. Also evaluate trimming the political `GENERAL_KEYWORDS` (`nato`, `sanctions`, `interest rates`, `central bank`, `ai regulation`) now that `news/Politics` is its own ingestion category (so political content no longer relies on those keywords for capture). Source: `filter_rejects_full.csv` (`full_text_raw` is untruncated by design, precisely so the article can be read) + `knowledge_vault_full.csv`. Report precision/recall **per source**, never pooled — the three sources have materially different baselines (see the telegram note below). |
| [ ] **T7B.2** | Threshold calibration — **now genuinely possible for the first time**, because both sides of the cut carry `relevance_score`. Build the full distribution from `knowledge_vault_full.csv` (survivors) UNIONed with `filter_rejects_full.csv` (rejects), in-window, deduped by `original_url`. Locate where 0.15 falls in it; sweep candidate thresholds and report, for each, how many sampled TP/FN/FP items change side. Confirm `DEFAULT_THRESHOLD = 0.15` or pick the empirical replacement. Note: `survivors_split.csv` (1,066 passed / 13 rescued, score-cut and marker agreeing exactly) validates instrumentation consistency — it is **not** a calibration result and must not be cited as one. |
| [ ] **T7B.9** | Semantic-rescue calibration. Two parts, in order. **(a) Cheap and decisive, no human judgement:** histogram `rescue_cosine` over all 1,254 distinct rejects, then count how many would be rescued at 0.30 / 0.32 / 0.34. If that count is trivial, the rescue stage is near-inert and the real question becomes whether to keep it at all (it costs $0.0079/day — nothing — but it is a code path and a latency cost). If the count is large, there is a real threshold decision. **(b) Only then**, manually classify the rescued-at-candidate-threshold items from the T7B.0 sample and pick the value in 0.30–0.40 yielding ≥80% precision on rescued items. Default currently `0.35`. |
| [ ] **T7B.10** | **Per-source threshold question (raised by the day-run).** telegram showed the highest average reject cosine (0.2317) and **zero duplication** (268 rows / 268 distinct) — consistent with the `filter-analysis` skill's own note that telegram arrives pre-filtered at the channel level. Decide explicitly: does telegram warrant a different threshold from newsapi? Answer from the T7B.1 per-source metrics. A decision either way is acceptable; leaving it implicit is not. |
| [ ] **Deliverable** | Produce `docs/A_pipeline/reports/phase7_filter_analysis.md` — the deferred Phase-7 Definition-of-Done item. Record: the A1 confirm/trim outcome; the `relevance_score` distribution + chosen `DEFAULT_THRESHOLD`; the rescue histogram, the (a)/(b) outcome and the chosen `GOLD_SEMANTIC_RESCUE_THRESHOLD`; the T7B.10 decision; the sampling frame and seed; and an explicit statement that **hackernews is excluded (KG-A-12)** and why. |

---

## §3 — Gates

This is an **analysis-only** sprint: the work is empirical classification and report
authoring, not new pipeline code. The pipeline four-gate model (Gate 1 / Gate 2 /
Gate 3 / E2E) therefore applies **lightly** — there is nothing new to import,
no new pure-function logic, no new persistence round-trip by default.

The one way code is touched is if calibration replaces a baked threshold value (e.g.
`DEFAULT_THRESHOLD` in `processing/keyword_sniper.py`, or the
`GOLD_SEMANTIC_RESCUE_THRESHOLD` default in `config/settings.py`). Any such value
change routes through the **normal gates** — Gate 2 unit tests over the new value
(e.g. `test_default_threshold_in_range`) plus an E2E re-baseline of drop/rescue rates
— and, if the A1 keyword list changes, a regeneration of
`processing/sniper_reference_vector.npy` (reference-vector drift, §1 risk 2).

---

## §4 — Skills

- `sprint-kickoff` — open the sprint and establish the working context.
- `filter-analysis` — drives T7B.1 / T7B.2 / T7B.9 (TP/TN/FP/FN classification,
  score-distribution pulls, rescue precision) against cloud `knowledge_vault`.

---

> Original Phase-7 design rationale (closed): `docs/old_docs/phase7_intelligent_filtering.md`
> — historical, not required to execute 7B.5.
