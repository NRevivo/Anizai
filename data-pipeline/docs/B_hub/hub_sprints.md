# hub_sprints.md
> Domain: B — Agentic Hub
> Type: Sprints
> Last updated: 2026-07-26
> TL;DR: Sprint status for the whole hub (18–27), the cross-sprint Phase-8 rationale +
>        dependency map, deferred items, and the hub Known Gaps (KG-B-*). Each open
>        sprint's full task table lives in its own `plans/` file (linked from the §1
>        Plan-file column); §2 is reference-only. Open this to see what is done, what
>        is next, why the revision is shaped this way, and what was consciously left out.

## Navigation
- §1 — Status Summary — every sprint 18–27, closed or open, with a Plan-file link
- Rationale / Phase-8 Context — why this revision exists, the implementation order + dependency map, and the spec sections it supersedes
- §2 — Open Work — Sprints 24–27: status + blocker + pointer to the `plans/` file (reference-only; no task tables)
- §3 — Deferred Items — what was consciously dropped and the condition to revisit
- §4 — Known Gaps — KG-B-* table with priority and condition to address

---

## §1 — Status Summary

| Sprint | Status | Closed date | Key outcome | Plan file |
|---|---|---|---|---|
| 18 — Foundation (8A) | Closed | 2026-04-29 | Worker pattern + Firestore plumbing; stub SessionResult round-trip. → `hub_archive.md` | — |
| 19 — Vault retrieval (8B) | Closed | 2026-05-01 | LangGraph skeleton + 3 retrieval agents; placeholder synthesis. → `hub_archive.md` | — |
| 20 — Synthesis + Firestore (8B) | Closed | 2026-05-04 | Real GPT-4o synthesis + rate_evidence + write_to_firestore; Tier 1 live. → `hub_archive.md` | — |
| 21 — Tier 2 + clarification (8C) | Closed | 2026-05-05 | Ambiguity detection, clarification branch, Tier 2 freeform. → `hub_archive.md` | — |
| 21.5 — NewsAPI provider migration | Closed | 2026-05-06 | Out-of-band pipeline maintenance; **Domain A scope**, not hub. → `task_plan.md` | — |
| 22 — Foundation Fixes (Revised) | Closed | 2026-05-26 | pg_trgm Polymarket match; all 5 BI cards real on Tier 1. → `hub_archive.md` | — |
| 23 — Producer-trigger Infrastructure | Closed | 2026-05-26 | NewsAPI `run_reactive()` + Kafka registration + `reactive_triggers_log` + trigger node (isolated). → `hub_archive.md` | — |
| 23.5 — Pre-26 Remediation | **Closed** | 2026-06-20 | Built the missing `sufficiency_check` node, wired the reactive path into the graph, fixed the trigger `.run` interface, reconciled the `raw_question` drift, built the central LLM cost layer, relocated `AGENT_VERSION`. **Track 4** stood up self-contained infra (Kafka+Postgres+seed), ran the full agent suite for a true baseline, and cleared all 13 pre-existing reds → suite green (560 passed, 7 documented emulator skips). Closes KG-B-6/-15/-16. Absorbs Sprint 26 T26.1/T26.5/T26.7. → `hub_archive.md` | `archive_plans/sprint23_5_pre26_remediation.md` |
| 24 — Follow-up Conversations (Revised) | **Closed** | 2026-07-04 | Answer-from-context follow-up subgraph (`agent/followup/`) + 2nd Firestore collection-group listener + done-transition sweep; atomic `sent→answered` claim; born-instrumented cost. Gate 1/2/3 green + real gpt-4o-mini E2E PASS. Opened KG-B-17/-18/-19. | `plans/sprint24_followups.md` |
| 25 — Suggested Actions + agentEvents | **Closed** | 2026-07-16 | `generate_suggested_actions` node (3 contextual actions, born-instrumented, degrade→`failed`) + non-blocking `agentEvents` emitter (per-`runId` guarded registry, `@events.emits` decorator, `run_id`/`currentRunId`, §3-D drain-before-`done`); MAIN graph only — follow-ups emit NONE. Gate 1/2/3 green + real-OpenAI E2E (mid-run delivery proven). Full-infra suite: 634 passed / 3 KG-B-13 skips / 1 KG-B-18 xfail. Closes KG-B-19. → `hub_archive.md` | `archive_plans/sprint25_suggested_actions.md` |
| 26 — Pre-Test Hardening | **Closed** | 2026-07-18 | Clarification strip (KG-B-8); vault-read retry (tight 3/0.5/2.0, fits vault_query's 15s); git-sha `agentVersion` **(Domain-C injection of `AGENT_GIT_COMMIT_SHORT_SHA` now LIVE in deployed `anizai-agent:0.5.0-sprint26`+55e8093, 2026-07-23 — T26.5 Domain-C dependency satisfied)**; delivery-path retarget closing **KG-B-18** (step 6 → `query_doc_id` + `_mark_failed` no-downgrade guard); 3 Prometheus metrics (real `/metrics`, stub gone); per-node latency report (`synthesize`+`rate_evidence` dominate ~85% of ~35s, within ≤60s p95). Gate 1/2/3 green + 2 real-OpenAI latency forecasts. Full agent suite 637 passed / 15 documented infra-skips / 0 xfail. Closes KG-B-8/-18; opened KG-B-20. → `hub_archive.md` | `archive_plans/sprint26_pretest_hardening.md` |
| Initial test (~2 days, cloud) | Pending | — | Gated on Sprint 26; baseline real cost + forecast quality on live vault data. **Stage-1 deploy prerequisites satisfied 2026-07-23** (agent image `0.5.0-sprint26` on cloud at `replicas:0`; `reactive_triggers_log` present; partner Firestore index+rules deployed); remaining before the run: **Stage 2** — `anizai-airflow` rebuild + scale agent to 1. | — |
| 27 — Post-Test Polish + Closeout | **Not planned** (partial/stub) | — | Deferred Sprint-26 hardening + Phase 8 State Ledger; tasks added post-test | `plans/sprint27_post_test_closeout.md` |

**Pre-test path:** 22 → 23 → ~~23.5~~ → ~~24~~ → ~~25~~ → ~~26~~ (closed 2026-07-18) → **initial test (NEXT)** → 27.
**Active plans:** `plans/sprint27_post_test_closeout.md` (partial/stub — gated on the initial test); closed & archived: `archive_plans/sprint26_pretest_hardening.md` (2026-07-18, all `[x]`), `archive_plans/sprint25_suggested_actions.md` (2026-07-16, all `[x]`), `archive_plans/sprint24_followups.md` (2026-07-04, all `[x]`), `archive_plans/sprint23_5_pre26_remediation.md` (superseded Sprint 26 T26.1/T26.5/T26.7).

---

## Rationale / Phase-8 Context

The cross-sprint "why + order" for the Phase-8 revision, lifted out of the (now
superseded) `agentic_hub_implementation_phase8_revised.md`. The per-sprint task
detail lives in each `plans/` file; this section is the connective tissue.

### Why this revision exists

Three drivers converged in late May 2026 to require a re-plan of Sprints 22–26:

1. **Cost concerns surfaced by Phase 9.5.** KG-PHASE-9.5-9 (the parallel OpenAI
   cost-analysis session) showed the OpenAI usage model needs re-baselining before
   adding new paid surfaces. The original Sprint 22–23 plan was a fresh
   reactive-search microservice on Tavily/Brave APIs — not justifiable until the
   cost picture clears.
2. **NewsAPI provider upgrade.** Phase 7A migrated NewsAPI to newsapi.ai with full
   article body (`articleBodyLen=-1`). The vault now ingests full text, covering a
   meaningful portion of what reactive search was designed to fetch — making
   reactive ingestion via the existing producer a viable alternative to external
   search APIs.
3. **The initial-test phase is the immediate goal.** The near-term target is a
   two-day live cloud run where producers operate end-to-end and the agent runs
   real forecasts, primarily to baseline real production costs and see real
   forecast quality on real (not seed) vault data. Everything is prioritized
   against "is this needed to make initial testing meaningful, or can it ship
   later?"

The revision added two new sprints (22 Revised + 23) and reordered the rest to
converge on a hardened-enough agent for the two-day initial test, then a focused
polish/closeout sprint (27) afterward. The 2026-06-18 audit then inserted Sprint
23.5 (remediation) to close cross-sprint-seam defects before Sprint 24.

### Implementation order + dependency map

**Pre-test path:** 22 → 23 → 23.5 (closed) → ~~24~~ (closed 2026-07-04) → **25 (NEXT)** → 26 → initial
test → 27.

```
Sprint 22 (Foundation Fixes)  ─┐
Sprint 23 (Producer-trigger)  ─┤  22 & 23 were parallel-able
                               ▼
              Sprint 23.5 (Pre-26 Remediation) — closed 2026-06-20
                               ▼
              Sprint 24 (Follow-ups) — closed 2026-07-04
                               ▼
              Sprint 25 (Suggested + Events, NEXT)
                               ▼
              Sprint 26 (Pre-Test Hardening — pure hardening)
                               ▼
                        INITIAL TEST (~2 days, cloud)
                               ▼
              Sprint 27 (Post-Test Polish + Phase 8 Closeout)
```

**Hard dependencies:**
- **Sprint 22 blocked 24, 25, 26** — they consume the SessionResult shape Sprint 22
  finalized (marketProbability wired, predictionSeries/sentimentTimeSeries
  populated, fuzzy-matched market data threaded through `synthesize`). *(Satisfied —
  22 closed.)*
- **Sprint 23 blocked wiring `trigger_reactive_ingestion` into the graph** — it did
  NOT block Sprints 24 or 25 themselves. *(That wiring, plus the missing
  `sufficiency_check` node, moved into Sprint 23.5 — now closed.)*
- **Sprint 24 blocked Sprint 25.** *(Satisfied — 24 closed 2026-07-04.)* Note the
  2026-07-04 revision **inverted** the original coupling: T25.7 now verifies the
  follow-up graph emits **no** events (follow-ups get a status-based "thinking"
  indicator instead of a reasoning panel) — see `plans/sprint25_suggested_actions.md`.

**Soft dependency:** Sprints 22 and 23 could run in parallel by different
sessions/contributors.

### Spec sections affected

These `agentic_hub_spec.md` sections are partially superseded by the revision (the
spec was not edited in place):

| Spec Section | Status | Successor |
|---|---|---|
| §8.3.2 Graph Topology — `sufficiency_check → reactive_search` branch | Superseded for V1 | `sufficiency_check → trigger_reactive_ingestion` (trigger-and-forget), built + wired in Sprint 23.5 |
| §8.3.3 `sufficient?` routing | Partially superseded | "reactive_search" target replaced by "trigger_reactive_ingestion (fire-and-forget)" |
| §8.5.3 Reactive ingestion loop note | Reversed | The spec said the Kafka loop is "replaced by the inline reactive search microservice"; the revision restores the Kafka loop (Sprint 23) as the V1 path |
| §8.8.3 Follow-up escalation path | Deferred | Sprint 24 implements `answer_from_context` only; escalation → Future Enhancements (FE 2, §3) |
| §8.12 Reactive Search Microservice (entire section) | Deferred indefinitely | Replaced by the Sprint 23 producer-trigger approach; spec section retained as design reference for FE 1 (§3) |

---

## §2 — Open Work

Reference-only: each open sprint's status + blocker + a pointer to its full plan
file. **The task tables live in the `plans/` files** (linked from the §1 Plan-file
column), not here. Closed Sprint 23.5 is recorded in `hub_archive.md` + its moved
plan `archive_plans/sprint23_5_pre26_remediation.md`.

- **Sprint 24 — Follow-up Conversations (Revised) — CLOSED 2026-07-04.** See §1 for
  the closeout summary; plan retained at `plans/sprint24_followups.md` (all `[x]`).
  Opened KG-B-17/-18/-19.
- **Sprint 25 — Suggested Actions + Chain-of-Thought Events — CLOSED 2026-07-16.** 3
  suggested actions per forecast + a non-blocking `agentEvents` stream on the **main
  graph only** (follow-ups emit none). See §1; plan archived at
  `archive_plans/sprint25_suggested_actions.md` (all `[x]`). Closed KG-B-19.
- **Sprint 26 — Pre-Test Hardening — CLOSED 2026-07-18.** Pure hardening + observability
  (clarification strip, vault-read retry, git-sha `agentVersion`, 3 Prometheus metrics +
  real `/metrics`, delivery-path retarget closing **KG-B-18**, per-node latency report).
  See §1 for the closeout summary; plan archived at
  `archive_plans/sprint26_pretest_hardening.md` (all `[x]`). Closed KG-B-8 + KG-B-18;
  opened KG-B-20. Latency analysis (26.3) → `sprint26_latency_report.md`.
- **Sprint 27 — Post-Test Polish + Closeout — Not planned (partial/stub).** Deferred
  Sprint-26 hardening + Phase 8 State Ledger; tasks 27.1–27.11 known today, 27.12+
  defined post-test. **Blockers:** the initial test (gated on Sprint 26). **Plan:**
  `plans/sprint27_post_test_closeout.md`.

---

## §3 — Deferred Items

The canonical Domain-B deferred list. It absorbs all eight Phase-8 "Future
Enhancements" (FE 1–8) from the superseded revised plan — the `(FE n)` tags in the
last column map each row to its Future-Enhancement number; nothing unique remains in
the phase8 file.

| Item | Deferred from | Reason | Condition to revisit |
|---|---|---|---|
| Reactive Search Microservice (Tavily/Brave, allowlist, `reactive_article_cache`, snippet extractor — spec §8.12) | Original Sprints 22-23 | OpenAI cost concerns (KG-PHASE-9.5-9) + NewsAPI full-body migration covers most of the need | After cost picture stabilizes AND producer-trigger coverage proves insufficient (FE 1) |
| Follow-up escalation (fetch fresh evidence on insufficient context) | Sprint 24 | Duplicates Sprint 23's trigger work in a second context; ship answer-from-context first | When test users regularly hit insufficient-context follow-ups (FE 2) |
| Cross-user cache + delta refresh (spec §8.7.3 / §8.7.4) | Phase 8 V1 | 3-4 test users / 2 days → low repeat-question rate; cache would obscure real cost numbers | After initial test produces enough sessions and cost analysis confirms synthesize dominates cost (FE 3). Pre-emptive `canonicalKey`-on-session-doc shipped in Sprint 22.7. **Evidence 2026-07-26 (agent-only cloud run, n=7):** the cost half of the condition is now **met** — gpt-4o is ~93% of spend ($0.1706 of $0.183), i.e. `synthesize` dominates, at ≈$0.026/forecast. The session-count half is not: 7 sessions over 20h is far too few to observe any repeat-question rate. Still deferred, now on one criterion instead of two |
| Polymarket vector index + multi-match clarification | Sprint 22 | pg_trgm catches verbatim/near-verbatim at 20% of the cost; semantic match is more complex | When test data shows users frequently paraphrase Polymarket questions that pg_trgm misses (measured ~0.49 on legit rewrites) (FE 4). **Evidence 2026-07-26 (agent-only cloud run):** **0 of 7** real frontend forecasts routed `tier_1` — the literal-match path never fired across a 20h window, leaving the whole Tier-1 branch (`marketProbability`, market data threaded into `synthesize`) unverified in cloud. Two readings are still open: the questions asked were simply not market-shaped, or the match is too narrow. n=7 is thin, but 0/7 is a signal. **Next agent session must include at least one deliberately market-shaped question** to separate them — until then this row cannot be advanced or dismissed |
| Sentiment time-series quality (sample-floor, larger window, dedicated enrichment, confidence bands) | Sprint 22 | Bucketing on already-computed scores ships zero-cost; may be noisy | When test data shows the sentiment lines are consistently empty or visibly noisy (FE 5) |
| Additional reactive trigger sources (Telegram / ArXiv / Polymarket) | Sprint 23 | NewsAPI alone validates the architecture; others are mechanical per-source follow-ups | When test shows NewsAPI-only coverage is thin for some domains (FE 6) |
| Performance optimization | Sprint 26 (analysis only) | Optimization is expensive/uncertain; only do it if analysis finds real regressions | If KG-B-5 analysis flags real issues — may need to land before Phase 10's 100+-forecast load (FE 7) |
| Public sentiment line — source quality (the Public line is `social_vectors.sentiment_score` bucketed, which is sparse; possible upgrades: Twitter/X signals, broader HackerNews coverage, dedicated public-sentiment scraping) | Sprint 22 | `social_vectors` is sparse (few signals per question typically); real-world quality of the Public line determines whether additional public-sentiment sources are worth the investment | When initial-test data shows the Public sentiment line is empty on too many forecasts (FE 8) |
| Dedicated pre-LLM guardrail/filter layer for follow-up messages (a filtering step that screens user follow-ups *before* they reach `answer_from_context`) | Sprint 24 (decision 2026-07-04, Advisor↔Ron) | V1 handles out-of-scope / forbidden-action / abusive follow-ups **inside the follow-up system prompt** (guardrail-lite, T24.7): the same single GPT-4o-mini call classifies and returns a fixed polite redirect, logged — zero extra cost/latency. A separate pre-LLM filter is over-engineering for 3–4 known testers over 2 days | After the initial test / when real external users arrive — revisit if test logs show the prompt-level classification being bypassed, misclassifying, or absorbing real abuse |

---

## §4 — Known Gaps

Hub-scoped gaps, renumbered KG-B-*; the `Origin` column maps to the original
`task_plan.md` IDs. Closed hub gaps (KG-PHASE8-12, -18, -21, -22) are recorded in
`hub_archive.md`. **KG-B-6, KG-B-15, KG-B-16 were CLOSED by Sprint 23.5
(2026-06-20)** — retained in the table below with a ✅ marker for one cycle, then
moved to `hub_archive.md` at the next hub-doc sweep. **Open count: 15** (Sprint 26 closed KG-B-8 + KG-B-18; KG-B-20 opened 2026-07-18 was already in the prior 16; **KG-B-21 opened 2026-07-26** from the agent-only cloud run).

> **Sprint 23.5 Track 4 note (2026-06-20):** the first true full-suite baseline run
> surfaced 13 pre-existing reds in files this sprint never touched (latent
> cross-sprint breakage — no prior sprint ran the whole suite as a closeout
> condition). All 13 were cleared under the prescribed dispositions (mock fixes,
> stale-assertion fixes, a datetime-flaky fix, and self-loading the vault seed);
> **zero were deferred**, so no new KG-B entry was opened. The standing
> full-suite-green closeout gate + infra-test skip-guard rule now live in the
> `sprint-closeout` skill to prevent recurrence. Full triage:
> `cabinet-outputs/dispatcher/sprint-23.5-track4-baseline-2026-06-20.md`.

| ID | Origin | Description | Priority | Condition to address |
|---|---|---|---|---|
| KG-B-1 | KG-PHASE8-11 | Polymarket `key_arguments_pro/con` return `[]` — Gold job writes undifferentiated `key_findings` | Low | Needs consensus-prompt + gold_job change; Phase 7 or post-Sprint-26 |
| KG-B-2 | KG-PHASE8-13 | `sessionResults` written top-level vs. spec patch §8.7.1 implied subcollection | Low | Reconcile spec ↔ server post-V1 (server reads top-level today) |
| KG-B-3 | KG-PHASE8-6 | Result timestamp field-name drift (`generatedAt` vs `createdAt`/`updatedAt`) | Low | Mitigated by writing all three; reconcile post-V1 |
| KG-B-4 | KG-PHASE8-7 | Worker uses `logging.basicConfig()` not `setup_logging()`. **RESCOPED 2026-07-26 (cloud evidence).** The real symptom is not the format — it is that the cloud agent emits ~1% of its INFO. `setup_logging()` installs `_SampledInfoFilter` (INFO at `LOG_INFO_SAMPLE_RATE`, default 0.01) and is idempotent/first-caller-wins; an import-time call via `trigger_reactive_ingestion.py` → `graph.py` already configures the process before `worker.py` runs, so `basicConfig()` is a no-op and the sampler is live. The ~20h agent-only run of 2026-07-25/26 produced **7 Cloud Logging entries total** and no reliable per-forecast `llm_usage` lines; cost/latency were recoverable only because Sprint 26 (26.4) had landed Prometheus metrics. Note the 1% policy is correct for the pipeline (~100 msg/s) and wrong for the agent (single-digit forecasts/hour). | **Medium** (raised from Low — it silently removes the agent's cost/latency audit trail) | Sprint 27 (27.1), whose wording was revised 2026-07-26: closing this requires exempting the agent from INFO sampling (`LOG_INFO_SAMPLE_RATE=1.0` on the Deployment, or an agent-side bypass), not merely swapping `basicConfig()` for `setup_logging()` — the swap alone formalizes the sampling. Interim lever for any measurement session: set the env var on `agent-worker` before bring-up (fresh pod required; read at module import). |
| KG-B-5 | KG-PHASE8-16 | Forecast latency vs NFR. **NFR relaxed to ≤60s p95 (product decision, Advisor↔Ron 2026-07-04; the historical 30s target is retired — 30–60s wait on the main forecast is acceptable-to-desirable UX, reinforced by the Sprint-25 reasoning panel).** Current measurements (36.3s cold / 32.2s warm / 47s broad) are all WITHIN the relaxed NFR — but the 47s broad case is close to the ceiling, and Sprint-25 events + cloud/network variance could push it over. The follow-up ≤7s budget (KG-B-17) is a separate, deliberately strict target — chat context, NOT relaxed. Sprint 27 T27.10's `p95 < 30s` assertion must be updated to `< 60s` when Sprint 27 is planned. | Low | ✅ **26.3 analysis SHIPPED 2026-07-18** → `docs/B_hub/sprint26_latency_report.md`: `synthesize` (~16–19s) + `rate_evidence` (~12–14s) dominate ~85% of ~35s; both measured scenarios within ≤60s p95; no O(1)-regression candidates. Measured via a Postgres-free driver (emulator + real OpenAI + realistic mocked agents) — `vault_query` is a mock floor, so the authoritative real-vault + cold/warm p95 is the cloud baseline day-run. Per-node analysis feeds Phase 10 load planning regardless of the NFR. Re-escalate if initial-test measurements cross 60s p95, or at Phase 10 load (100+ forecasts) where latency becomes throughput+cost. | 
| KG-B-6 | KG-PHASE8-17 | **(Rescoped 2026-06-18)** Cost tracking is structurally insufficient across **all 4** agent LLM call sites (`query_understand`, `rate_evidence`, `synthesize`, `build_embedding`) — each accumulates only `total_tokens`; none splits prompt/completion or computes `cost_usd`, and no central pricing layer exists. A single `total_tokens` value is unpriceable (output ~3–4× input; models differ). | **High** | ✅ **CLOSED 2026-06-20 (Sprint 23.5 Track 2)** — central `agent/utils/llm_cost.py` + 4-site retrofit + `state.total_cost_usd` landed and green. Price reconciliation vs KG-PHASE-9.5-9 remains a named pre-test gate (D5). |
| KG-B-7 | KG-PHASE8-15 | No schema validation on inbound `forecastQueries`; `KeyError` not typed `MalformedQueryError` | Medium | Sprint 27 (T27.2), with the unified error handler |
| KG-B-8 | KG-PHASE8-20 | `clarificationCandidates` carries 5 hub-internal fields alongside the 5 spec-contracted ones | Medium | ✅ **CLOSED 2026-07-18 (Sprint 26, 26.2):** stripped the **4** dead hub-internal fields (`intent`/`domain`/`entities`/`polymarket_search_terms` — the "5 fields incl. polymarket_slug" wording was wrong; there is no polymarket_slug write); only the 5 spec fields are written. Verified Gate 1 + Gate 2 (strip survives subgraph→Firestore). Retained one cycle per convention. |
| KG-B-9 | KG-PHASE8-19 | Frontend renders demo data, not real Firestore session reads | Medium | Partner-side (Friend 1 / Friend 2); hub write contract verified |
| KG-B-10 | KG-PHASE8-23 | Dead config `AGENT_REACTIVE_MAX_PER_SESSION` (original microservice; zero consumers) | Low | Remove when FE 1 is implemented or formally retired |
| KG-B-11 | KG-PHASE8-14 | Four legacy `forecastQueries` docs using `query` instead of `question`; swept to `failed` | Low | Friend 2 conversation; source unclear |
| KG-B-12 | KG-PHASE8-10 | Firebase emulator UI doesn't display Admin-SDK-written docs | Low | Cosmetic dev-loop nuisance; query via REST |
| KG-B-13 | KG-PHASE8-25 | kafka-python-ng selector race on bootstrap→coordinator handoff blocks Windows-local Gate 3/E2E of the trigger | Low | Production Linux/GKE unaffected; Gate 3 runs on Linux CI. Monitor upstream / consider confluent-kafka |
| KG-B-14 | KG-PHASE8-1/-2/-4 | Cosmetic spec-polish items (cross-ref verification, §8.7 header rename, §8.3.1 `(added)` marker) | Low | Post-V1 doc cleanup |
| KG-B-15 | (new — 2026-06-15) | `state.sufficiency_checks` is never written by any built node — the graph runs a single `vault_query` with no sufficiency check. The field exists and `trigger_reactive_ingestion` reads `sufficiency_checks[-1].missing_dimensions`, but that half of keyword construction is always empty. Root cause: the 2026-05-23 re-plan repurposed Sprint 22 (which originally carried the sufficiency node) and never re-scheduled it; Sprint 26 T26.7 inherited the assumption it existed. | **High** | ✅ **CLOSED 2026-06-20 (Sprint 23.5 Track 1)** — `sufficiency_check` node built (23.5.1) and wired after `vault_query` with `_route_after_sufficiency` (23.5.5); green. |
| KG-B-16 | (new — 2026-06-18) | Trigger node interface mismatch: every graph-wired node module exposes `def run(state)` and `graph.py` wires via `module.run`, but `trigger_reactive_ingestion.py` exposes `def trigger_reactive_ingestion(state)` with no `run`. Wiring it via the established pattern would `AttributeError`. Never hit because the node has never been instantiated inside the graph (built-in-isolation fingerprint). | Medium | ✅ **CLOSED 2026-06-20 (Sprint 23.5 Track 1)** — trigger entry point renamed to `run(state)` (23.5.3) and wired into the graph; green. |
| KG-B-17 | (new — 2026-07-04) | **Follow-up answer-node budget (`AGENT_FOLLOWUP_BUDGET_MS=6000`) validated only on a small LOCAL sample.** Sprint 24 E2E (n=5; local emulator + real gpt-4o-mini; seeded data): latency min 1667 / median 2514 / **max 4109** ms, 0/5 budget-timeouts → 6000 ms held, no preemptive bump (no evidence of slow calls, and with no streaming a larger budget only adds dead-air). But this is **not** the production distribution — cloud + real vault + varied questions + OpenAI network variance + cold starts / longer contexts can push the tail past 6000 ms, at which point a follow-up returns the timeout **caveat** (graceful degradation, but worse UX — the user must re-ask). Suspected at Sprint-24 close and consciously NOT bumped. | Medium | **Watch during the initial cloud test.** If timeout-caveats appear on real follow-ups (check the `llm_usage` logs / caveat count), raise `AGENT_FOLLOWUP_BUDGET_MS` to ~10–12 s **and** shift the ≤7 s end-to-end target + the Gate 3 (24.12) assertion by the same delta (they are coupled — see `plans/sprint24_followups.md` §3 budget bullet). Sibling to KG-B-5 (main-forecast latency). Streaming (deferred) would largely dissolve this pressure. |
| KG-B-18 | (new — 2026-07-04) | `test_sprint21_gate3_tier2_resume_freeform_completes_forecast` fails on the emulator with `404 no entity to update: forecastQueries/{…_tier2_resume}`. `write_to_firestore` step 6 updates the original session's queue doc, which this Tier-2-resume test never seeds — a pre-existing **Sprint-21** limitation documented in the test's own docstring. Latent in the 23.5 green baseline because the emulator was down then (among the 7 documented emulator skips); Sprint 24's Gate 3 requires the emulator up, so the test now executes and surfaces the bug. **Proven not Sprint 24** (stash-reverting the six Sprint-24 tracked changes → identical failure; zero Sprint-24 files touched). | Low | ✅ **CLOSED 2026-07-18 (Sprint 26 — 26.11 retarget + 26.10 Gate 3):** `test_sprint21_gate3_tier2_resume_freeform_completes_forecast` is green against the emulator (xfail removed, final assertion flipped `claimed`→`done`); verified end-to-end — the fresh queue doc → `done`, the original clarified doc stays `awaiting_clarification`, no 404. Backstops retained: `_mark_failed` no-downgrade guard + step-6 Layer-1 try/except. Retained one cycle per the KG-B-6/-15/-16 convention. Original prescription (history): Fix in **Sprint 26** (26.11). Root cause is step 6 marking the WRONG doc — the fix carries `query_doc_id` through state and points step 6 at the processed doc, so the fresh resume doc is the one marked `done` (retarget; NOT "seed the original" / "tolerate missing"). `_mark_failed` no-downgrade guard retained as backstop. Interim: `xfail` marker so the closeout suite reads clean; removed on close (final assertion flips `claimed`→`done`). |
| KG-B-19 | (new — 2026-07-04) | `TestTotalCostAccumulation::test_total_cost_usd_accumulates_across_nodes` fails with LangGraph `InvalidUpdateError` on a self-contained 2-node `StateGraph(ForecastState)` + empty `graph.invoke({})` where both nodes write `total_cost_usd`. **Proven not Sprint 24** (stash-revert → identical failure; imports only `agent/state.py` + langgraph, both unmodified by 24). **NOT a langgraph version change** — `langgraph==0.2.39` is pinned exact in `requirements.txt`. **Root-cause theory REVISED 2026-07-14 (advisor pre-handoff review, finding #1):** the original `langchain-core`-drift theory is very likely WRONG. The two stub nodes are wired SEQUENTIALLY (`START → a → b`), and merge-time `InvalidUpdateError` is a *concurrent-writes-in-one-superstep* error — not a sequential-write error; the production graph already has 4 nodes sequentially writing the same shared scalars (`llm_calls_count` / `total_tokens_used` / `total_cost_usd`) and is green in graph-integration + the real Sprint-24 E2E. The distinguishing feature of the failing test is the **empty `graph.invoke({})`**, not the shared-field write. This is an artificial-graph test artifact; the real cost-accumulation path is unaffected. | Medium | ✅ **CLOSED 2026-07-15 (Sprint 25 T25.0) — fork (2), TEST DEFECT:** re-run with a non-empty input (`graph.invoke({"session_id": "x"})`) → green. The artificial empty `graph.invoke({})` was the sole cause; test input fixed + docstring added, `requirements*.{txt,lock}` untouched (no version pin). Confirms the finding-#1 theory (sequential shared-scalar writes were never the problem). Retained one cycle per the KG-B-6/-15/-16 convention. Original triage prescription follows for history: **Sprint 25 T25.0 — triage with a fork (~20 min).** (1) Re-run the test with a minimal NON-empty input (e.g. `graph.invoke({"session_id": "x"})`). (2) **Green → it is a TEST DEFECT**: fix the test input, close KG-B-19 as such, and change **nothing** in `requirements*.{txt,lock}` (a repo-wide dependency pin driven by a wrong root cause would blast-radius into Domain A + the GKE images). (3) **Still red → only then** audit the *installed* `langgraph` / `langchain-core` versions vs `requirements.lock` (`langchain-core==0.3.84`, `langgraph==0.2.39`) and pin exact per the KG-PHASE8-5 closure pattern. Interim (either fork): xfail/skip-guard so the closeout suite reads clean. |
| KG-B-20 | (new — 2026-07-18) | **forecastQueries hygiene (pre-existing, surfaced by the 26.11 analysis).** On resume-on-clarify the ORIGINAL queue doc (id == sessionId) is left at `awaiting_clarification` indefinitely (after 26.11's retarget, step 6 marks the fresh doc `done` instead of the original), and `deleteSession` (server, `session.repository.ts`) deletes only `forecastQueries/{sessionId}` — the fresh-UUID resume docs are never deleted. No correctness/UX impact: the worker scans only `status=='pending'`, and nothing in the hub or server reads the queue by status except the worker's listener. Pure litter in an internal collection. | Low | Post-test hygiene / when queue accumulation becomes operationally noticeable. Server-side cleanup (mark the original `superseded` in `requeueClarifiedSession` and/or delete resume docs in `deleteSession`) is the partner's domain — a coordinated item, not a hub sprint. |
| KG-B-21 | (new — 2026-07-26) | **No reaper for `forecastQueries` orphaned at `claimed`.** The worker's main listener queries `status=='pending'` only, so a document claimed by a worker that then dies mid-forecast is never revisited by anything. Two `e2e-sprint21-resume-*` docs have sat at `claimed` since 2026-05-05 — ~3 months — and did not self-clear during the 2026-07-25/26 cloud run. Config `AGENT_CLAIM_TIMEOUT_SECONDS` exists, but no consumer enforcing it has been identified; if there is none, re-claim on worker death is **unbuilt**, not broken. User-visible consequence: a forecast interrupted by a crash or an eviction is lost silently — the query never completes and never fails, so the frontend shows neither a result nor an error. Distinct from KG-B-20, which is litter at `awaiting_clarification` with no correctness impact; this one is a recovery gap. | Medium | Sprint 27 **27.6**, whose wording was amended 2026-07-26 to audit for the enforcing consumer *before* writing the restart-resilience test — if absent, 27.6 becomes a build (periodic sweep of `claimed` past the timeout back to `pending`), not a test. Also reset the two known orphans when convenient. Evidence: `docs/B_hub/agent_cloud_run_20260726.md` §3 finding 3. |
