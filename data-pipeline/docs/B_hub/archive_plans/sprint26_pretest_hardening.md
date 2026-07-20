# sprint26_pretest_hardening.md
> Domain: B — Agentic Hub
> Type: Sprint Plan
> Last updated: 2026-07-18
> TL;DR: The self-contained plan for **Sprint 26 — Pre-Test Hardening**. The minimum
>        observability + resilience needed for the two-day initial test. **Pure
>        hardening + observability — no graph-topology changes** (the trigger wiring,
>        cost logging, and `AGENT_VERSION` relocation all moved to Sprint 23.5). Task
>        IDs reflect the post-23.5 reconciliation (old 26.1 / 26.7 / the 26.5
>        relocation half / 26.10.5 are gone — see §4 "Moved to Sprint 23.5"); 26.11 is
>        a later-added implementation task (2026-07-16 review — see §4 numbering note).
>        **Unblocked — Sprint 25 closed 2026-07-16.**

## Navigation
- §1 — Scope
- §2 — Confirmed design decisions
- §3 — Design Rationale Log — Sprint 26 (from the Phase-8 revised plan + the 2026-07-16 review)
- §4 — Task table (post-23.5) + what moved to Sprint 23.5
- §5 — Cross-sprint context — see hub_sprints.md

> **2026-07-16 review note.** This plan was originally written 2026-06-28, *before*
> Sprints 24 and 25 closed and before several decisions landed (KG-B-5 NFR relaxation
> 2026-07-04, KG-B-18 opened 2026-07-04). An Advisor↔Ron pre-Sprint-26 review on
> 2026-07-16 reconciled the plan against the actual post-Sprint-25 codebase. The
> substantive changes: the Postgres-retry scope was expanded from momentum-only to all
> vault-read paths (26.6); a delivery-path hardening task was added (26.11, closing
> KG-B-18); the Prometheus metrics set was trimmed (queue-depth gauge deferred to 27)
> and its cost-counter source corrected; and the version-string task was corrected to
> read the canonical `AGENT_VERSION` rather than hardcode a base. Details inline below
> and in §3.
>
> **2026-07-18 review note (readiness verification).** An Advisor↔Ron pre-handoff
> review verified every concrete claim in this plan against the live codebase (all nine
> 26.6 tool-function names, the four clarification fields, `record_usage`, `_METRICS_STUB`,
> `AGENT_VERSION`, step 6, `_mark_failed`, the KG-B-18 xfail) and revised two tasks:
> (1) **26.11** was re-framed from "swallow the step-6 404" to the root fix the Sprint-21
> author intended — carry the processed queue-doc id (`query_doc_id`) through state and
> point step 6 at it, so the doc that actually ran is the one marked `done` (the original
> clarified doc correctly stays `awaiting_clarification`; the `_mark_failed` no-downgrade
> guard is retained as the structural backstop, the step-6 try/except downgraded to
> general hardening). (2) **26.4** gained a manual `agent_node_duration_seconds` observe
> in `write_to_firestore` (load-dependent latency — evidence subcollection + the
> agentEvents drain); `claim_session` stays out by design (fixed, load-independent
> bootstrap latency, not the load-varying tail the histogram exists to isolate). Opened
> KG-B-20 (forecastQueries hygiene). Details in §2/§3/§4.

---

## §1 — Scope

Focused observability + retry + version tracking work needed before the two-day
initial test. Most of the original Sprint 26 task list moved to Sprint 27
(post-test polish). After the Sprint 23.5 remediation absorbed the trigger wiring,
the central cost layer, and the `AGENT_VERSION` relocation, this sprint is **pure
hardening + observability with no graph-topology changes**. It includes only items
that genuinely gate meaningful initial-test results:

- KG-B-8 (was KG-PHASE8-20) — `clarificationCandidates` hub-internal field cleanup
  (**strip**; the fields are dead — see §2)
- KG-B-5 (was KG-PHASE8-16) — per-node latency analysis (**analysis only**; fix in
  Sprint 27 or Phase 10). Must cover the Sprint-25 additions and read against the
  relaxed ≤60s p95 NFR — see 26.3
- T26.6 — agent-specific Prometheus metrics (three metrics hung off existing hooks;
  the queue-depth gauge is deferred to Sprint 27 — see §2 / 26.4)
- T26.7 — `agentVersion` adds a git short-hash **on top of** the Sprint-23.5
  canonical `AGENT_VERSION`, for a single source of version truth
- T26.2 (partial) — Postgres retry wrapper, **expanded** to every vault-read call
  site across all three retrieval paths, wrapped at the tools layer — see §2 / 26.6
- **Delivery-path hardening + KG-B-18** — step 6 must mark the queue doc it actually
  processed (`query_doc_id`), not the original session doc; and no write after the
  `done`-flip may fail or reverse a delivered forecast — see 26.11

**Blockers:** none (Sprint 25 closed 2026-07-16). **Next action:** now. KG-B-15 (the
old T26.7 routing blocker) and the trigger wiring were resolved in Sprint 23.5.

---

## §2 — Confirmed design decisions

- **Latency analysis is per-node, not just total.** Without per-node attribution, the
  data is useless. Output is a written summary (its own report / Known-Gaps note) —
  not a code change. **The report must now cover `generate_suggested_actions` (the
  Sprint-25 post-synthesis node on the critical path) and the `agentEvents` emission
  overhead (the `@events.emits` wrapper + background writer), and must be read against
  the relaxed ≤60s p95 NFR (KG-B-5, product decision 2026-07-04) — not the retired 30s
  target.**
- **Cost is already centralized (Sprint 23.5 Track 2).** The four agent LLM call
  sites compute `cost_usd` via `agent/utils/llm_cost.py` and accumulate
  `state.total_cost_usd`. Sprint 26 does not re-implement cost logging (old 26.1 moved
  to 23.5). **The `agent_llm_cost_usd_total{model}` counter hooks into
  `agent/utils/llm_cost.py`'s `record_usage` (the agent copy only — there are two
  copies, agent + pipeline), which is the single place `model` and `cost_usd` coexist
  per call. It does NOT read `state.total_cost_usd` — that is a single aggregate scalar
  with no per-model split, so it cannot carry the `model` label.**
- **Prometheus via `prometheus_client`, exposed through the existing health server.**
  Use `prometheus_client`'s `generate_latest()` inside the existing `/metrics` handler
  in `agent/health.py` (replacing the `_METRICS_STUB` text) — no second HTTP server.
  Adding `prometheus_client` is a new pinned dependency, but the Domain C
  `anizai-agent` image is rebuilt at the initial-test phase regardless (everything from
  Sprint 22 onward is not yet on cloud), so it adds no *extra* rebuild. **Three metrics
  ship in Sprint 26**, each hung off a hook that already exists: `agent_node_duration_seconds`
  (histogram, label `node_name`) via the Sprint-25 `@events.emits` wrapper — which covers
  the decorator-emitting nodes — **plus a manual `.observe()` in the two nodes that emit
  their event pair manually, `write_to_firestore.run()` and `generate_suggested_actions.run()`**
  (both on the critical path and outside the decorator; `generate_suggested_actions` emits
  manually so its degrade path can report `failed` vs `done`). `write_to_firestore`'s latency
  is load-dependent (evidence subcollection size + the agentEvents `drain`). `claim_session`
  is deliberately NOT
  instrumented: its ~3–4 fixed Firestore round-trips are load-independent bootstrap
  latency, not the load-varying tail this histogram exists to isolate.
  `agent_llm_cost_usd_total` (counter, label `model`) via `record_usage`;
  `agent_session_total` (counter, labels `tier`, `status`) at the done/failed
  terminals. **`agent_queue_depth` (gauge) is deferred to Sprint 27** — it is an
  operational metric for multi-user load, not for a 3–4-user / 2-day test, and it is
  the only one of the four that needs a new update mechanism (poll / listener callback)
  rather than an existing hook.
- **Postgres retry uses the existing `utils/retry.retry_on_transient()` helper from
  Phase 9.5 Stage B.** Zero new code in `utils/`. **Wrapped at the tools layer**
  (`agent/tools/*`), which is the single chokepoint through which every agent reaches
  persistence (service isolation, CLAUDE.md §3.3) — one wrap per persistence function
  covers all call sites automatically (e.g. `fetch_latest`, called 3× from
  `market_bridge` for polymarket / linked_sources / googletrends). No agent
  business-logic change. The nine read functions:
  - `market_tools`: `fetch_latest`, `fetch_time_series`, `fetch_fred_anomalies`,
    `find_polymarket_market_by_question`
  - `knowledge_tools`: `similarity_search`, `fetch_full_text`
  - `social_tools`: `similarity_search`, `fetch_raw_comments`
  - `mapping_tools`: `lookup_by_canonical`

  **Why expanded from momentum-only (the real gap this closes).** The original T26.2
  scoped this to the four `momentum_vault` functions only — an artifact of mirroring
  Gold's Phase-9.5 wrapping (the helper was built for Gold's `momentum_vault_insert`
  path), not a principled boundary. In reality all three retrieval agents read Postgres
  vaults (researcher→knowledge, pulse→social, market→momentum + mapping), and a GKE
  Postgres scale-cycle blip in the cloud hits all of them equally. Only Gold's insert
  path is currently wrapped; the agent's read call sites are unprotected. Momentum-only
  would have left two-thirds of the vault-read surface exposed going into the initial
  cloud test. Tracked here in-plan as justification rather than as a separate KG (the
  work is already scheduled). The retry adds no happy-path overhead (it fires only on a
  transient error) and `vault_query` dispatches the three agents in parallel, so even a
  worst-case retry does not serialize across agents.
- **Delivery-path hardening (KG-B-18) — root fix + backstop (re-framed 2026-07-18;
  supersedes the two-layer "swallow the 404" framing in the §3 2026-07-16 review).** The
  KG-B-18 emulator 404 is a *wrong-target* bug, not merely an unguarded write. On a
  resume-on-clarify run, `write_to_firestore` step 6 calls
  `update_query_status(session_id, "done")` on the ORIGINAL session's queue doc — but the
  doc the run actually claimed and processed is the fresh-UUID doc the server created in
  `requeueClarifiedSession` (id ≠ sessionId). `claim_session` overwrites
  `state["session_id"]` with the resolved session id, so by step 6 the runner has lost
  the processed doc's id. Three parts:
  - **Root fix (primary)** — carry the processed queue-doc id through state as a new
    single-writer `query_doc_id` field. `process_query._build_initial_state` sets it in
    both paths (first-time: `query_doc_id == session_id`; resume: the fresh-UUID doc
    id — the value already passed in as `query_doc_id`). Step 6 changes from
    `update_query_status(session_id, "done")` to
    `update_query_status(query_doc_id, "done")` so the doc that actually ran is the one
    marked `done`. Expected end-state on resume: the fresh doc → `done`; the original
    clarified doc correctly stays `awaiting_clarification` (it never produced a forecast,
    so `done` on it would be a lie). No 404 in the first place — step 6 now hits an
    existing doc.
  - **Layer 2 (structural backstop, retained)** — a guard in
    `process_query._mark_failed`: before writing `failed`, read the current session
    status, and if it is already `done`, do not downgrade — log and skip. This locks the
    invariant that *any* future write added after the `done`-flip cannot reverse a
    delivered forecast, independent of step 6's target.
  - **Layer 1 (general hardening, downgraded)** — still wrap step 6 in its own
    try/except that logs a WARNING and continues, so a missing/errored queue doc can
    never fail a delivered forecast. With the root fix in place this no longer carries
    the KG-B-18 test (the retarget does); it remains belt-and-suspenders for any
    unexpected queue-doc state.

  The invariant — *no write after the `done`-flip may fail or reverse a delivered
  forecast* — is recorded as a constraint that the Sprint 27 unified error handler
  (T27.2, KG-B-7) must preserve (see §5). A separate, pre-existing hygiene gap surfaced
  by this analysis (the original doc lingering at `awaiting_clarification`, and
  `deleteSession` not removing fresh resume docs) is logged as KG-B-20 — out of scope
  here.
- **OpenAI retry is already complete** (verified 2026-05-23 against
  `utils/openai_client.py`). No further work needed in Sprint 26.

---

## §3 — Design Rationale Log — Sprint 26 (from the Phase-8 revised plan + the 2026-07-16 review)

- **Original Sprint 26 was scaled back from 12 tasks to ~6.** The original task list
  was production-hardening (50-session load test, 3-worker concurrency, edge case
  coverage) that targets a multi-tenant production environment, not an initial test
  with 3–4 users for 2 days. Moved production-hardening work to Sprint 27.
- **KG-PHASE8-17 prioritized as a pre-test must-have** (cost-tracking accuracy). The
  whole point of the two-day initial test is cost measurement (per KG-PHASE-9.5-9).
  *Resolved early:* this became the Sprint 23.5 Track 2 cost layer (rescoped to all
  4 LLM sites, KG-B-6); Sprint 26 only consumes it via the metrics counter.
- **KG-PHASE8-16 (now KG-B-5) reduced to analysis-only.** The full latency
  optimization task was originally in Sprint 26. Analysis is cheap (half day);
  fix-in-response is expensive and uncertain. Splitting them: analysis ships in 26,
  optimization (if needed) ships in 27 or Phase 10.
- **OpenAI retry verified already complete.** Phase 9.5 Stage B Item 2 added
  `utils/openai_client.py` with `max_retries=5`. Verified against the file on
  2026-05-23: every OpenAI call across the agent goes through this centralized
  factory. No new retry work needed for OpenAI.
- **Postgres retry already has a helper.** Phase 9.5 Stage B Item 1b added
  `utils/retry.retry_on_transient()`. No new infrastructure — the sprint just wraps
  the agent's vault-read call sites in it.

### Added / revised in the 2026-07-16 pre-Sprint-26 review

- **Postgres-retry scope expanded from momentum-only to all vault reads (26.6).** The
  original scope (four `momentum_vault` functions) was an artifact of mirroring Gold's
  wrapping, not a considered boundary — the §2 design bullet even named all three
  retrieval agents while the task listed only market functions (`researcher` and
  `pulse_analyst` have zero momentum call sites; the contradiction was original and
  copied faithfully into this file). Corrected: wrap every vault-read function at the
  tools layer across `market_tools` / `knowledge_tools` / `social_tools` /
  `mapping_tools`. Rationale + the real gap this closes are in §2.
- **Delivery-path hardening added (26.11, closes KG-B-18).** KG-B-18 was opened
  2026-07-04, after this plan's 2026-06-28 date, and was earmarked for Sprint 26 but had
  no task here. Added as a two-layer hardening (step-6 best-effort + `_mark_failed`
  no-downgrade guard). Framed not as a test-seeding fix but as removing a latent
  production footgun: step 6 runs after the `done`-flip, so its exception would flip a
  delivered forecast to `failed`. See §2.
- **Prometheus metrics trimmed and re-sourced (26.4).** Three metrics ship (node
  duration, LLM cost, session totals), each hung off an existing hook; the queue-depth
  gauge is deferred to Sprint 27 (operational metric, needs a new update mechanism, low
  value for a 3–4-user test). The cost counter is sourced from `record_usage` (agent
  copy) — the only place `model` + `cost_usd` coexist — not from the aggregate
  `state.total_cost_usd`, which cannot carry the `model` label.
- **Version-string task corrected (26.5).** The original example format
  (`0.5.0-sprint26+<sha>`) contradicted "on top of the canonical `AGENT_VERSION`"
  (which is `0.5.0-sprint23.5` in code). Corrected: read `settings.AGENT_VERSION` and
  append `+<short-sha>` — never hardcode a base in code (that would re-split the source
  of truth that 23.5 Track 3 consolidated). The base is bumped once to `0.5.0-sprint26`
  at its single canonical home in `agent/config/settings.py`. `AGENT_VERSION` is only a
  human-facing label (health response + SessionResult); no logic branches on it, so the
  bump is cosmetic and the git short-hash carries the real build identity.
- **Latency report refreshed (26.3).** Must cover the Sprint-25 additions
  (`generate_suggested_actions`, `agentEvents` overhead) and be read against the relaxed
  ≤60s p95 NFR — see §2.

### Added / revised in the 2026-07-18 pre-handoff readiness review

- **26.11 re-framed from "swallow the 404" to "retarget step 6" (Advisor↔Ron).** The
  2026-07-16 draft treated KG-B-18 as an unguarded-write problem (wrap step 6 in
  try/except so the emulator 404 can't fail the forecast). Tracing the code showed the
  real defect is that step 6 marks the **wrong doc**: on resume-on-clarify it targets the
  original session's queue doc, while the run actually processed the fresh-UUID doc the
  server minted in `requeueClarifiedSession`. The 404 is only the test's symptom (the
  test never seeds the original doc); in production the original exists and gets a
  spurious `done` while the fresh doc it processed is stranded at `claimed`. The chosen
  fix carries `query_doc_id` through state and points step 6 at it — the doc that ran is
  the one marked `done`, the original correctly stays `awaiting_clarification`, and no
  404 occurs in the first place. This is exactly the fix the Sprint-21 author anticipated
  ("Sprint 26 adds query_doc_id to state", per the test docstring). Considered but
  rejected for now: collapsing to a single queue doc (server reuses the original id) — it
  is cleaner conceptually but crosses into the partner-owned server AND reopens the
  G1 resume-detection contract (`doc_id != sessionId`) days before the initial test;
  deferred to a post-test server-coordinated change. The `_mark_failed` no-downgrade
  guard (old Layer 2) is retained as a structural backstop; the step-6 try/except (old
  Layer 1) is retained as general hardening but no longer carries the KG-B-18 test.
- **26.4 histogram coverage clarified (Advisor↔Ron).** `agent_node_duration_seconds`
  hangs off `@events.emits`, which wraps only the 9 decorated pair-nodes; `claim_session`
  (one-shot) and `write_to_firestore` (manual emit) are outside it. `write_to_firestore`
  is added via a manual `.observe()` because its latency is load-dependent (evidence
  subcollection size + the pre-`done` agentEvents `drain`) — precisely the load-varying
  tail the histogram exists to catch, and the node most exposed to cloud/network jitter.
  `claim_session` is left out deliberately: it is not O(1) (it performs ~3–4 Firestore
  round-trips — the claim transaction + two `update_session_status` writes) but that cost
  is **constant and load-independent**, i.e. stable base noise, not the tail being
  isolated; instrumenting it would add a fixed offset to a metric whose whole purpose is
  variance. *(Correction, 2026-07-18 build session: `generate_suggested_actions` is also a
  manual emitter, not decorator-covered — it likewise gets a manual `.observe()`. See the
  build-session note below.)*

### Implementation decisions (2026-07-18 build session)

- **`prometheus_client` added via surgical install, NOT a clean venv rebuild
  (Advisor↔Ron).** A conscious, documented deviation from CLAUDE.md §4.4's literal
  "clean venv rebuild" wording, chosen for drift-avoidance before the baseline test: a
  clean rebuild re-resolves the range-pinned langchain/openai/google lines to
  latest-in-range — the exact KG-B-19 / KG-PHASE8-5 drift surface — which would make any
  cost/latency/quality shift in the baseline test unattributable (our code vs a langchain
  bump). §4.4's *intent* (requirements.txt ↔ lock in sync, reproducible build) is met:
  `pip install 'prometheus-client>=0.20,<1.0'` (zero transitive deps → resolved 0.25.0),
  the range line added to `requirements.txt`, and `prometheus_client==0.25.0` HAND-inserted
  into `requirements.lock` (BOM + every other line preserved byte-for-byte; NO `pip freeze`,
  which would have recaptured drift). Full agent suite green afterward (608 passed / 30
  infra-skips).
- **Pre-existing venv drift surfaced + partially resolved.** The precondition sync check
  (venv `pip freeze` vs `requirements.lock`) found one orphan: `newspaper4k==0.9.5` (the
  Phase-7C-retired scraper lib, closed 2026-05-09) — installed in the venv but absent from
  both manifests and imported by zero code. Uninstalled it. Its transitive-only co-orphans
  still in the lock (`lxml_html_clean`, `requests-file`, `tldextract`, `w3lib`) are a
  Domain-A Phase-7C hygiene item (**KG-A-11**, Advisor-logged) — NOT touched here.
- **26.3 measured via a Postgres-free driver — conscious deviation from the ratified "run
  T20.11 + T21.12" mechanism (Advisor↔Ron).** The literal E2E drivers need a worker + real
  Postgres purely to time `vault_query` (not the bottleneck). Same node coverage obtained
  via `tests/e2e/sprint26_latency_run.py`: emulator + real OpenAI + realistic-volume mocked
  agents (one Tier-1 + one Tier-2; NOT a redundant cold/warm pass). `vault_query` is a mock
  floor; real-vault + cold/warm p95 deferred to the cloud baseline day-run. Report:
  `docs/B_hub/sprint26_latency_report.md` (← KG-B-5). Result: `synthesize` (~16–19s) +
  `rate_evidence` (~12–14s) dominate ~85% of ~35s; both scenarios within ≤60s p95; no O(1)
  regressions.
- **26.4 completeness fix surfaced by the 26.3 run.** `generate_suggested_actions` emits
  `agentEvents` MANUALLY (not via `@events.emits`), so it was outside the histogram hook —
  the plan's §2 had wrongly assumed it was a decorated pair-node. Added a manual `.observe()`
  (success path, mirroring `write_to_firestore`) + Gate-1 test. The metric contract is
  unchanged; the histogram now covers all load-bearing token-bound nodes.

---

## §4 — Task table (post-23.5, revised 2026-07-16)

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 26.2 | `[x]` | Close KG-B-8 (was KG-PHASE8-20) — clean ClarificationCandidate writes. **Strip** the hub-internal fields in `agent/nodes/query_understand.py` `_build_clarification_candidates` before they enter state (so `write_clarification` writes clean candidates). The fields are actually **four** — `intent`, `domain`, `entities`, `polymarket_search_terms` (there is no `polymarket_slug` write; the old "5 fields incl. polymarket_slug" wording was wrong). Strip is safe: `process_query._build_initial_state` hardcodes `structured_intent` on the resume path and Express clears `clarificationCandidates` before requeue, so nothing consumes these fields. **Also fix the stale docstring** in `_build_clarification_candidates`, which currently claims the fields are "consumed by process_query on resume" — they are not. Keep the five spec-contracted fields (`id`, `label`, `source`, `description`, `matchConfidence`). | Gate 1, Gate 2 | §8.2.4 (patched) |
| 26.3 | `[x]` | Close KG-B-5 (was KG-PHASE8-16) **analysis only** — per-node latency instrumentation + report. Run T20.11 + T21.12 scenarios; produce a per-node-latency report (Known Gaps note / own doc) classifying each node "token-volume-bound (expected)" vs "O(1) regression candidate". **Must include `generate_suggested_actions` (Sprint-25 post-synthesis node, on the critical path) and the `agentEvents` emission overhead (`@events.emits` wrapper + background writer). Read results against the relaxed ≤60s p95 NFR (KG-B-5, 2026-07-04) — not the retired 30s target.** Feeds Phase 10 load planning regardless of the NFR. | — | KG-B-5 |
| 26.4 | `[x]` | Implement T26.6 — agent-specific Prometheus metrics via `prometheus_client.generate_latest()` inside the existing `/metrics` handler in `agent/health.py` (replaces `_METRICS_STUB`; no second server). **Three metrics, each off an existing hook:** `agent_node_duration_seconds` histogram (label `node_name`) via the Sprint-25 `@events.emits` wrapper (the decorator-emitting nodes) **plus a manual `.observe()` in the two manual emitters, `write_to_firestore.run()` and `generate_suggested_actions.run()`** (both on the critical path, outside the decorator; `generate_suggested_actions` emits manually so its degrade path reports `failed` vs `done`) — `claim_session` deliberately excluded (fixed ~3–4-round-trip bootstrap, load-independent, not the tail this histogram isolates); `agent_llm_cost_usd_total` counter (label `model`) via `agent/utils/llm_cost.py` `record_usage` (**agent copy only** — the single place `model`+`cost_usd` coexist; NOT `state.total_cost_usd`, which has no per-model split); `agent_session_total` counter (labels `tier`, `status`) at the done/failed terminals. **`agent_queue_depth` gauge is deferred to Sprint 27** (operational metric; needs a new update mechanism). Add `prometheus_client` to `requirements.txt` (+ `.lock`) — no extra image rebuild (Domain C rebuilds at initial-test regardless). | Gate 1 | §8.8.2 (patched) |
| 26.5 | `[x]` | Implement T26.7 — `agentVersion` includes a git commit short-hash **on top of** the Sprint-23.5 canonical `AGENT_VERSION` in `agent/config/settings.py`. **Read `settings.AGENT_VERSION` and append `+<short-sha>` — never hardcode a base in code** (that would re-split the source of truth consolidated in 23.5 Track 3). Bump the canonical base **once** to `0.5.0-sprint26` at its home in `settings.py`. Short-sha set via build-time env var (`AGENT_GIT_COMMIT_SHORT_SHA`, read in `settings.py`). Result: `0.5.0-sprint26+<short-sha>`; the sha carries the real build identity, the base is a human label. Surfaced in SessionResult + the health response. (The relocation half moved to Sprint 23.5 Track 3 / 23.5.12.) | Gate 1 | §8.7.2 (patched) |
| 26.6 | `[x]` | Implement T26.2 (partial), **expanded** — wrap every agent vault-read call at the **tools layer** with `utils/retry.retry_on_transient(...)` (same helper Gold uses, Phase 9.5 Stage B Item 1b). Nine functions across four modules: `market_tools` (`fetch_latest`, `fetch_time_series`, `fetch_fred_anomalies`, `find_polymarket_market_by_question`), `knowledge_tools` (`similarity_search`, `fetch_full_text`), `social_tools` (`similarity_search`, `fetch_raw_comments`), `mapping_tools` (`lookup_by_canonical`). Wrapping at the tools layer (not in the agents) is the single chokepoint per service isolation (CLAUDE.md §3.3) and covers every call site automatically. No agent business-logic change. Rationale for the momentum→all-reads expansion: §2 / §3. | Gate 1, Gate 2 | §8.7.5 |
| 26.11 | `[x]` | **Delivery-path hardening (closes KG-B-18) — retarget step 6 + backstop.** Root cause: step 6 marks the WRONG queue doc on resume-on-clarify (the original session doc, not the fresh-UUID doc the run actually processed — see §2). **Root fix:** add a single-writer `query_doc_id` field to `ForecastState`; set it in `process_query._build_initial_state` in both paths (first-time: `== session_id`; resume: the fresh-UUID doc id already passed in as `query_doc_id`); change `write_to_firestore` step 6 from `update_query_status(session_id, "done")` to `update_query_status(query_doc_id, "done")`. **Layer 2 (retained backstop):** guard `process_query._mark_failed` to read current session status and refuse to downgrade a session already at `done` (log + skip). **Layer 1 (retained as general hardening; no longer carries the test):** wrap step 6 in try/except — log WARNING and continue. **Test:** update `test_sprint21_gate3_tier2_resume_freeform_completes_forecast` — flip the final assertion `fq_fresh["status"]` from `"claimed"` to `"done"` (the fresh doc is now the one marked done), remove the `@pytest.mark.xfail`, and refresh the now-accurate "Sprint 26 fix" comments in the docstring/inline. Expected end-state on resume: fresh doc → `done`, original doc stays `awaiting_clarification`. (Numbered 26.11 — see numbering note below — but an implementation task, grouped with 26.2–26.6 above the gates.) | Gate 1, Gate 3 | §8.7.5, §8.8.1 |
| 26.8 | `[x]` | Gate 1 tests: unit tests for the new metrics assembly + `/metrics` exposition format (incl. the `write_to_firestore` duration `.observe()`); version-string assembly (reads `settings.AGENT_VERSION`, appends short-sha); retry wrapping across the four tools modules; **step 6 targets `query_doc_id`** (first-time == session_id; resume == fresh doc id); **Layer 2 guard (`_mark_failed` refuses to downgrade a `done` session)**. | Gate 1 | §9.3 Gate 1 |
| 26.9 | `[x]` | Gate 2 tests: subgraph tests for the surviving Sprint-26 changes (clarification cleanup, retry wrapping). | Gate 2 | §9.3 Gate 2 |
| 26.10 | `[x]` | Gate 3 tests: against the Firestore emulator on a real session run — verify clarification field cleanup, the `/metrics` endpoint, and the version string; **verify step 6 marks the processed doc (`query_doc_id`) `done` on both first-time and resume-on-clarify runs (fresh doc → `done`, original stays `awaiting_clarification`); verify Layer 1 keeps the session `done` if the queue write still errors; return `test_sprint21_gate3_tier2_resume_freeform_completes_forecast` to green with the xfail removed (KG-B-18 closed)**. | Gate 3 | §9.3 Gate 3 |

### Numbering note

Task IDs are non-sequential by design. The old 26.1 / 26.5-relocation-half / 26.7 /
26.10.5 slots are recorded as "moved" below and are **not** reused (reusing them would
collide with the references in §1 and §4). **26.11** is an implementation task added in
the 2026-07-16 review; it carries a high number to avoid reusing a "moved" slot, but it
belongs with the implementation tasks (26.2–26.6), above the gates (26.8–26.10). The
`T26.x` (spec-era) vs `26.x` (this table's row) naming is an inherited collision —
`T26.6` / `T26.7` / `T26.2` in the scope text are old spec IDs; the `26.x` rows are this
sprint's tasks.

### Moved to Sprint 23.5 (do not re-implement here)

- **old 26.1** (cost logging) → Sprint 23.5 **Track 2** (central `llm_cost.py` +
  4-site retrofit + `state.total_cost_usd`; rescoped KG-B-6).
- **the relocation half of old 26.5** (`AGENT_VERSION` → `config/settings.py`) →
  Sprint 23.5 **Track 3** (23.5.12). Sprint 26 (26.5 above) now only adds the git
  short-hash on top.
- **old 26.7** (wire `trigger_reactive_ingestion` into the graph) → Sprint 23.5
  **Track 1** (23.5.5, with `sufficiency_check` 23.5.1 + `_route_after_sufficiency`).
  Closed KG-B-15 / KG-B-16.
- **old 26.10.5** (E2E trigger-cycle-close verification) → Sprint 23.5 **23.5.7**
  (E2E split gate).

---

## §5 — Cross-sprint context

For the Phase-8 revision rationale, the implementation-order / dependency map, and
the spec sections this revision supersedes, see `hub_sprints.md` (Rationale /
Phase-8 Context section). Deferred work and the full Known Gaps (KG-B-*) table live
in `hub_sprints.md` §3/§4. How the hub actually works (graph, nodes, the Firestore
contract) is in `hub_agents.md`. The remediation that reshaped this sprint is the
closed `archive_plans/sprint23_5_pre26_remediation.md`.

**Constraint carried into Sprint 27.** The delivery-path invariant established by 26.11
— *no write after the `done`-flip may fail or reverse a delivered forecast* — must be
preserved by the Sprint 27 unified error handler (T27.3, implementing T26.1) — it wraps
every node and touches the same `_mark_failed` / node-failure path. The Layer 2 guard's
unit test (26.8) locks this in, but the T27.3 refactor must not regress it. (T27.2 is a
different task — inbound schema validation, KG-B-7.)

**Domain C dependency (26.5).** The hub side of the git-hash version is complete
and degrades gracefully (settings.py reads `AGENT_GIT_COMMIT_SHORT_SHA`; when unset
`AGENT_VERSION` is just the base `0.5.0-sprint26`). For the sha to actually stamp the
deployed build, the initial-test `anizai-agent` image rebuild must inject it:
`infrastructure/Dockerfile.agent` needs `ARG AGENT_GIT_COMMIT_SHORT_SHA` +
`ENV AGENT_GIT_COMMIT_SHORT_SHA=${AGENT_GIT_COMMIT_SHORT_SHA}`, and the build command
must pass `--build-arg AGENT_GIT_COMMIT_SHORT_SHA=$(git rev-parse --short HEAD)`.
Tracked with the Domain C rebuild backlog (project_master §3), not a Domain B code task.

**Deferred from Sprint 26 to Sprint 27.** The `agent_queue_depth` Prometheus gauge
(operational metric; needs a poll/listener update mechanism) and any latency
optimization flagged by the 26.3 analysis (fix-in-response, if the report finds real
regressions — may need to land before Phase 10's 100+-forecast load).
