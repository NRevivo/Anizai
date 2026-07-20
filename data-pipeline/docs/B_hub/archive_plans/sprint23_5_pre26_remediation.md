# sprint23_5_pre26_remediation.md
> Domain: B — Agentic Hub
> Type: Sprint Plan
> Last updated: 2026-06-20
> TL;DR: An out-of-band remediation sprint that lands **before Sprint 24**. It
>        closes the cross-sprint-seam defects found in the 2026-06-18 audit —
>        the orphaned `sufficiency_check` node (KG-B-15), the trigger node's
>        graph-interface mismatch (KG-B-16), the `raw_question` plan/code drift,
>        the structurally-insufficient LLM cost instrumentation (KG-B-6, rescoped),
>        and the split `AGENT_VERSION` source. It absorbs Sprint 26's T26.1 / T26.5
>        / T26.7. After 23.5, Sprint 26 is pure hardening + observability.

## Navigation
- §0 — Why this sprint exists — the audit finding in one paragraph
- §1 — Scope & Non-Goals
- §2 — Track 1: Reactive / Sufficiency Correctness
- §3 — Track 2: Cost Instrumentation (full picture)
- §4 — Track 3: Version Hygiene
- §4.5 — Track 4: Test Baseline + Cleanup & the Suite-Green Gate
- §5 — Gates
- §6 — Design Decisions
- §7 — How this amends the revised plan (Sprints 24–27)
- §8 — Cost-scope reconciliation with KG-PHASE-9.5-9

---

## §0 — Why this sprint exists

The 2026-06-18 audit (see `cabinet-outputs/advisor/problem-reports/kg-b-15-and-sprint-22-27-audit.md`)
found that the Phase 8 plans are reliable where a sprint is self-contained and
unreliable at the **seams** between sprints — wherever one sprint's task was
written against an *imagined* output of another sprint instead of the real code.
Every defect found sits on such a seam:

| Seam | Unverified assumption | Reality |
|---|---|---|
| Sprint 23 → 26 | a `sufficiency_check` node exists, populates `missing_dimensions` | no such node (KG-B-15) |
| Sprint 26 T26.7 | the trigger node conforms to the `.run` graph interface | it exposes `trigger_reactive_ingestion()` (KG-B-16) |
| Sprint 23 keyword build | keywords = entities + missing_dimensions + raw_question | code uses entities only (D4 drift) |
| Sprint 26 T26.1 | the `rate_evidence` "pattern" already logs cost | logs `total_tokens` only; no cost layer (KG-B-6) |
| Sprint 26 T26.5 | `agentVersion` lives in `config/settings.py` | hardcoded in `agent/nodes/synthesize.py` |

The root cause of the first row: the **2026-05-23 re-plan** repurposed Sprint 22
(which, per the Sprint-20 `graph.py` docstring, originally carried the
`sufficiency_check` node) into "Foundation Fixes," and the node was never
re-scheduled. Sprint 26 T26.7 inherited the assumption it existed.

This sprint resolves all five before Sprint 24 begins, so that (a) the reactive
path is correct and integrated, and (b) the cost instrumentation exists **before**
Sprints 24 and 25 add new LLM call sites.

---

## §1 — Scope & Non-Goals

**In scope:** the five remediation items above, grouped into three tracks. All
work is inside `data-pipeline/`. Decision locked (2026-06-18): the trigger node is
**wired into the graph in this sprint** — the sufficiency node, the interface fix,
and the wiring are one coherent change and are not split across sprints.

**Non-goals (unchanged from the revised plan):**
- No Tavily/Brave reactive-search microservice (Future Enhancement 1, deferred).
- No polling/wait reactive variant — trigger-and-forget stays (FE 1, Option B).
- No follow-up escalation, suggested actions, or agentEvents — those are
  Sprints 24/25.
- No performance *optimization* — the latency analysis stays in Sprint 26 (T26.3).
- No Polymarket vector index, no sentiment-quality upgrades (FE 4/5).

**Blockers:** none. Sprints 22 and 23 are complete; this sprint depends only on
their (audited, confirmed-real) output.

---

## §2 — Track 1: Reactive / Sufficiency Correctness

Builds the missing `sufficiency_check` node as it was originally intended,
reconciles the keyword drift, fixes the node interface, and wires the reactive
path into the graph end-to-end.

> **Amended by the Advisor↔Ron decision record (2026-06-20), R1.** This
> section supersedes the original 23.5.4/D3/D4 "entities + missing_dimensions"
> keyword reconciliation. The locked V1 behavior and its rationale follow.

### R1 — V1 reactive keywords are entities-only + the 7-day recency window

**Decision (locked):** V1 reactive keywords = `structured_intent.entities`
**only**, bounded by the trigger's existing **7-day recency window**
(`AGENT_REACTIVE_DEFAULT_WINDOW_DAYS` → `run_reactive(time_window_days=7)`).
`missing_dimensions` is **not** fed into `_build_keywords`.

`sufficiency_check` still **decides whether to trigger** (signal-count floor +
per-entity coverage) and still **records** `missing_dimensions` in the
`sufficiency_checks` entry — for telemetry and as the seam for a future
`vault_query_2` / LLM-refine path — but it does **not** shape keywords.

**Why entities-only + recency is correct and sufficient for V1 (the "why",
recorded so it's understandable later):** the gap reactive ingestion actually
closes is **recency** — fresh, last-7-days articles about the question's
entities that the vault does not yet have — **not topic**. Three reasons this
is the right V1 design:

1. **Folding `missing_dimensions` in would be a no-op anyway.** Uncovered-entity
   names are a *subset* of `entities`, which `_build_keywords` prepends first
   and dedups case-insensitively, so they collapse away — they add nothing to
   the keyword set.
2. **The recency window is what's doing the work.** Every trigger carries the
   7-day window, so it fetches *fresh* coverage of the entities the vault is
   thin on. That is the missing dimension that matters at query time.
3. **Overlap is cheap and harmless.** Silver's SHA-256 dedup drops any fetched
   article that already exists in the vault, so re-pulling entity coverage
   costs nothing and can't pollute the vault.

Topic-shaping keywords derived from `missing_dimensions` belong to a future
`vault_query_2` / LLM-refine enhancement, not this sprint. This is also
simpler/less code than a "plain-string missing_dimensions" variant.

### Target topology (this sprint)

```
… build_embedding → vault_query → sufficiency_check
                                     ├─ sufficient?   ──▶ rate_evidence → synthesize → write_to_firestore → END
                                     └─ insufficient? ──▶ trigger_reactive_ingestion ──▶ rate_evidence → … (trigger-and-forget)
```

The insufficient branch dispatches the trigger and then **continues to
`rate_evidence`** with whatever evidence is currently available — it does not wait
for ingestion (trigger-and-forget). The trigger fires at most once per session
(`AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION=1`).

### Task table

| Task | Status | Description | Gate(s) |
|---|---|---|---|
| 23.5.1 | `[x]` | `agent/nodes/sufficiency_check.py` — node `run(state)` that evaluates the three evidence packages against `structured_intent` and writes `state.sufficiency_checks` (append): a `{is_sufficient: bool, missing_dimensions: list[str], reason: str, attempt: int}` dict. Deterministic V1 rubric (per `agent-design`): signal-count floor (`AGENT_EVIDENCE_MIN_COUNT`) **AND** per-entity coverage — `structured_intent` exposes `entities` (no `dimensions` field), so "dimension coverage" is realized as per-entity evidence coverage; uncovered entities become `missing_dimensions`. Signal count comes from the shared **`count_raw_signals`** helper (R2 — operates on raw pre-normalization packages, not normalized EvidenceItems) so it can't drift from `rate_evidence`. No LLM, no `agentEvents` (R6). Single-responsibility, state-as-contract (hub-principles). | Gate 1, Gate 2 |
| 23.5.2 | `[x]` | Sufficiency rubric mechanism **decided: deterministic for V1** (evidence-count floor + per-entity coverage heuristic, **no LLM**) — keeps the node O(signals×entities), adds zero cost/latency before the initial test. `missing_dimensions` = entities with no supporting evidence. LLM-based sufficiency is a Future Enhancement if signal quality is poor. Recorded in §6 (D2). | — |
| 23.5.3 | `[x]` | Fix trigger node interface (**KG-B-16**): rename the entry point to `run(state)` (keep internal helpers `_build_keywords`, `_send_to_kafka`, `_log_attempt`, `_get_producer`). Update the module docstring. No behavior change beyond the name. | Gate 1 |
| 23.5.4 | `[x]` | **(Amended per R1.)** Reconcile the `raw_question` drift: V1 keyword set is **entities-only** (raw_question excluded; `missing_dimensions` recorded but **not** merged — see §2/R1). `_build_keywords` is reverted to entities-only and its docstring records the recency rationale. Update the revised plan §Sprint 23 text + T23.5 keyword description to entities-only. | Gate 1 |
| 23.5.5 | `[x]` | Wire into `agent/graph.py` (**absorbs T26.7**): add `sufficiency_check` after `vault_query`; add a routing function `_route_after_sufficiency` reading the latest `sufficiency_checks` entry + `reactive_triggers_emitted < AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION`; conditional edge → `trigger_reactive_ingestion` (insufficient) or `rate_evidence` (sufficient); `trigger_reactive_ingestion → rate_evidence`. Routing reads state only, no LLM (agent-design P3). Update node-name constants + the module docstring. | Gate 2 |
| 23.5.6 | `[x]` | `AGENT_EVIDENCE_MIN_COUNT` (default per audit; env-overridable) added to `agent/config/settings.py`. | Gate 1 |
| 23.5.7 | `[x]` | **E2E split gate (per R7/decision record §4).** **Half A — hard closeout gate:** insufficient question → `sufficiency_check` marks insufficient → one trigger emitted → `reactive_triggers_log` row written → synthesis proceeds on available evidence (trigger-and-forget). Real Kafka + real Postgres; LLM/vault/Firestore mocked for determinism. **Half B — non-blocking PoC:** STUB the NewsAPI fetch → assert 1–2 canned articles land in Bronze (`BRONZE_NEWSAPI`) via the trigger consumer's dispatch (Kafka only). **Out of scope for 23.5:** the "fresh session surfaces the new articles as evidence" link (needs full Bronze→Silver→Gold→vault enrichment) — deferred. | E2E |

---

## §3 — Track 2: Cost Instrumentation (full picture)

The audit found cost tracking is structurally insufficient across **all four**
agent LLM call sites — each accumulates a single `total_tokens` number; none
splits prompt/completion or knows any model's price, and there is no
cost-computation layer anywhere. A single `total_tokens` value is unpriceable
(output tokens cost ~3–4× input on GPT-4o; models differ per node). This track
builds a central cost layer and retrofits every call site, so the initial test
measures a complete per-query cost — not a partial one.

### Task table

| Task | Status | Description | Gate(s) |
|---|---|---|---|
| 23.5.8 | `[x]` | `agent/utils/llm_cost.py` — a model→pricing table `{model: (input_usd_per_1k, output_usd_per_1k)}` covering `gpt-4o`, `gpt-4o-mini`, `text-embedding-3-small` (env-overridable defaults from a real source, see table below; final reconciliation vs KG-PHASE-9.5-9 is a **pre-test gate, not a 23.5 task** — R7). Helper `compute_cost(model, prompt_tokens, completion_tokens) -> float`. Embedding calls have no completion tokens (pass 0). Unknown model → cost 0.0 + a logged warning (never raise). | Gate 1 |
| 23.5.9 | `[x]` | Extend `_extract_token_usage` (or add a sibling) at all four call sites to capture `prompt_tokens`, `completion_tokens`, and `model` — not just `total_tokens`. Sites: `query_understand.py`, `rate_evidence.py`, `synthesize.py`, `build_embedding.py`. | Gate 1 |
| 23.5.10 | `[x]` | At each of the four sites, compute `cost_usd` via `llm_cost.compute_cost(...)`, emit a structured usage log line (`model`, `prompt_tokens`, `completion_tokens`, `total_tokens`, `cost_usd`), and accumulate a new `state.total_cost_usd` (float) alongside the existing `total_tokens_used`. Add `total_cost_usd: float` to `ForecastState`. (**Absorbs T26.1; rescopes KG-B-6 from 2 nodes to all 4.**) | Gate 1, Gate 2 |
| 23.5.11 | `[x]` | Acceptance criteria added to the Sprint 24 and Sprint 25 plans (`docs/old_docs/agentic_hub_implementation_phase8_revised.md`): any new LLM-calling node (`answer_from_context`, `generate_suggested_actions`) **must** route token usage through `agent/utils/llm_cost.py` and contribute to `state.total_cost_usd` from its first commit — born instrumented, never retrofitted. | — |

### Authoritative price defaults (OpenAI, pulled 2026-06-20 — per R7)

`llm_cost.py` uses **per-1K-token** units; every value is env-overridable
(`LLM_COST_<MODEL>_INPUT_PER_1K` / `_OUTPUT_PER_1K`). Defaults:

| Model | input $/1K | output $/1K | notes |
|---|---|---|---|
| `gpt-4o` | 0.0025 | 0.0100 | **legacy/grandfathered rate.** Still served as of 2026-06-20, but snapshot `gpt-4o-2024-05-13` sunsets **2026-10-23** → migrate to **gpt-5.5** later. NO model migration in 23.5 (own validation); the env-overridable table makes the future swap a config change. |
| `gpt-4o-mini` | 0.00015 | 0.00060 | covers future followup / suggested_actions too |
| `text-embedding-3-small` | 0.00002 | — | embeddings have no output tokens |

These are the 3 distinct models across the 4 instrumented sites
(query_understanding=gpt-4o-mini, evidence_rating=gpt-4o-mini,
synthesis=gpt-4o, embedding=text-embedding-3-small). Source + date + the
legacy/sunset note are recorded next to the table in `llm_cost.py`.

> Note: Sprint 26 T26.4 (Prometheus `agent_llm_cost_usd_total` counter) now reads
> `state.total_cost_usd` / the usage log lines produced here. T26.4 stays in
> Sprint 26 but is unblocked by this track.

---

## §4 — Track 3: Version Hygiene

| Task | Status | Description | Gate(s) |
|---|---|---|---|
| 23.5.12 | `[x]` | Move `AGENT_VERSION` to its canonical home in `agent/config/settings.py` (today it is a hardcoded constant in `agent/nodes/synthesize.py:93`). Update `synthesize.py` and `health.py` to import from settings. Bump to `0.5.0-sprint23.5`. (**Absorbs the relocation half of T26.5.**) Sprint 26 then only adds the git short-hash on top (`AGENT_GIT_COMMIT_SHORT_SHA`), with a single source of version truth. | Gate 1 |

---

## §4.5 — Track 4: Test Baseline + Cleanup & the Suite-Green Gate

> **Added by the Advisor↔Ron decision (2026-06-20).** Tracks 1–3 were implemented
> and verified green on their own gate tests. But running the **full** agent suite
> for the first time (the Track 1–3 implementation run) surfaced **13 pre-existing
> test failures** in files this sprint never touched — latent breakage that
> accumulated because every prior sprint closed on *its own* gate tests, never the
> whole suite. None of the 13 is in the Known Gaps table. Track 4 closes that hole
> and installs the gate that prevents recurrence. **23.5 does not close until the
> suite is green (or every red is an explicit, documented skip).**

### §4.5.1 — Root cause (why this is a real gap, not noise)

No sprint ever ran the entire suite as a closeout condition, so a behavior change
in one sprint could break another sprint's tests invisibly. The clearest case:
**Sprint 22 / T22.7** added the `canonical_key` kwarg to
`update_session_status`, and `write_to_firestore` now passes it on every call —
but the **Sprint 21** gate test still asserts the call *without* it. That red sat
undetected through Sprints 23 and 23.5. A permanently-red suite trains everyone to
ignore reds, which is exactly how a future real regression would slip through.

> Honest scope note: a green suite proves *no regression in tested behavior*. It
> does **not** prove zero gaps — untested code paths remain invisible. The suite is
> the best regression detector available, not a completeness proof. Deeper
> per-component understanding is a separate follow-up (post-commit learning pass).

### §4.5.2 — The 13, triaged (prescribed disposition)

| # | Failures | Class | Root cause | Disposition |
|---|---|---|---|---|
| A | 7 × `test_process_query` | Unit-test isolation | `_build_initial_state → get_query_doc` reaches **real** Firestore because the `mocked_runner` fixture never patches it (fails on missing ADC) | **Fix the mock** — patch `get_query_doc` / `firestore_client` so the unit tests need **no** Firestore at all (self-contained; no emulator). Fallback: `usefixtures` emulator skip-guard only if the mock fix proves deeper than expected. |
| B | 2 × `test_sprint21_gate1` (`canonical_key`) | Genuine drift | Stale Sprint-21 assertion vs Sprint-22 T22.7 signature change | **Fix the assertion** to expect `canonical_key`. ~1-line each. |
| C | 1 × `test_schemas` (datetime) | Flaky | Asserts equality of two `datetime.now()` calls differing by microseconds | **Fix the assertion** to not depend on microsecond timing. |
| D | 3 × `test_tools_smoke` (×2) + `test_sprint22_gate2_subgraph` sentiment (×1) | Seed-data dependency | `db_available` guards on DB *reachability*, not on seed presence; DB up-but-unseeded → empty result → fail | **Load `tests/fixtures/sprint19_vault_seed.sql`** in the fixture/run, **or** strengthen the guard to skip when seed rows are absent. Prefer making the test load its own seed (deterministic). |

### §4.5.3 — Procedure (self-contained; runs while infra is brought up by the task itself)

1. **Bring up infra inside the task** — do not depend on a developer's machine
   state: `docker compose -f infrastructure/docker-compose.yml up -d kafka
   kafka-init postgres`, then load `tests/fixtures/sprint19_vault_seed.sql` into
   the test DB. Firestore emulator is **only** needed if Disposition A falls back
   to the emulator path; the mock fix removes that dependency.
2. **Capture the true baseline** — run the **full** agent suite once and record the
   real red count (it may be exactly these 13, or a couple more hidden behind the
   environmental ones). This number is the before/after anchor in the report.
3. **Apply the dispositions** in §4.5.2 (fix / fix / fix / seed) until the suite is
   green or every remaining red is an explicit `skip`/`xfail` with a reason string.
4. **Document** — any red that is deliberately deferred (not fixed) gets a new
   **KG-B-** entry in `hub_sprints.md` §4 with cause + reason; nothing is left
   untracked.
5. **Close the tracker** — update `task_plan.md` (active-sprint status + Known Gaps
   counts) so 23.5 reads **closed**, and the deferred-reds counts reconcile.
6. **STOP before any commit.** Produce the report (below) and hand back to Ron via
   the Advisor for verification. Commits are a separate, post-verification dispatch.

### §4.5.4 — Task table

| Task | Status | Description | Gate(s) |
|---|---|---|---|
| 23.5.13 | `[x]` | Stand up self-contained infra (docker kafka+postgres + load `sprint19_vault_seed.sql`); run the **full** agent suite; record the true baseline red count. **Baseline: 11 failed, 549 passed, 7 skipped** (13 pre-existing; 2 `tools_smoke` reds resolved purely by seeding). | Suite baseline |
| 23.5.14 | `[x]` | Disposition B + C — fix the 2 `canonical_key` stale assertions (Sprint-22 T22.7 drift) and the 1 `datetime` microsecond-flaky assertion. | Suite green |
| 23.5.15 | `[x]` | Disposition A — complete the `mocked_runner` mock so the 7 `test_process_query` unit tests patch `get_query_doc` (returns `None` → first-time-query path) and need no real Firestore. Mock fix was sufficient; no emulator fallback needed. | Suite green |
| 23.5.16 | `[x]` | Disposition D — `tools_smoke` (×2) now self-load the seed via a new `vault_seed_loaded` session fixture (idempotent; skips if unloadable). The `test_sprint22_gate2_subgraph` sentiment red was **mis-triaged**: real root cause was time-drift (fixed `NOW` not threaded into `write_to_firestore` sentiment bucketing → May fixtures aged out of the 14-day window), fixed by injecting `NOW` into the bucketing in the test. | Suite green |
| 23.5.17 | `[x]` | No deliberately-deferred red (suite fully green) → no new KG-B entry required; a Track 4 note + the closure of KG-B-6/-15/-16 recorded in `hub_sprints.md` §4; `task_plan.md` tracker + Known Gaps counts updated to mark 23.5 **closed**. | — |
| 23.5.18 | `[x]` | **Durable prevention** — updated the `sprint-closeout` skill: closeout now requires (a) the **full** suite green (not just the sprint's gate tests), and (b) every infra-dependent test carries a **skip-guard** so the suite is deterministic without live infra. | — |

### §4.5.5 — Deliverable (the verification basis for Ron's return)

A closeout report at **`cabinet-outputs/dispatcher/sprint-23.5-track4-baseline-2026-06-20.md`**
(house style of the existing 23.5 dispatcher report), containing: before/after
suite numbers; the per-failure triage table with each item's actual disposition;
the new KG-B entries; confirmation `task_plan.md` marks 23.5 closed; the
changed-files list; and an explicit statement that **no commits were made** plus
the working-tree status. The Advisor cross-checks every claim against the real
code/tests before Ron approves the commit dispatch.

---

## §5 — Gates

- **Gate 1 (unit):** `sufficiency_check` rubric (sufficient / insufficient /
  missing_dimensions derivation); `count_raw_signals` raw-package counting (R2);
  `_build_keywords` **entities-only** (R1 — missing_dimensions must NOT leak in);
  trigger node `run()` entry point; `llm_cost.compute_cost` (each model,
  embedding zero-completion, unknown-model-zero, env override); per-site usage
  capture into `total_cost_usd`; `AGENT_VERSION` import from settings.
- **Gate 2 (subgraph):** `vault_query → sufficiency_check → {rate_evidence |
  trigger_reactive_ingestion → rate_evidence}` routes correctly on both
  sufficient and insufficient paths; `reactive_triggers_emitted` gate respected;
  `total_cost_usd` accumulates across nodes.
- **Gate 3 (emulator/Kafka+Postgres):** insufficient-path run emits a real trigger,
  writes a `reactive_triggers_log` row (Linux CI / cloud — Windows-local `skipif`
  per KG-B-13).
- **E2E (23.5.7):** split gate — **Half A** (hard closeout: insufficient →
  trigger emitted + `reactive_triggers_log` row + synthesis proceeds, real
  Kafka+Postgres) and **Half B** (non-blocking PoC: stubbed NewsAPI fetch →
  1–2 canned articles land in Bronze). Both `skipif` Windows-local; verified on
  the §7 infra (`docker compose up -d kafka kafka-init postgres` + Firestore
  emulator on 8080).
- **Suite-green closeout gate (Track 4 — §4.5):** the **full** agent suite must be
  green, or every remaining red an explicit documented `skip`/`xfail`, before 23.5
  is marked closed. This becomes a standing closeout condition for all future
  sprints via the `sprint-closeout` skill update (23.5.18).

---

## §6 — Design Decisions

- **D1 — Wire in 23.5, not Sprint 26.** The sufficiency node, the interface fix,
  and the graph wiring are one change. Splitting them recreates the
  built-in-isolation gap that caused KG-B-15/-16. (Ron, 2026-06-18.)
- **D2 — Deterministic sufficiency rubric for V1 (confirmed at 23.5.2).**
  Zero added cost/latency before the initial test. `missing_dimensions` =
  the `structured_intent.entities` with no supporting evidence (per-entity
  coverage; `structured_intent` has no `dimensions` field). LLM-based
  sufficiency is a Future Enhancement if signal is poor.
- **D3 — Superseded by R1: V1 keywords are entities-only.** The original D3
  ("entities + missing_dimensions is canonical") was found to be a no-op
  (missing_dimensions dedup away against entities) and is replaced by R1:
  entities-only + the 7-day recency window. See §2/R1 for the full rationale.
- **D4 — Central cost layer, not per-node copies.** One pricing table, one
  `compute_cost`, fed by all call sites — so cost is consistent and the two future
  LLM nodes plug in without re-implementing.
- **D5 — Cost machinery in 23.5; price reconciliation is a pre-test gate
  (per R7).** The cost *machinery* (pricing table + `compute_cost` + 4-site
  retrofit + `state.total_cost_usd`) is built and tested in 23.5 with
  env-overridable defaults from a real source (§3). The exact USD numbers are
  not critical to closing 23.5; the **final reconciliation against the
  Gold-side KG-PHASE-9.5-9 figures is a named pre-test gate, not a 23.5 task**.
  One canonical per-model price set must serve both sides (gpt-4o is used on
  both). See §8.

### R5 — V1 reactive topology: single sufficiency_check, trigger-and-forget

The locked V1 graph topology (confirmed with Ron) is a **single**
`sufficiency_check` followed by trigger-and-forget — there is **no second
vault query (`vault_query_2`) and no refine loop** in 23.5:

```
vault_query → sufficiency_check
                ├─ sufficient?   → rate_evidence → synthesize → write_to_firestore → END
                └─ insufficient? → trigger_reactive_ingestion → rate_evidence → … (trigger-and-forget)
```

`_route_after_sufficiency` reads the latest `sufficiency_checks` verdict + the
per-session trigger budget (`reactive_triggers_emitted <
AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION`). The insufficient branch emits one
trigger and **continues to `rate_evidence` on whatever evidence is currently
available** — it does not wait for ingestion. If the budget is already spent,
it routes straight to `rate_evidence` (synthesis proceeds, low confidence).

The richer multi-attempt rubric in the `agent-design` skill (vault_query_2
keyed off `missing_dimensions`, a 2nd sufficiency check, then reactive_search)
is a deliberate **Future Enhancement** — the `attempt` field on each verdict is
recorded for forward-compatibility but is always 1 in the locked V1 topology.

---

## §7 — How this amends the revised plan (Sprints 24–27)

`docs/old_docs/agentic_hub_implementation_phase8_revised.md` is amended as follows
(the revised plan is not rewritten in place beyond the 23.5.4 text fix; this
section is the current-of-record delta):

- **Sprint 26 loses** T26.1 (→ Track 2), T26.5 relocation (→ Track 3), T26.7 +
  T26.10.5 (→ Track 1). **Sprint 26 retains** KG-B-8 cleanup, KG-B-5 latency
  analysis, Prometheus metrics (T26.4/T26.6), Postgres retry wrapping (T26.2). It
  becomes pure hardening + observability with no graph-topology changes.
- **Sprint 23 §** keyword description corrected to D4 (23.5.4).
- **Sprints 24 & 25** gain the cost-instrumentation acceptance criterion (23.5.11).
- **Pre-test path becomes:** 22 → 23 → **23.5** → 24 → 25 → 26 → initial test → 27.

---

## §8 — Cost-scope reconciliation with KG-PHASE-9.5-9

This sprint instruments the **per-query agent cost** (query-time): the four
agent LLM call sites + the two future ones. The **per-article Gold-enrichment
cost** (ingestion-time, Domain A) is the other half of total system cost and is
the scope of KG-PHASE-9.5-9 (Ron's parallel OpenAI cost analysis). For a complete
cost number the two halves must be reconciled and must use the **same per-model
price figures** — `agent/utils/llm_cost.py` (23.5.8) is the agent-side source of
those figures and should be cross-checked against 9.5-9 before the initial test.
The initial test's cost baseline = agent-side (this sprint) + Gold-side (9.5-9).
