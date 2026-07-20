# hub_archive.md
> Domain: B — Agentic Hub
> Type: Archive
> Last updated: 2026-07-19
> TL;DR: Append-only record of the closed hub sprints (18–26) — outcome, key decisions, tasks completed, gaps raised — plus a one-line summary of the 15 spec patches. Open this for the "why" behind a closed decision.

> **Append-only.** Add new closed sprints below; do not rewrite existing entries.
> The authoritative full records (per-task tables, full State Ledgers, design
> decision logs) live in `task_plan_archive.md` — the forensic breadcrumb at the
> `data-pipeline/` root. This file is the Domain B digest. New-style closed **plan
> files** (one self-contained file per retired sprint — e.g. the Sprint 23.5
> remediation) live under `archive_plans/`; this archive carries the digest entry
> and points to them.

## Archive Index

| Sprint | Closed | Key decisions summary |
|---|---|---|
| 18 — Foundation (8A) | 2026-04-29 | Worker pattern over FastAPI; single worker; hub config separate from pipeline; full §8.7.2 stub contract |
| 19 — Vault retrieval (8B) | 2026-05-01 | Audit-gated start; structured-output JSON; parallel agent dispatch; top-3 candidates persisted; langchain pinned |
| 20 — Synthesis + Firestore (8B) | 2026-05-04 | GPT-4o synthesis + GPT-4o-mini rate_evidence; top-level sessionResults; 500-doc batches; synthesis owns influence fields |
| 21 — Tier 2 + clarification (8C) | 2026-05-05 | Margin threshold 0.10; tier inferred in synthesize; resume via sessionId≠query_doc_id; ≥2-candidate guard |
| 22 — Foundation Fixes (Revised, 8D) | 2026-05-26 | pg_trgm 0.85 fuzzy match; all 5 BI cards wired; sentiment bucketing; canonicalKey on session doc |
| 23 — Producer-trigger Infrastructure | 2026-05-26 | NewsAPI `run_reactive()`; one-call-per-keyword; trigger-and-forget; producer singleton; node built in isolation |
| 23.5 — Pre-26 Remediation | 2026-06-20 | Built the missing `sufficiency_check` node + wired the reactive path; trigger `.run` interface fix; central LLM cost layer; `AGENT_VERSION` relocation; full-suite-green closeout gate. Closes KG-B-6/-15/-16. Plan: `archive_plans/sprint23_5_pre26_remediation.md` |
| 24 — Follow-up Conversations (Revised) | 2026-07-04 | 2nd Firestore collection-group listener on `messages` + separate `agent/followup/` answer-from-context subgraph + done-transition sweep; atomic `sent→answered` claim; born-instrumented; no escalation (FE 2). Opened KG-B-17/-18/-19. Plan: `archive_plans/sprint24_followups.md` |
| 25 — Suggested Actions + agentEvents | 2026-07-16 | `generate_suggested_actions` node (GPT-4o-mini, 3 `{id,label,prompt}`, born-instrumented, degrade→`failed`); non-blocking `agentEvents` emitter (MAIN graph only) — per-`runId` guarded registry, explicit `run_id`, `@events.emits` decorator, `currentRunId`, §3-D drain-before-`done`, `dispose_run`; follow-ups emit NONE; `process_query` streams to capture run_id for `fail_event`. Real-OpenAI E2E proved mid-run delivery. Closes KG-B-19. Plan: `archive_plans/sprint25_suggested_actions.md` |
| 26 — Pre-Test Hardening | 2026-07-18 | Clarification strip (KG-B-8); vault-read retry at tools layer (tight 3/0.5/2.0 fits vault_query's 15s); `AGENT_VERSION`=base+sha (internal `_BASE`, zero consumer repointing); **KG-B-18** delivery retarget (step 6 → `query_doc_id` single-writer state field; `_mark_failed` no-downgrade guard); 3 Prometheus metrics + real `/metrics` (histogram via `@events.emits` + manual observe in the 2 manual-emit nodes `write_to_firestore`/`generate_suggested_actions`); latency report (Postgres-free driver; `synthesize`+`rate_evidence` dominate ~85%). Surgical `prometheus_client` add (no clean rebuild, drift-avoidance). Opened KG-B-20. Plan: `archive_plans/sprint26_pretest_hardening.md` |
| Spec patches (1–15) | pre-Sprint 18 | Firestore-native rewrite — see §Spec Patches below |

---

## Sprint 18 — Agentic Hub Foundation (Phase 8A)

**Outcome:** Built the bare-skeleton hub — a worker process that listens on
`forecastQueries`, atomically claims pending docs, writes a full §8.7.2-shaped stub
`SessionResult`, and transitions status queued → claimed → running → done. No
LangGraph, no LLM, no real reasoning — this validated the worker pattern + Firestore
plumbing end-to-end before reasoning landed.

**Key decisions:**
1. Worker pattern (atomic Firestore claim) over FastAPI/WebSocket; V1 = single worker (multi-worker infra via txns exists but unused).
2. Hub config in a NEW `agent/config/settings.py` package, separate from the pipeline `config/` (independent lifecycle/deployment/domain).
3. Firebase project `anizai-ai` even in the dev emulator (matches `client/.env`) for zero-config pivot to live Firebase.
4. ADC in dev, service-account JSON in prod (`*-service-account*.json` gitignored).
5. Stub SessionResult conforms to the full §8.7.2 schema (`finalProbability=0.5`, `agentVersion="0.1.0-sprint18-stub"`) so the frontend contract is satisfied from day 1.
6. `_EmulatorCredentials(credentials.Base)` wrapper for the Admin SDK against the emulator; bypass `@firestore.transactional` via `_claim_query_txn.to_wrap` for Gate 1.
7. Poll-based `_shutdown_event.wait(timeout=1.0)` — Windows can't interrupt a no-timeout `Event.wait()` with SIGINT.

**Tasks completed:** T18.1–T18.13 (state, config, deps, firestore_client, stub
process_query, worker, health, Dockerfile.agent, compose, Gate 1/2/3 + E2E). 26/26
automated; E2E round-trip 93ms.

**Known gaps raised:** KG-PHASE8-7 (worker logs suppressed), KG-PHASE8-9 (Windows
SIGINT — closed same sprint), KG-PHASE8-10 (emulator UI projection).

---

## Sprint 19 — Vault Retrieval Pipeline (Phase 8B)

**Outcome:** Built the LangGraph skeleton + the three retrieval agents, with the
graph terminating at a relocated Sprint-18 stub synthesize node. Established the
audit-gated start, structured-output classification, and parallel agent dispatch
that the rest of the hub builds on.

**Key decisions (D1–D10):**
1. Audit-gated start (D7) — T19.0 persistence-API audit approved before any code in `agent/tools|agents|nodes`; surfaced 3 corrections upfront.
2. Tool wrappers are the only place the hub touches `persistence/` (Service Isolation); drift normalized at that one boundary.
3. `query_understand` uses OpenAI structured-output (`response_format=json_schema, strict:true`) with closed enums (D9) — eliminates the malformed-JSON branch.
4. Top-3 Polymarket candidates persisted on a new `polymarket_candidates_considered` state field for Sprint 21 reuse (D10), not reusing `clarification_candidates`.
5. `process_query.py` replaced wholesale, not extended (D5); stub helpers + `AGENT_VERSION` moved to `synthesize.py` (D1/D8).
6. ThreadPoolExecutor parallel dispatch in `vault_query`, 15s per-agent timeout, fail-fast.
7. Module-level singleton graph compile (malformed-graph tripwire at startup).
8. `langchain>=0.3,<0.4` pinned mid-sprint — langchain 1.x removed `langchain.debug` that langchain-core's callback manager reads on every `graph.invoke()` (production-breaking, caught by Gate 2).

**Tasks completed:** T19.0 (audit) + T19.1–T19.14. Bundled spec corrections:
§8.4.2 `community_sentiment` str→float (T19.3); §8.3.2 `polymarket_slug` →
`polymarket_search_terms` (T19.5). 222/222 (3 emulator skips). KG-PHASE8-7 closed.

**Known gaps raised:** KG-PHASE8-11 (Polymarket `key_arguments_pro/con` empty),
KG-PHASE8-12 (no Polymarket resolver — auto-pick deferred). KG-PHASE8-5 (venv/
requirements underspecification) escalated.

---

## Sprint 20 — Synthesis + Firestore Writes (Phase 8B)

**Outcome:** Replaced the placeholder with real GPT-4o synthesis, added the
`rate_evidence` and `write_to_firestore` nodes, and persisted the full §8.7.2
SessionResult + subcollections to Firestore. This was the first "it actually works"
milestone — end-to-end Tier 1 against real Firestore + real OpenAI + real vault data.

**Key decisions (D1–D13):**
1. GPT-4o for synthesis (quality-critical); GPT-4o-mini for a single unified `rate_evidence` pass over the whole evidence pool (D1/D2/D4).
2. `synthesize` owns `impact_on_forecast` / `impact_magnitude` / `is_key_evidence` / `rank` — final-forecast judgments, not retrieval-time (D5).
3. Top-level `sessionResults/{id}` per the server contract, not a subcollection (D6) — drift tracked KG-PHASE8-13.
4. `WriteBatch` 500-doc cap (Firestore hard limit); idempotent `evidence_id` doc ids; auto-id for time-series (D7).
5. `agentEvents` + `suggestedActions` deferred to Sprint 25 (D8); `process_query.py` shrunk to a ~55-line exception wrapper (D9).
6. `LOG_INFO_SAMPLE_RATE=1.0` for the hub worker (KG-PHASE8-7 mitigation) — keeps JSON+trace_id, disables 1% sampling.
7. `AGENT_VERSION` → `0.3.0-sprint20-real-synthesis-and-firestore`; recency half-life 7 days (D12).

**Tasks completed:** T20.1–T20.12. 386/386. E2E: cold 36.3s / warm 32.2s, §8.7.2
PASS, ~$0.025–0.030/forecast (sessions `e2e-sprint20-fc6005a0`, `…-8ca30556`).

**Known gaps raised:** KG-PHASE8-13 (sessionResults drift), -14 (legacy query docs),
-15 (no inbound schema validation), -16 (latency > 30s p95), -17 (synthesize/embedding
don't log usage), -18 (empty vault_market summary — closed Sprint 21), -19 (frontend
demo data).

---

## Sprint 21 — Tier 2 + Clarification Flow (Phase 8C)

**Outcome:** Made the agent honest about ambiguity (write `clarificationCandidates`
and stop) and added the Tier 2 freeform path (full pipeline, `marketProbability:
null`). Added the graph's first conditional edge and exercised the partner-side
resume flow.

**Key decisions:**
1. `MARGIN_THRESHOLD=0.10` per spec §8.2.3 (Sprint 19's 0.15 was conservative); `MAX_CANDIDATES=3`.
2. Clarification detection uses LLM confidence as a proxy for vault similarity (no Polymarket vector index — KG-PHASE8-12 still deferred).
3. `ClarificationCandidate.id` = UUID4 stable-within-session for resume matching.
4. Tier inferred in `synthesize` from `market_evidence.polymarket` (None → tier_2); spec-exact Tier 2 caption.
5. G1 production-contract fix — Express resume creates a fresh-UUID `forecastQueries` doc; the hub detects resume via sessionId ≠ query_doc_id and reads `canonicalKey` from the session doc.
6. ≥2-candidate guard added after candidate-shaping (KG-PHASE8-21, a latent single-candidate bug surfaced + fixed during T21.12).
7. `AGENT_VERSION` → `0.4.0-sprint21-clarification-tier2`.

**Tasks completed:** T21.1–T21.12. 441 tests. T21.12 E2E PASS 47s (auto-pick path,
session `e2e-sprint21-7e9fd318`). KG-PHASE8-18 closed; KG-PHASE8-21 opened+closed
within sprint.

---

## Sprint 22 — Foundation Fixes (Revised, Phase 8D)

**Outcome:** Consolidated the wiring fixes that make all five BI cards render real
data on Tier 1 forecasts — a `pg_trgm` Polymarket resolver, `marketProbability` /
`predictionSeries` / `sentimentTimeSeries` threaded through, and `canonicalKey`
written to the session doc. Wiring sprint, not a behavior change — `AGENT_VERSION`
unchanged.

**Key decisions (D1–D6):**
1. `pg_trgm` threshold 0.85 — paraphrase-defense (lowercase + strip non-alphanumeric + unordered trigram set): trivial variations ~1.00, moderate paraphrase 0.50–0.55, heavy < 0.30. silver_job patched to propagate `question` into Polymarket `metadata_extension`.
2. T22.3 expanded to fix LLM-prompt-side wiring — `synthesize` feeds `market_evidence.polymarket` into the call; the synthesis prompt's two "Sprint 20 limitation" passages rewritten to neutral both-cases language (D2).
3. predictionSeries adapter parses ISO string → tz-aware datetime so the server's `toDate?.()` works; injects confidence=1.0 / reasonType="market" / evidenceIds=[] per point (D3).
4. `bucket_sentiment_by_time()` is generic (items, sentiment field, time field); effective window is `(window_days // bucket_days) * bucket_days` (D4).
5. sentimentTimeSeries emits one doc per bucket-date where ≥1 source has data, null for the missing side; (x+1)/2 scale normalization at the write boundary (D6).
6. T22.7 UNSET sentinel + tri-state kwarg distinguishes omit-kwarg from explicit-null for `canonicalKey`.

**Tasks completed:** T22.1–T22.11. Gate 1 226 + Gate 2 3 + Gate 3 emulator. E2E PASS
40.9s (session `e2e-sprint22-c31b5da7`, Option C fallback — Polymarket REST reset →
synthetic insert + run; resolver match_score=1.0). KG-PHASE8-12 closed (wiring +
resolver; vector index → FE 4). KG-PHASE8-22 closed.

---

## Sprint 23 — Producer-trigger Infrastructure (New)

**Outcome:** Added a path for the agent to trigger ingestion of targeted articles
when the vault is insufficient, reusing the existing `ingestion_triggers` Kafka
topic and extending it to the NewsAPI producer — replacing the deferred Tavily/Brave
reactive-search microservice. The trigger node was built and tested in isolation;
it is **not wired into the graph** (that is Sprint 26 T26.7). *(Superseded
2026-06-18: the `.run` interface fix + graph wiring moved to Sprint 23.5 — the node
exposes `trigger_reactive_ingestion()` not `run()` (KG-B-16), and the
`sufficiency_check` node it routes from was never built (KG-B-15). See
`archive_plans/sprint23_5_pre26_remediation.md`.)*

**Key decisions (D1–D7):**
1. D1 — One `getArticles` call per keyword + URL dedup in `run_reactive`; rejected `" OR "`-joined single-keyword (undocumented newsapi.ai behavior). Worst case ≤8 HTTP calls/trigger.
2. D4 — Keywords from `structured_intent.entities` first, then `sufficiency_checks[-1].missing_dimensions`; case-insensitive dedup, cap 8; `raw_question` excluded (mostly stop-words).
3. D5 — Trigger counter increments on every attempt (success OR Kafka failure) — "≤ N attempts per session," prevents retry storms when Kafka is down.
4. D6 — Node always writes the counter on all paths (incl. no-ops) — LangGraph rejects empty `{}` returns.
5. D7 — KafkaProducer is a module-level lazy singleton (reversed from per-call): cold start once per worker amortizes the 1–3s bootstrap; the 2s send timeout fits the warm steady state.
6. Trigger-and-forget — full Bronze→Silver→Gold propagation routinely exceeds the 30s p95 NFR, so the agent does not wait; articles land for the next session.
7. NewsAPI only for V1; other sources are mechanical follow-ups (FE 6).

**Tasks completed:** T23.1–T23.8; T23.9 implemented but Windows-`skipif` (kafka-python-ng
selector race, KG-PHASE8-25 / KG-B-13 — Linux CI verifies); T23.10 deferred to Sprint
26 T26.10.5 (needs the wired graph). 20/20 Gate 1 + 3/3 Gate 2 + 1 Windows-skip Gate 3.

**Known gaps raised:** KG-PHASE8-23 (dead `AGENT_REACTIVE_MAX_PER_SESSION` config),
KG-PHASE8-24 (`__consumer_offsets` cold-start), KG-PHASE8-25 (selector race),
KG-PHASE8-26 (`seek_to_end` race). The last three are test-infra / Kafka-container
behavior; production Linux/GKE unaffected.

---

## Sprint 24 — Follow-up Conversations (Revised)

**Outcome:** Built the follow-up conversation path — a **second, structurally-separate**
LangGraph (`agent/followup/`) that answers chat follow-ups on a completed forecast
**exclusively** from the parent `SessionResult` + its top-5 evidence (no escalation;
that is Future Enhancement 2). A second Firestore **collection-group** listener on
`messages` (`role=='user' AND status=='sent'`) plus a **done-transition sweep** both
feed the subgraph, routed through an **atomic `sent→answered` claim** so each message
is answered exactly once. The answer node is **born-instrumented** (cost via
`llm_cost`) from its first commit. The main forecast graph is untouched — it never
imports the followup package. Full task record: `plans/sprint24_followups.md`.

**Key decisions (ratified 2026-07-04, advisor↔Ron):**
1. **No escalation** — answer from parent context only; insufficient context → a fixed
   transparent message (escalation deferred to FE 2).
2. **Idempotency is an ATOMIC transactional `sent→answered` claim** (mirrors the main
   path's `claim_query`) — only the winner writes the reply; no intermediate
   `processing` state; a crash before commit leaves the message `sent` → re-answered.
3. **The triggering message is identified everywhere by `trigger_message_id`**, never a
   positional `message_history[-1]` pick (the 24.14 back-to-back guarantee);
   `replyToMessageId` links each answer to its question.
4. **Sweep hook = Option A** — called from `process_query` (the runner, not a graph
   node) after a *successful* `graph.invoke()`, **done-guarded** (an
   `awaiting_clarification` terminus is not swept) — keeps the main graph decoupled.
5. **`FollowupState` = 8 flat fields** (incl. `trigger_message_id` + `trigger_question`,
   ratified after review, and `total_cost_usd`).
6. **Budget = local deadline + per-call timeout** `AGENT_FOLLOWUP_BUDGET_MS=6000`,
   `max_retries=0`; overrun → a **complete** caveat message (content-borne; assistant
   messages carry no frontend-facing status). No shared budget module.
7. **Fixed, deterministic copy** for the two non-answerable classes (guardrail-lite in
   the prompt; a dedicated pre-LLM filter is deferred).
8. **`agentEvents` deliberately OUT** — Sprint 25 (T25.7) retrofits the three nodes.
9. **CG index is partner-side** (`server/firebase/firestore.indexes.json`; the
   `evidence` CG index is the template).

**Tasks completed:** T24.1–T24.15. Gate 1 35 + Gate 2 3 + Gate 3 4 (emulator) green.
Full agent suite **604 passed / 3 skipped / 2 failed** — the two failures pre-existing
and **stash-proven not Sprint 24** (KG-B-18/-19). **Real E2E**
(`tests/e2e/sprint24_e2e_run.py`, real gpt-4o-mini + Firestore emulator) **PASS**: 5
follow-up calls, latency min 1667 / median 2514 / max 4109 ms, **0/5** budget-timeouts,
the 3rd follow-up confirmed `insufficient_evidence` via the real LLM (session
`e2e-sprint24-e4658ba3`, ~$0.0013). Commit `d49ee388` (author Ron; not pushed).

**Known gaps raised:** KG-B-17 (follow-up budget validated only on a small local sample
— watch during the initial cloud test), KG-B-18 (pre-existing Sprint-21 resume-doc 404,
emulator-exposed; Sprint 26 fix), KG-B-19 (pre-existing LangGraph `InvalidUpdateError`;
suspected `langchain-core` drift within `>=0.3,<0.4`, à la KG-PHASE8-5 — not a
`langgraph` bump, which is pinned exact).

**Next:** Sprint 25 (Suggested Actions + agentEvents) — `plans/sprint25_suggested_actions.md`.

---

## Sprint 25 — Suggested Actions + Chain-of-Thought Events

**Date closed:** 2026-07-16
**Scope:** Two deferred dynamic-UI features — 3 contextual suggested actions per
forecast (a GPT-4o-mini node after `synthesize`) and a non-blocking `agentEvents`
chain-of-thought stream on the MAIN forecast graph. Full task record (T25.0–25.14,
all `[x]`) → `archive_plans/sprint25_suggested_actions.md`.

**Outcome:** `generate_suggested_actions` (Node 6.5) runs between `synthesize` and
`write_to_firestore`, which injects `suggestedActions` into the SessionResult. A
non-blocking emitter (`agent/events.py`) streams `agentEvents` during the run —
every main-graph node emits start/complete, `claim_session` emits a one-shot
bootstrap. Follow-ups emit nothing (the 2026-07-04 inversion of the original T25.7).
Gate 1/2/3 green + a real-OpenAI E2E that proved events arrive DURING the run. Opened
with T25.0, which closed KG-B-19 as a test-input defect (empty `graph.invoke({})`) —
no dependency change.

**Key decisions:**
1. Suggested actions = a separate GPT-4o-mini call after synthesize (keeps synthesis
   lean); schema `{id,label,prompt}` with `id` (`sa-1/2/3`) assigned deterministically
   by the node, not the LLM; prompts constrained to be answerable from THIS forecast
   (so the Sprint-24 follow-up graph can actually serve a clicked chip).
2. The node is **born-instrumented** and **G4-graceful**: any failure (incl. client
   construction, moved inside the try) degrades to `[]` and completes its event
   `failed` — never fails the forecast (contrast synthesize, which raises).
3. `agentEvents` emission is **non-blocking** (enqueue + single FIFO background writer)
   AND fire-and-forget (write failures swallowed).
4. `sequence` + `runId` live in the emitter's **per-`runId` guarded registry**, NOT on
   state; `ForecastState.run_id` is the single new state field (single-writer,
   `claim_session`). Run resolution is by **explicit `run_id` from state**, not
   thread-local (LangGraph runs nodes on pool threads — a thread-local set in
   claim_session wouldn't propagate). Concurrency-correct once
   `AGENT_MAX_CONCURRENT_SESSIONS` > 1 (default 3).
5. Registry bounded by **explicit `dispose_run`** at `process_query`'s finally — NOT
   idle-pruning (a run between nodes is momentarily idle but not done; idle-prune would
   drop an active concurrent run's context).
6. `claim_session` bootstrap order: mint `run_id` → write `currentRunId` on the
   `running` transition (new Convention-A kwarg on `update_session_status`, no extra
   write) → `init_run` → one-shot `done` bootstrap event → return `run_id`. currentRunId
   must precede the first event.
7. §3-D pinned drain order in `write_to_firestore`: outputs → complete last event →
   drain → session `done` → queue `done`.
8. `process_query` streams via `graph.stream(stream_mode="values")` to capture `run_id`
   off the accumulated state before a raise, so `fail_event` fires the exact-run path
   (session_id backup). DRY `@events.emits` decorator on the 9 pair-nodes;
   `write_to_firestore` + `generate_suggested_actions` wired manually (drain ordering /
   failed-on-degrade). Event `type` = node name (open vocabulary); `status` ∈
   `pending/running/done/failed`; no `parentMessageId`.
9. Test hygiene: shared `mock_reactive_producer` + `mock_suggested_actions_client`
   conftest fixtures made the Gate-2 + emulator full-forecast tests hermetic (Gate 2
   shouldn't hit a real broker — pre-existing since 23.5). KG-B-18 guarded with an
   xfail (Sprint-26 fix earmarked).

**Tests:** Full agent suite (Kafka+Postgres+emulator up): **634 passed, 3 skipped
(KG-B-13 kafka-python-ng Windows selector race — prod/CI-Linux unaffected), 1 xfailed
(KG-B-18), 0 failed.** Gate 1 `test_sprint25_gate1.py` (21) · Gate 2
`test_sprint25_gate2.py` (3) · Gate 3 `test_sprint25_gate3.py` (4, live emulator) ·
E2E `e2e_sprint25_agentevents.py` (mid-run delivery proven; real OpenAI; one run).

**Gaps:** None new. **Closed KG-B-19.** Carried: KG-B-18 (xfail → Sprint 26), KG-B-13
(kafka Windows), plus the standing KG-B-* set — see `hub_sprints.md §4`.

**Next:** Sprint 26 — Pre-Test Hardening (`plans/sprint26_pretest_hardening.md`).

---

## Sprint 26 — Pre-Test Hardening (Closed 2026-07-18)

**Outcome:** Pure hardening + observability before the initial cloud test — **no
graph-topology changes**. Stripped dead clarification fields (KG-B-8); wrapped all 9
vault-read tool functions in a tight retry; added a git short-hash to `agentVersion`;
retargeted the delivery path to close **KG-B-18**; shipped 3 Prometheus metrics + a
real `/metrics` endpoint; produced a per-node latency report. Full agent suite **637
passed / 15 documented infra-skips / 0 failed / 0 xfail** (Gate 1 `test_sprint26_gate1.py`
13/13 + version-sha; Gate 2 `test_sprint21_gate2.py` 8/8 incl. subgraph strip; Gate 3
`test_emulator_integration.py` 7/7 incl. the KG-B-18 test green with xfail removed +
`agentVersion` coupling; 2 real-OpenAI latency forecasts).

**Key decisions:**
1. **Vault-read retry 3/0.5/2.0 (tighter than Gold's 5/1.0/16.0).** Agent reads run in
   `vault_query`'s 15s per-agent future (fail-fast); Gold's ~15s backoff would time out
   first. ≤1.5s backoff fits; rides out F6 DNS/connection races only. Wrapped at the tools
   layer via a shared `agent/tools/_retry.py` + settings profile. (26.6)
2. **`AGENT_VERSION` = `base+sha`** (base as internal `_BASE`, env-overridable; base bumped
   to `0.5.0-sprint26`). Single name → zero consumer repointing; emulator coupling
   `result["agentVersion"] == synthesize.AGENT_VERSION` holds by construction. Image build
   must inject `AGENT_GIT_COMMIT_SHORT_SHA` (Domain C). (26.5)
3. **KG-B-18 via step-6 retarget, not "seed the original."** Single-writer `query_doc_id`
   on `ForecastState`; step 6 marks the processed fresh doc `done` (not the original doc
   `claim_session` overwrote `session_id` onto). `_mark_failed` no-downgrade guard + step-6
   L1 try/except retained as backstops. (26.11)
4. **3 metrics on the default registry** (`agent/metrics.py`): `agent_node_duration_seconds`
   histogram via `@events.emits` + a manual `.observe()` in the **two manual-emit nodes**
   (`write_to_firestore`, `generate_suggested_actions` — the latter a completeness gap the
   26.3 run surfaced); `agent_llm_cost_usd_total` via `record_usage` (agent copy);
   `agent_session_total{tier,status}` at done/failed. Real `/metrics` via `generate_latest()`
   replaces the stub. `agent_queue_depth` gauge deferred to Sprint 27. (26.4)
5. **Surgical `prometheus_client==0.25.0` add, not a clean venv rebuild** (Advisor↔Ron).
   Zero-dep, hand-added to `requirements.lock` (BOM preserved); avoids re-resolving
   langchain/openai/google (KG-B-19/PHASE8-5 drift) before the baseline test. Removed the
   Phase-7C `newspaper4k` venv orphan; co-orphans → KG-A-11 (Domain A).
6. **26.3 Postgres-free latency driver** (`tests/e2e/sprint26_latency_run.py`) — conscious
   deviation from literal T20.11/T21.12. Emulator + real OpenAI + realistic-volume mocked
   agents. `synthesize` (~16–19s) + `rate_evidence` (~12–14s) dominate ~85% of ~35s; both
   scenarios within ≤60s p95; no O(1) regressions. `vault_query` = mock floor; real-vault +
   cold/warm p95 → cloud baseline day-run. Report: `docs/B_hub/sprint26_latency_report.md`.

**Gaps:** Closed **KG-B-8** + **KG-B-18**. **Opened KG-B-20** (forecastQueries hygiene —
original clarified doc lingers at `awaiting_clarification`; `deleteSession` misses fresh
resume docs; Low). KG-B-5 analysis shipped (re-escalate if the day-run crosses 60s p95 or
at Phase 10). KG-A-11 (Domain-A co-orphan libs) Advisor-logged.

**Next:** the **initial cloud test** (~2 days) — gated on Sprint 26, now unblocked. Before
it: one cumulative `anizai-agent` image rebuild (Domain C) injecting
`AGENT_GIT_COMMIT_SHORT_SHA` + applying the `reactive_triggers_log` DDL to cloud Postgres.
Then Sprint 27 (post-test polish). Full task record →
`archive_plans/sprint26_pretest_hardening.md`.

---

## Spec Patches (1–15)

The 15 patches in `docs/old_docs/agentic_hub_spec_patch.md` realigned the original spec (Postgres
`forecast_sessions` table, FastAPI/WebSocket gateway, ephemeral Tier 2, read-only
Postgres) to the Firestore-native reality of the partner frontend. Applied before
Sprint 18.

1. **§7.3 IAM** — dual-store model: read-only PG vaults, read-write on `reactive_article_cache`, write to Firestore session collections via Admin SDK.
2. **§8.1 IS/IS-NOT** — Firestore worker replaces the FastAPI/WebSocket API layer.
3. **§8.1.1 dependencies** — add reactive cache, source allowlist, firestore client, forecastQueries listener rows.
4. **§8.2.2 Tier 2** — persist to Firestore (`tier:"tier_2"`, `canonicalKey:null`) instead of ephemeral; surface related markets.
5. **§8.2.3 clarification** — ambiguous/multi-market detection + clarify-and-stop flow; add **§8.2.4 ClarificationCandidate** schema.
6. **§8.3.1 ForecastState** — add clarification, reactive-search, and output fields; `evidence_trail` typed as `EvidenceItem[]`.
7. **§8.3.2 graph topology** — sufficiency-check + reactive-search loop + clarification branch (the loop portion was later not built — see `hub_agents.md` §2.4).
8. **§8.3.3 conditional edges** — `ambiguous?` and `sufficient?` routing logic.
9. **§8.4 agents** — tag `origin`, emit `agentEvents`; per-agent shapes for inter-node passing, unified `EvidenceItem` only at the Firestore write.
10. **§8.5** — renamed "Evidence Evaluation & Sufficiency Checking"; add **§8.5.4 VaultSufficiencyCheck** rubric + **§8.5.5 unified EvidenceItem** schema.
11. **§8.7** — Firestore persistence model; remove the `forecast_sessions` Postgres table; SessionResult schema + label thresholds; cache/staleness/resolution.
12. **§8.8** — worker pattern replaces the API gateway; only `/health` + `/metrics`; follow-up + clarify-resolution flows.
13. **§8.10 directory** — `agent/`, `reactive_search/`, `persistence/reactive_cache.py`; remove `api/` and `persistence/forecast_sessions.py`.
14. **§8.11 config** — Firestore + reactive-search env vars; remove `API_*` and `AGENT_MAX_REACTIVE_ITERATIONS`.
15. **§8.12 NEW** — Reactive Search Microservice section (later deferred indefinitely — see `hub_sprints.md` §3).
