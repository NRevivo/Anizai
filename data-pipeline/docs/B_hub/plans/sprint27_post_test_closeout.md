# sprint27_post_test_closeout.md
> Domain: B — Agentic Hub
> Type: Sprint Plan
> Last updated: 2026-07-26
> TL;DR: The **partial / stub** plan for **Sprint 27 — Post-Test Polish + Phase 8
>        Closeout**. Picks up after the two-day initial test: everything deferred
>        from the original Sprint 26 plan, plus tasks surfaced by the test itself,
>        then closes Phase 8 with a State Ledger. Tasks 27.1–27.11 + 27.13 are
>        **known today**; tasks 27.12+ are **defined post-test**. Gated on the
>        initial test (which is gated on Sprint 26).

## Navigation
- §1 — Scope (partial / stub)
- §2 — Confirmed design decisions
- §3 — Design Rationale Log — Sprint 27 (from the Phase-8 revised plan + the 2026-07-16 review)
- §4 — Task table (known today: 27.1–27.11, 27.13; 27.12+ post-test)
- §5 — Cross-sprint context — see hub_sprints.md

> **2026-07-16 review note.** This plan dates from 2026-06-28. An Advisor↔Ron review
> on 2026-07-16 (the same one that reconciled Sprint 26) aligned it to the
> post-Sprint-25 codebase and to the inflows created by the Sprint 26 decisions:
> the 27.10 load-test NFR was corrected to the relaxed ≤60s p95 (KG-B-5); the
> `agent_queue_depth` gauge deferred out of Sprint 26 was given a home (27.13); the
> delivery-path invariant from 26.11 was recorded as a constraint the unified error
> handler (27.3) must preserve; the `AGENT_REACTIVE_*` dead-config cleanup (KG-B-10)
> was framed as verify-then-delete with a live-sibling caveat (27.11); and the
> Firestore-retry task (27.4) was noted not to regress 26.11's Layer 1. This remains
> a partial/stub plan — 27.12+ are still defined post-test.

---

## §1 — Scope (partial / stub)

> **This is a partial/stub plan.** Tasks 27.1–27.11 and 27.13 are known today. Tasks
> 27.12+ are **defined post-test** — they receive new entries based on initial-test
> findings (e.g., performance optimization per the KG-B-5 latency analysis if it
> showed real regressions, or specific error-handler enhancements tuned to the
> actual failure modes seen in production).

Pick up after the two-day initial test. Address everything deferred from the
original Sprint 26 plan plus anything surfaced by the initial test itself. Close
Phase 8 with a State Ledger.

**Blockers:** the initial test (which is gated on Sprint 26). **Next action:**
post-test; scope is partially defined now, partially after the test.

**Deliberately NOT pulled into Sprint 27 (conscious calls, not omissions).**
- **KG-B-1** (Polymarket `key_arguments_pro/con` return `[]`) — needs a `gold_job`
  change (a consensus-prompt enhancement), i.e. Domain A / Phase 7 work, not a hub
  task. Stays in the KG table.
- **KG-B-2 / KG-B-3** (spec ↔ server reconciliation; result-timestamp field-name
  drift) — post-V1 reconciliation, mitigated today (server reads the top-level shape;
  all three timestamp fields are written). Not gating anything pre- or post-test.

---

## §2 — Confirmed design decisions

- **Sprint 27 scope is partially defined now, partially after initial test.** Tasks
  27.1–27.11 and 27.13 are known today. Tasks 27.12+ will be added based on
  initial-test findings.
- **Firestore retry wrapper is separate from Postgres retry.** Postgres has a
  working helper (`utils/retry.retry_on_transient`), and Sprint 26 (26.6) wraps every
  agent *read* call site with it at the tools layer. Sprint 27's 27.4 adds the
  *Firestore* side — a similar pattern for `firestore_client.py` *write* paths, tuned
  for `google.api_core.exceptions` transient classes. The two are complementary
  (Postgres reads vs Firestore writes).
- **The delivery-path invariant from Sprint 26 (26.11) must survive Sprint 27.** 26.11
  established: *no write after the `done`-flip may fail or reverse a delivered
  forecast* — via `write_to_firestore` step 6 best-effort (Layer 1) + a
  `_mark_failed` no-downgrade guard (Layer 2). The unified error handler (27.3) reworks
  the node-failure path, and the Firestore-retry wrapper (27.4) touches the same write
  paths, so both must preserve this invariant — see 27.3 / 27.4. The Layer 2 unit test
  (Sprint 26 26.8) locks it in; the 27.3 refactor must not regress it.
- **`agent_queue_depth` is a defined piece of work, not a test finding.** It was
  scoped and deferred out of Sprint 26 (26.4) — the gauge and its update mechanism are
  known now — so it lands as a numbered task (27.13), not in the post-test 27.12+
  bucket.

---

## §3 — Design Rationale Log — Sprint 27 (from the Phase-8 revised plan + the 2026-07-16 review)

- **Partially defined now, partially after initial test.** Pre-defining all of
  Sprint 27 would risk addressing problems that don't exist. Leaving space for new
  tasks (27.12+) means real findings from the initial test get the implementation
  effort, not speculative ones.

### Added / revised in the 2026-07-16 pre-Sprint-26 review

- **27.10 load-test NFR corrected to ≤60s p95.** The original assertion (`p95 < 30s`)
  predates the 2026-07-04 product decision that relaxed the main-forecast NFR to ≤60s
  p95 (KG-B-5 — the 30s target is retired; a 30–60s wait on the main forecast is
  acceptable-to-desirable UX, reinforced by the Sprint-25 reasoning panel). Updated to
  `p95 < 60s`.
- **`agent_queue_depth` gauge given a home (27.13).** Deferred out of Sprint 26 (26.4)
  as an operational metric not worth its new update mechanism for a 3–4-user / 2-day
  test. It is defined work, so it is a numbered task here, not a post-test placeholder.
- **Delivery-path invariant recorded as a 27.3 constraint.** 26.11's Layer 2 guard
  (`_mark_failed` refuses to downgrade a `done` session) must survive the unified
  error handler's rework of the failure path. Reciprocal of the constraint noted in
  Sprint 26 §5.
- **27.4 must not regress 26.11 Layer 1.** Wrapping `firestore_client` writes in a
  Firestore-retry helper must not turn step 6 back into a hard-fail — step 6 is
  intentionally best-effort (a retry that exhausts and still fails is swallowed by
  Layer 1's try/except, keeping the delivered forecast `done`).
- **KG-B-10 reframed as verify-then-delete (27.11).** The web-search microservice
  (FE 1) was formally retired, so KG-B-10's close-condition is already met. Rather than
  hardcode a deletion into the plan, 27.11 instructs a per-variable consumer audit
  (grep) at implementation time, deleting only the dead vars — with a hard caveat to
  preserve `AGENT_REACTIVE_DEFAULT_WINDOW_DAYS`, which is **live** (consumed by
  `trigger_reactive_ingestion` as the 7-day recency window). `AGENT_REACTIVE_MAX_PER_SESSION`
  is confirmed dead (closes KG-B-10); the other siblings are audited in the same sweep.

---

## §4 — Task table (known today: 27.1–27.11, 27.13; 27.12+ post-test)

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 27.1 | `[ ]` | Close KG-B-4 (Origin KG-PHASE8-7) — replace `logging.basicConfig()` in `agent/worker.py` with the standard `setup_logging()` from `utils/logging_config.py`. **REVISED 2026-07-26 (cloud finding) — the swap alone does NOT fix the symptom; done naively it formalizes it.** `setup_logging()` is what installs `_SampledInfoFilter`, which passes INFO at `LOG_INFO_SAMPLE_RATE` (default **0.01**). The cloud agent already runs that filter today — not via `worker.py` but via an import-time `setup_logging()` call reached through `trigger_reactive_ingestion.py` → `graph.py`, and the function is idempotent/first-caller-wins — so ~99% of INFO, including the `llm_usage` cost lines, never reaches Cloud Logging (verified on the 2026-07-25/26 agent-only run: 7 log entries across ~20h). The 1% policy was written for a pipeline at ~100 msg/s; the agent handles single-digit forecasts per hour. **This task must therefore also exempt the agent from INFO sampling** — either `LOG_INFO_SAMPLE_RATE=1.0` on the `agent-worker` Deployment env (read at module import, so it needs a fresh pod) or an explicit agent-side bypass of the filter. Acceptance is not "basicConfig removed" but "an `llm_usage` INFO line is queryable in Cloud Logging for every forecast". See `docs/B_hub/agent_cloud_run_20260726.md` §3 finding 2 and `docs/guides/bringup_profiles.md` §5 trap 3. | — | §8.8.2 |
| 27.2 | `[ ]` | Close KG-B-7 (Origin KG-PHASE8-15) — **inbound schema validation** on `forecastQueries` before `claim_session` reads `question`. Raise typed `MalformedQueryError` instead of `KeyError`. (Distinct from the unified error handler in 27.3 — this is input validation at the queue boundary.) | Gate 1 | §8.8.1 |
| 27.3 | `[ ]` | Implement T26.1 — unified `agent/error_handler.py`. Wraps every node. Categorizes exceptions as retryable / non-retryable / escalate. Hooked in based on real failure modes seen in the initial test. **Constraint (from Sprint 26 26.11):** must preserve the delivery-path invariant — the `_mark_failed` no-downgrade guard (a session already at `done` is never flipped to `failed`). The Layer 2 unit test (Sprint 26 26.8) locks this in; this refactor must not regress it. | Gate 1, Gate 2 | §8.7.5 |
| 27.4 | `[ ]` | Implement T26.2 (completion) — Firestore retry wrapper. Similar pattern to `utils/retry.retry_on_transient` but tuned for `google.api_core.exceptions.ServiceUnavailable`, `DeadlineExceeded`, etc. Wraps `firestore_client.py` write paths. **Must not regress Sprint 26 26.11 Layer 1:** `write_to_firestore` step 6 is intentionally best-effort — a retry that exhausts and still fails is swallowed there, leaving the delivered forecast `done`. Do not convert step 6 into a hard-fail. | Gate 1 | §8.7.5 |
| 27.5 | `[ ]` | Implement T26.3 — stress test claim atomicity. 100 `forecastQueries` docs submitted simultaneously, 3 concurrent workers. Verify exactly-once. | Gate 3 | §8.8.1 |
| 27.6 | `[ ]` | Implement T26.4 — worker restart resilience. Kill worker mid-session, verify re-claim after `AGENT_CLAIM_TIMEOUT_SECONDS`. **CAVEAT ADDED 2026-07-26 (KG-B-21) — verify the reaper exists before writing the test.** This task assumes something scans `forecastQueries` at `status=='claimed'` and re-claims on timeout. Evidence says it may not: two `e2e-sprint21-resume-*` docs have sat at `claimed` since 2026-05-05 and never self-cleared, and the main listener's query is `status=='pending'` only. If `AGENT_CLAIM_TIMEOUT_SECONDS` has no enforcing consumer, then re-claim is unbuilt rather than broken, this task is a **build** not a test, and a test written first will read as a regression instead of an original gap. Audit the consumer first; if absent, scope the reaper (a periodic sweep of `claimed` past the timeout back to `pending`) as part of this task. | Gate 3 | §8.8.1 |
| 27.7 | `[ ]` | Implement T26.5 — structured JSON logging. Every node entry/exit, every external call, every state transition. | Gate 1 | §7.2 of pipeline_core |
| 27.8 | `[ ]` | Implement T26.8 — graceful shutdown on SIGTERM. Finish all claimed sessions before exiting. | Gate 1 | §8.8.1 (patched) |
| 27.9 | `[ ]` | Implement T26.9 — edge case tests: empty vault, vault returns malformed data, OpenAI returns invalid JSON, Firestore transient unavailability, plan limit hit mid-claim. | Gate 1, Gate 2 | §9.3 |
| 27.10 | `[ ]` | Implement T26.10 — load test E2E. 50 sessions over 10 minutes. Verify **p95 latency < 60s** (updated from `< 30s` per the KG-B-5 NFR relaxation, 2026-07-04), error rate < 2%, cost within budget. | E2E | §9.3 E2E |
| 27.11 | `[ ]` | Implement T26.11 — documentation pass: docstrings, `prompts/README.md`, `.env.example`. **Includes the `AGENT_REACTIVE_*` dead-config sweep (closes KG-B-10) — verify-then-delete:** grep each variable in the block for live consumers, delete only those with zero. `AGENT_REACTIVE_MAX_PER_SESSION` is confirmed dead (the live producer-trigger path uses `AGENT_REACTIVE_TRIGGER_MAX_PER_SESSION` instead) → delete + close KG-B-10; audit `AGENT_REACTIVE_SEARCH_ENABLED` / `AGENT_REACTIVE_TIMEOUT_MS` / `AGENT_REACTIVE_MAX_ARTICLES` and delete any with no consumers. **Must preserve `AGENT_REACTIVE_DEFAULT_WINDOW_DAYS`** — it is live (consumed by `trigger_reactive_ingestion` as the 7-day recency window). Update `.env.example` accordingly. | — | §5.4 |
| 27.12+ | `[ ]` | New tasks added based on initial-test findings (**placeholder — defined post-test**). E.g. performance optimization if the 26.3 latency analysis flagged real regressions (may need to land before Phase 10's 100+-forecast load); error-handler enhancements tuned to observed failure modes. | — | — |
| 27.13 | `[ ]` | **`agent_queue_depth` Prometheus gauge (deferred from Sprint 26 26.4).** Add the gauge to the agent's `/metrics` endpoint (same `prometheus_client` exposition landed in 26.4). Value = count of `forecastQueries where status=='pending'`, kept current via an update mechanism (periodic poll or a listener callback — the implementer picks based on the worker's listener structure). Operational metric for queue backpressure; not on any node's critical path. | Gate 1 | §8.8.2 (patched) |
| 27.last | `[ ]` | Phase 8 closeout — generate Phase 8 State Ledger covering all sprints. Move all sprint sections to `task_plan_archive.md`. Collapse `task_plan.md` to summary keywords. | — | sprint-closeout skill |

### Numbering note

The `T26.x` identifiers inside the 27.3–27.11 descriptions are **spec-era IDs** for the
original Sprint 26 tasks that were moved to Sprint 27 in the 2026-05-23 revision (e.g.
"T26.1" = the unified error handler, now task-row 27.3). They are not this table's row
numbers. **27.13** is a task added in the 2026-07-16 review; it carries a number above
the `27.12+` post-test placeholder so it doesn't collide with post-test entries, but it
is a known-today implementation task (the queue-depth gauge deferred out of Sprint 26).

---

## §5 — Cross-sprint context

For the Phase-8 revision rationale, the implementation-order / dependency map, and
the spec sections this revision supersedes, see `hub_sprints.md` (Rationale /
Phase-8 Context section). Deferred work and Known Gaps (KG-B-*) live in
`hub_sprints.md` §3/§4. How the hub actually works (graph, nodes, the Firestore
contract) is in `hub_agents.md`.

**Inflows from Sprint 26 (2026-07-16 review):**
- **Delivery-path invariant (26.11 → 27.3).** The `_mark_failed` no-downgrade guard and
  step-6 best-effort behavior must be preserved by the unified error handler (27.3) and
  the Firestore-retry wrapper (27.4). Locked by the Sprint 26 26.8 Layer 2 unit test.
- **`agent_queue_depth` gauge (26.4 → 27.13).** Deferred out of Sprint 26; lands here as
  a numbered task on the same `/metrics` exposition.
- **Latency optimization (26.3 → 27.12+).** Remains conditional on the 26.3 per-node
  analysis + initial-test findings; stays in the post-test bucket, and may need to land
  before Phase 10's 100+-forecast load if the analysis flagged real regressions.
