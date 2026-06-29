# phase7b5_filter_calibration.md
> Domain: A — Pipeline
> Type: Sprint Plan
> Last updated: 2026-06-28
> TL;DR: The one open unit of Domain-A work — empirically calibrate the Phase 7B filter thresholds (`DEFAULT_THRESHOLD`, `GOLD_SEMANTIC_RESCUE_THRESHOLD`) and confirm the 10 A1 keyword removals against real production vault data. Self-contained: every concrete value is inline; you do not need to open old_docs to execute this sprint.

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

**Entry gate.** ≥200 post-7A `knowledge_vault` rows in cloud Postgres — **satisfied
(616+ rows available)**. The calibration sample must be drawn from **post-7A rows
only** (full-body shape); mixing in pre-7A snippet-era rows would reproduce the
stale-threshold problem 7B.5 exists to avoid.

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
| [ ] **T7B.1** | Run the `filter-analysis` skill against ≥200 **post-7A** full-body `knowledge_vault` rows. Classify TP/TN/FP/FN for each of the 10 A1 candidate terms (`strike, attack, odds, token, inference, default, revenue, vote, energy, defense`); confirm or trim the removal list. Also evaluate trimming the political `GENERAL_KEYWORDS` (`nato`, `sanctions`, `interest rates`, `central bank`, `ai regulation`) now that `news/Politics` is its own ingestion category (so political content no longer relies on those keywords for capture). |
| [ ] **T7B.2** | Threshold calibration. Pull the `relevance_score` distribution from the post-7A `knowledge_vault` rows; compute the percentile `0.15` corresponds to under the full-body distribution; confirm `DEFAULT_THRESHOLD = 0.15` does not drop legitimate high-signal articles, or pick the empirical replacement. |
| [ ] **T7B.9** | Semantic-rescue calibration. Run the rescue logic against ~100 known-low-signal (sniper-rejected) post-7A articles; manually classify rescued vs. dropped; pick the `GOLD_SEMANTIC_RESCUE_THRESHOLD` (within 0.30–0.40) that yields ≥80% precision on rescued items. Default currently `0.35`. |
| [ ] **Deliverable** | Produce `docs/phase7_filter_analysis.md` — the deferred Phase-7 Definition-of-Done item. Record the A1 confirm/trim outcome, the `relevance_score` distribution + chosen `DEFAULT_THRESHOLD`, and the rescue-precision analysis + chosen `GOLD_SEMANTIC_RESCUE_THRESHOLD`. |

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
