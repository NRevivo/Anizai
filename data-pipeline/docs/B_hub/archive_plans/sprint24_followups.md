# sprint24_followups.md
> Domain: B — Agentic Hub
> Type: Sprint Plan
> Last updated: 2026-07-04
> TL;DR: The active, self-contained plan for **Sprint 24 — Follow-up Conversations
>        (Revised)**. Chat follow-ups on a completed forecast, answered **exclusively**
>        from the parent `SessionResult` + the top evidence already retrieved — no
>        escalation, no new vault search. Carries this cycle's preserved edits: the
>        **status-based** idempotency design (24.14, revised 2026-07-04 — user-message
>        status `sent → answered` + explicit `replyToMessageId` linkage, replacing the
>        fragile "later assistant reply" heuristic), the done-transition safety-net
>        sweep (24.15), the collection-group index deployment requirement (24.1), the
>        `FollowupState.total_cost_usd` cost field, and the ≤7s / 6s call-budget
>        design. Sprint 24 is the project's current NEXT task; no `agent/followup/`
>        files exist yet.

## Navigation
- §1 — Scope — what the follow-up path is and is not
- §2 — Cost-instrumentation acceptance criterion — "born instrumented" (23.5.11)
- §3 — Confirmed design decisions — including the preserved idempotency + cost edits
- §4 — Task table — 24.1–24.15, all `[x]` (implementation + Gate 1/2/3 green; E2E run for real locally — real gpt-4o-mini + emulator — PASS)
- §5 — Cross-sprint context — see hub_sprints.md (rationale, dependency map, deferred)

---

## §1 — Scope

Implement chat follow-ups on completed forecasts. **No escalation** — answers are
constructed exclusively from the parent SessionResult and the top evidence items
already retrieved during the original forecast. When the existing context is
insufficient, the agent returns a transparent message ("can't answer with high
confidence… improvements pending escalation enablement") rather than fabricating a
partial answer.

The original Sprint 24 plan (`agentic_hub_implementation.md`) included a branch in
`T24.4` that escalates to reactive search when the context is insufficient. This
revision implements only the simpler, cheaper "answer from context" path. The
escalation branch is reserved as a Future Enhancement (FE 2, see `hub_sprints.md`
§3) that connects cleanly to Sprint 23's `trigger_reactive_ingestion` node when
re-enabled.

**Blockers:** none (Sprints 22, 23, and 23.5 complete). **Next action:** Sprint 24
kickoff — no `agent/followup/` files exist yet.

---

## §2 — Cost-instrumentation acceptance criterion (added by Sprint 23.5 / 23.5.11)

- **Born instrumented.** `answer_from_context` (the new GPT-4o-mini follow-up node)
  **must** route its token usage through `agent/utils/llm_cost.py`
  (`record_usage` / `compute_cost`) and contribute to `FollowupState.total_cost_usd`
  from its **first commit** — never retrofitted. The cost layer already exists
  (Sprint 23.5 Track 2); a new LLM node that does not call it is incomplete.

---

## §3 — Confirmed design decisions

- **`T24.4` renamed to `answer_from_context.py`.** Single GPT-4o-mini call. System
  prompt explicitly forbids re-running the forecast, revising the probability, or
  speculating beyond the evidence already in context.
- **Insufficient-evidence response is transparent.** "Based on this forecast's
  evidence, I can't answer with high confidence. This will improve once expanded
  retrieval is enabled." The phrasing intentionally hints at the Future Enhancement
  (FE 2) without exposing internal mechanics to a real user.
- **Follow-ups never modify the parent SessionResult.** Reinforces the "One
  Question, One Thread" rule (spec §8.2.3).
- **Budget: 6s for the LLM call (`AGENT_FOLLOWUP_BUDGET_MS=6000`); end-to-end reply
  target ≤7s** including `load_context` + the Firestore write. The 6s value is the
  answer-node call budget; the extra ~1s is context-load + write/delivery overhead.
  Gate 3 (24.12) asserts the ≤7s end-to-end target so the test threshold and the
  call budget stay consistent. Tighter than the main forecast (~15–30s).
- **Timeout returns a complete message with a caveat, not a truncated reply.** Per
  handoff §6.1 / §7's degradation contract: on budget overrun the agent writes a
  full assistant message whose *content* carries an "I had to stop early" caveat and
  `status: "complete"` — the frontend never has to render a partial/streaming
  follow-up.
- **Follow-up processing is idempotent via a message-status transition (revised
  2026-07-04; supersedes the 2026-06-27 "later assistant reply" guard).** The
  server already writes `status: 'sent'` on every user message
  (server/src/repositories/session.repository.ts `addMessage`). The listener
  filters on `role=='user' AND status=='sent'`; when the agent answers, it writes
  the assistant message **and flips the user message's status to `'answered'` in
  the same batch**. The answered message thereby leaves the listener's filter set —
  exactly the `pending → done` pattern that protects the main `forecastQueries`
  path. Listener (re)attach then re-delivers **only genuinely unanswered**
  messages, which is correct recovery behavior (a crash mid-answer leaves the
  message `'sent'` and it is answered after restart), not a bug to guard against.
  Each assistant message also carries `replyToMessageId: <user message doc id>` —
  explicit linkage, so two back-to-back user questions are each answered
  independently and no time-ordering inference exists anywhere (the old heuristic
  silently skipped Q2 whenever the answer to Q1 landed after Q2 was written).
- **Done-transition safety-net sweep (added 2026-07-04).** The listener delivers
  each message event once; a follow-up written in the narrow window *before* the
  parent session flips to `done` would be delivered while the parent check fails
  and never re-delivered. Closure: when the session transitions to `done`
  (write_to_firestore step 5), sweep that session's `messages` for docs with
  `role=='user' AND status=='sent'` and process them (24.15). Early messages are
  caught by the sweep; later messages by the listener; no gap between the two.
- **Collection-group listener + index is a deployment item, not just code
  (added 2026-07-04).** Listening on `messages` across all sessions is a
  collection-group query; the parent `session.status=='done'` condition cannot be
  expressed in the query and is checked per-message inside the callback (one
  parent-doc read). The collection-group index/field-scope exemption required for
  the `role`/`status` filter works implicitly on the local emulator but MUST be
  explicitly deployed to production Firestore before the initial test — record it
  in the pre-test deployment checklist alongside the `reactive_triggers_log` DDL
  and the cumulative `anizai-agent` image rebuild.
- **Out-of-scope handling lives in the follow-up prompt (guardrail-lite, added
  2026-07-04).** The `answer_from_context` system prompt classifies as well as
  answers: questions that are off-topic for the parent forecast, request forbidden
  actions (re-run, revise probability, fetch new evidence), or are otherwise
  chat-abuse get a fixed polite redirect message instead of an attempted answer,
  and the classification is logged. Zero extra LLM calls, zero added latency. A
  dedicated pre-LLM filter layer is consciously deferred — see the Deferred Items
  entry in `hub_sprints.md` §3 (decision 2026-07-04).
- **`FollowupState` carries cost (added 2026-06-27).** `FollowupState` includes
  `total_cost_usd: float` so `answer_from_context` can satisfy the Sprint 23.5 /
  23.5.11 "born instrumented" criterion — the accumulator the acceptance criterion
  refers to must actually exist on the state.
- **Complete-message responses, no streaming.** Token-by-token streaming deferred
  (handoff doc §7).

### Build resolutions — ratified 2026-07-04 (advisor↔Ron review of Claude Code's first implementation-plan draft)

Final answers to the open questions Claude Code raised, plus refinements surfaced in
review. These are binding for the Sprint-24 build; do not re-litigate.

- **Sweep hook (24.15) → Option A, done-guarded.** A followup-owned sweep is called
  from `process_query` **after a successful `graph.invoke()` return**. `process_query`
  is the runner, not a graph node, so the main graph stays decoupled from the followup
  package (satisfies the 24.15 "without coupling" constraint), and the sweep targets
  exactly the just-completed session. **Guard (required):** the sweep first confirms the
  session reached `status=='done'` before sweeping — a successful invoke can terminate
  in `awaiting_clarification` (the clarification branch), which has no forecast and must
  not be swept. The claim-race loser returns before invoke, so it never sweeps. (Verified
  2026-07-04: `process_query` is a pure lifecycle wrapper around `graph.invoke()`.)
- **Idempotency is an ATOMIC claim (refines 24.14 / 24.5).** The sweep and the listener
  are two concurrent processors that can both read a message as `sent` and both answer
  it — the plain read-then-batch-write in 24.5 / 24.14 is not race-safe against that.
  Implement the claim as a **transactional conditional flip `sent → answered`** (only
  the winner writes the assistant reply), mirroring `claim_session`'s atomic claim on the
  main path. Use **no** intermediate `processing` state — flip straight `sent → answered`.
- **Cross-boundary status surface = one new user value (`answered`).** The only new
  message status the frontend/BFF must tolerate on **user** messages is `answered`.
  Assistant messages carry no frontend-facing status: the timeout/degradation signal
  rides in message **content** (the caveat), per the timeout bullet above — if a
  `status:"complete"` marker is stamped on an assistant message it is internal and the
  frontend need not key on it.
- **Budget (refines 24.9) → local deadline.** A local wall-clock deadline + a per-call
  timeout on the LLM call. No shared/global budget module (none exists at the agent
  root); `FollowupState` carries no budget object.
- **agentEvents is OUT of Sprint 24.** Emitting agentEvents (`followup_started` /
  `context_loaded` / `followup_response_complete`) is Sprint 25 (T25.7). The followup
  nodes are built here WITHOUT agentEvents emission.
- **`FollowupState` full field set (8 fields; trigger id + content added 2026-07-04).**
  `state.py` implements the complete ratified field set: `parent_session_id`,
  `message_history`, `parent_session_result`, `parent_evidence` [top 5], `response_text`,
  `total_cost_usd`, plus **`trigger_message_id: str`** and **`trigger_question: str`** —
  the doc id and content of the specific user message this run answers, both set by the
  listener/sweep when it builds the initial state (the message doc is already in hand, so
  the content costs zero extra reads). **The triggering message is identified by
  `trigger_message_id` everywhere — the atomic claim, the `sent → answered` flip, and
  `replyToMessageId` — and `answer_from_context` answers `trigger_question`, never
  `message_history[-1]` / positional.** Positional resolution reintroduces the exact
  time-ordering bug 24.14 removed (two rapid `sent` messages would both be answered as
  the newest).
- **Second listener drain.** The followup listener honors the worker's `_shutdown_event`
  drain and unsubscribes symmetrically with the main listener (24.8).
- **CG index is a deploy item in an EXISTING partner-side repo file (reinforces 24.1;
  re-corrected 2026-07-04).** The `messages` collection-group query needs a composite
  collection-group index. The Firestore config lives IN the repo at
  `server/firebase/firestore.indexes.json` (BFF/partner side — NOT `data-pipeline`, so
  Claude Code does not edit it; an earlier note here wrongly claimed no such file exists
  — it does, and it already carries a `COLLECTION_GROUP` index for `evidence`, the exact
  template). The needed entry:
  `{ "collectionGroup": "messages", "queryScope": "COLLECTION_GROUP", "fields": [ {"fieldPath":"role","order":"ASCENDING"}, {"fieldPath":"status","order":"ASCENDING"} ] }`
  The partner adds this to `server/firebase/firestore.indexes.json` and runs
  `firebase deploy --only firestore:indexes` (anizai-ai project). Emulators ignore
  composite-index requirements, so Gate 3 passes without it; production silently returns
  nothing if absent. Also captured in the partner contract
  `Claude-anizai-docs\frontend_partner\sprint24_followups_frontend_contract.md`; the
  backlog row already exists in `C_cloud/cloud_sprints.md §2` — do not add a duplicate.
- **Input-lock is the practical one-at-a-time guard, partner-side.** The ratified
  back-to-back behavior (24.13 / 24.14 — both messages answered, `replyToMessageId`
  linked) stands as the hub safety-net; the primary guarantee that a user cannot fire a
  second follow-up while one is in flight is a frontend/BFF input-lock (the hub cannot
  prevent a send — it only reacts to an already-written message).
- **Cross-boundary contract → partner.** The frontend input-lock, the `answered` status
  value, `replyToMessageId` passthrough, the CG index, and `messages` security rules are
  documented for the frontend/BFF partner in
  `Claude-anizai-docs\frontend_partner\sprint24_followups_frontend_contract.md`.
  Implementation proceeds on the ratified design and does **not** block on partner
  sign-off (resolves Claude Code OQ2).

### Design Rationale Log — Sprint 24 (from the Phase-8 revised plan)

- **Escalation path explicitly deferred, not deleted.** Future Enhancement 2 is the
  live target for re-adding it. Decision made because escalation effectively
  duplicates Sprint 23's work in a different context — building both during this
  phase would touch overlapping code twice. Better to ship Sprint 23 (main-forecast
  escalation) first, see how it behaves, then port the pattern to follow-ups.
- **Transparent insufficient-evidence message hints at future capability.** "This
  will improve once expanded retrieval is enabled" signals that the system has a
  known limitation rather than an inability — important for user perception even
  though no external user sees this in initial test.

---

## §4 — Task table

| Task | Status | Description | Gate(s) | Spec Reference |
|------|--------|-------------|---------|----------------|
| 24.1 | `[x]` | Implement `agent/followup/listener.py` — second Firestore listener: **collection-group query** on `messages` where `role == 'user'` AND `status == 'sent'`. The parent `session.status == 'done'` condition is NOT expressible in the query — check it per-message inside the callback via one parent-doc read; if the parent is not `done`, skip (the 24.15 sweep catches these later). Triggers the follow-up subgraph. **Deployment note:** the collection-group index this query needs is implicit on the emulator but must be explicitly deployed to production Firestore before the initial test (record in the pre-test deployment checklist). Idempotency comes from the status filter itself — see 24.14. Shutdown/drain semantics for the second Watch must be defined alongside 24.8. | Gate 1 | §8.8.3 (revised) |
| 24.2 | `[x]` | Implement `agent/followup/state.py` — `FollowupState` TypedDict: `parent_session_id`, **`trigger_message_id`**, **`trigger_question`**, `message_history`, `parent_session_result`, `parent_evidence` (top 5), `response_text`, **`total_cost_usd: float`** (the cost accumulator the 23.5.11 "born instrumented" criterion writes into; see 24.4). `trigger_message_id`/`trigger_question` are the doc id + content of the specific user message this run answers (set by the listener/sweep; `trigger_message_id` drives the atomic claim / `sent → answered` flip / `replyToMessageId`, and `answer_from_context` answers `trigger_question`, never `message_history[-1]` — see §3 Build resolutions). (Removed from original plan: `needs_escalation`, `escalation_results`.) | Gate 1 | §8.8.3 (revised) |
| 24.3 | `[x]` | Implement `agent/followup/nodes/load_context.py` — loads parent SessionResult, top 5 evidence items by rank, last 10 messages from history. | Gate 1 | §8.8.3 (revised) |
| 24.4 | `[x]` | Implement `agent/followup/nodes/answer_from_context.py` (revised T24.4) — GPT-4o-mini call. System prompt explicitly forbids re-running the forecast, revising probability, or fetching new evidence. On insufficient evidence, returns the transparent message. **Born instrumented (23.5.11): route token usage through `agent/utils/llm_cost.py` (`record_usage` / `compute_cost`) and accumulate into `FollowupState.total_cost_usd` from the first commit.** | Gate 1, Gate 2 | §8.8.3 (revised) |
| 24.5 | `[x]` | Implement `agent/followup/nodes/write_message.py` — writes assistant message to `messages` subcollection with `role: "assistant"` and `replyToMessageId: <user message doc id>`, **and in the same Firestore batch flips the answered user message's `status` from `'sent'` to `'answered'`** (the transition that removes it from the listener's filter set — see 24.14). | Gate 1 | §8.8.3 (revised) |
| 24.6 | `[x]` | Implement `agent/followup/graph.py` — small LangGraph: `load_context → answer_from_context → write_message`. No escalation branch. | Gate 2 | §8.8.3 (revised) |
| 24.7 | `[x]` | Implement `agent/prompts/followup.py` — system prompt encoding the no-revise / no-fetch / transparent-insufficiency constraints, **plus out-of-scope classification (guardrail-lite, 2026-07-04)**: off-topic questions, forbidden-action requests (re-run / revise probability / fetch new evidence), or chat abuse receive a fixed polite redirect instead of an attempted answer; the classification is logged. Same single LLM call — no added latency. | Gate 1 | §8.8.3 (revised) |
| 24.8 | `[x]` | Update `agent/worker.py` — initialize the follow-up listener alongside the main listener. Both run concurrently. | Gate 1 | §8.8.1 |
| 24.9 | `[x]` | Implement budget enforcement (`AGENT_FOLLOWUP_BUDGET_MS=6000` — the answer-node call budget). On overrun, write a **complete** assistant message whose content carries a degradation caveat with `status: "complete"` (per handoff §6.1 — not a truncated reply). End-to-end reply target ≤7s (6s call budget + context-load/write overhead), consistent with the 24.12 Gate 3 threshold. | Gate 1 | §8.8.3 (revised) |
| 24.10 | `[x]` | Gate 1 tests: unit tests for each follow-up node (mocked LLM, mocked parent context loading). `test_sprint24_gate1.py` + `test_sprint24_listener.py` — 35 unit tests green (2026-07-04). | Gate 1 | §9.3 Gate 1 |
| 24.11 | `[x]` | Gate 2 tests: integration test of follow-up graph. Both branches: sufficient context (real answer) and insufficient context (transparent message). `test_sprint24_gate2.py` — 3 graph-integration tests green (2026-07-04). | Gate 2 | §9.3 Gate 2 |
| 24.12 | `[x]` | Gate 3 tests: against Firestore emulator. Submit a forecast, wait for done, submit a follow-up message, verify assistant reply appears within the ≤7s end-to-end target (consistent with the 6s call budget + overhead per 24.9). `test_sprint24_gate3.py::test_gate3_followup_round_trip` — 4/4 Gate 3 tests green on the emulator (2026-07-04). | Gate 3 | §9.3 Gate 3 |
| 24.13 | `[x]` | E2E test: real environment. Run a forecast, then ask three follow-ups (two clearly answerable from context, one requiring evidence the agent didn't retrieve). Verify the third gets the transparent message. Additionally send two follow-ups back-to-back and verify both are answered with correct `replyToMessageId` linkage. **Standalone runner `tests/e2e/sprint24_e2e_run.py` — RUN FOR REAL LOCALLY (real gpt-4o-mini + Firestore emulator), PASS (2026-07-04).** Both answerable follow-ups answered; the 3rd classified `insufficient_evidence` via the real LLM and returned the transparent message; back-to-back pair both answered with distinct/correct `replyToMessageId`. Per-call gpt-4o-mini latency (n=5): min 1667ms / median 2514ms / max 4109ms; `AGENT_FOLLOWUP_BUDGET_MS=6000` timeout fired on 0/5 calls; ~$0.0013 total. Decoupled from the initial test. | E2E | §9.3 E2E |
| 24.14 | `[x]` | **Status-based idempotency (revised 2026-07-04; supersedes the "later assistant reply" guard).** Each user message is answered exactly once via a status transition, mirroring the main path's `pending → done`: the listener filters `status=='sent'` (24.1); answering flips the message to `'answered'` in the same batch as the assistant write (24.5); the message leaves the filter set and is never re-delivered. Re-attach/restart re-delivers only genuinely unanswered (`'sent'`) messages — correct recovery, not double-answering. `replyToMessageId` gives explicit answer→question linkage so back-to-back questions are each answered independently (the superseded time-ordering heuristic silently dropped Q2 when Q1's answer landed after Q2). Verify: (a) no double-answers across a simulated listener re-attach / worker restart; (b) two rapid consecutive user messages both get answers, each linked to the right question. | Gate 1, Gate 3 | §8.8.1 |
| 24.15 | `[x]` | **Done-transition safety-net sweep (added 2026-07-04).** When a session transitions to `done`, sweep that session's `messages` subcollection for `role=='user' AND status=='sent'` docs and process them through the follow-up subgraph. Closes the one-time-delivery gap: a message written just before the `done` flip is delivered while the parent check fails and never re-delivered by the listener — the sweep catches it. Hook at the natural seam where the follow-up path learns of the `done` transition (implementation decides exact placement relative to `write_to_firestore` step 5 without coupling the main graph to the followup package). Gate 3: write a user message while the session is still `running`, let the forecast complete, verify the message gets answered. | Gate 1, Gate 3 | §8.8.1 |

---

## §5 — Cross-sprint context

For the Phase-8 revision rationale, the implementation-order / dependency map
(24→25→26→initial test→27 with blockers), and the spec sections this revision
supersedes, see `hub_sprints.md` (Rationale / Phase-8 Context section). Deferred
work (incl. follow-up escalation, FE 2) and Known Gaps live in `hub_sprints.md`
§3/§4. How the hub actually works (graph, nodes, the Firestore contract) is in
`hub_agents.md`.
