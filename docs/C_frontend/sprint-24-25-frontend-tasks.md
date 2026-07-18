# Anizai · Frontend / BFF Tasks · Sprint 24 + 25 — **AS-BUILT**
> Domain: C — Frontend / BFF
> Type: Sprint Record
> Last updated: 2026-07-15
> TL;DR: The as-built record of Sprint 24 + 25 (T0–T8) — what was actually changed, the real file paths, and how each item was verified. Open this for the detail behind the Sprint 24+25 row in `frontend_sprints.md §1`.

> **Status: live, not archived.** This is the current as-built record for the most
> recent sprint. Summary status → `frontend_sprints.md §1`. Specs → `frontend_api.md`,
> `frontend_contracts.md`, `frontend_ui.md`.

Sources: `sprint24_followups_frontend_contract.md` + `sprint25_frontend_contract.md`
Owner: Noam (`client/`, `server/`) + shared Firestore project `anizai-ai`

> **This is an as-built record.** The original planning doc was written before we
> read the code and marked several already-built tasks as open. This version
> records what was actually changed, the real file paths, and how each item was
> verified. Task IDs (T0–T8) are preserved for traceability. Status legend:
> **DONE** (built this sprint) · **DONE (pre-existing)** (already built before
> this sprint, confirmed working) · **OPEN** (still outstanding — see Open items).

Recommended original order was `T0 → T1,T2 → T3,T4,T5 → T6 → T7,T8`. All
frontend/BFF code work is complete and both Firestore deploys (rules + indexes)
have landed on `anizai-ai`. The only remaining items are one upstream pipeline
dependency (the hub's Sprint 25) and one scoped dead-code cleanup — see Open
items.

---

## Phase 1 · The big blocker

### T0 · KG-B-9 · Real Firestore reads + `currentRunId` — **DONE (listeners pre-existing; `currentRunId` added)**
- **Reality found:** the real-time listeners already existed — `subscribeToSession`,
  `subscribeToSessionMessages`, `subscribeToAgentEvents` in
  `client/src/services/session.service.ts`, wired through `App.tsx`. Demo fixtures
  were already removed. So the doc's "switch from demo data" was largely already
  done; the genuinely missing piece was the new `currentRunId` field (needed by T6
  Rule B).
- **Changed:**
  - `client/src/services/session.service.ts` — added `currentRunId: string | null`
    to `SessionDocData` and read it in `subscribeToSession`'s `onData` mapping.
  - `client/src/App.tsx` — added `activeSessionCurrentRunId` state, set from the
    session-doc snapshot (reset on session switch / logout). Read straight off the
    live doc, not the REST aggregate (which only refreshes on status transitions).
- **Verified:** client `tsc` clean; live browser check — logged-in session views
  render real data and update in real time (queued session observed transitioning
  in the dashboard). Mid-run-refresh repopulation mechanism traced (see Open item 1
  for its dependency).

---

## Phase 2 · Contract additions to types / BFF

### T1 · Add `'answered'` to message `status` — **DONE**
- **Changed:**
  - `server/src/services/sessions.service.ts` — `SessionMessage.status` union now
    `'sent' | 'failed' | 'answered' | null`.
  - `client/src/services/session.service.ts` — same union; `mapSessionMessageDoc`
    now passes `'answered'` through (previously collapsed unknown → `null`).
  - `client/src/types/index.ts` — `ChatMessage.status` union adds `'answered'`.
  - `client/src/App.tsx` — `toChatMessage` maps `'answered'`/`'failed'` distinctly
    instead of collapsing everything to `'sent'`.
- **Grounding:** hub flips the trigger message `sent → answered` in the same write
  batch as the assistant reply (`data-pipeline/agent/followup/nodes/write_message.py`).
- **Verified:** server `tsc` + Vitest (11/11); client `tsc`. UI does not render
  `'answered'` as pending/unsent.

### T2 · Surface `replyToMessageId` on assistant messages — **DONE**
- **Changed:**
  - `server/src/services/sessions.service.ts` — added `replyToMessageId?: string | null`
    to `SessionMessage`.
  - `server/src/repositories/session.repository.ts` — `mapMessageDoc` now reads
    `data.replyToMessageId ?? null` (previously dropped).
  - `client/src/services/session.service.ts` — same field + mapping.
- **Structural decision:** `replyToMessageId` is **top-level** on the assistant
  message doc, **not** nested in `meta`. Grounded in `write_message.py` (writes it
  top-level alongside `role`/`content`/`agentVersion`). This resolves the original
  doc's "field or meta" ambiguity in favor of top-level.
- **Verified:** server `tsc` + Vitest (`sessions.repository.test.ts`); client `tsc`.

---

## Phase 3 · Small client-side UI behaviors

### T3 · Send-lock · disable send while the session is busy — **DONE**
- **Reality found:** only half-built. `isAwaitingAssistantResponse` existed but was
  never used to gate the composer — only `isSendingMessage` (the in-flight POST)
  did. So a second message could be sent the instant the POST resolved, and there
  was **no lock at all during initial-forecast processing** (the doc's own body
  required that window too). This second gap was missed in the first plan draft and
  caught on re-review.
- **Changed:**
  - `client/src/pages/DashboardPage.tsx` — derives
    `isSendLocked = isSessionProcessing || isAwaitingAssistantResponse`, where
    `isSessionProcessing` is `status ∈ {queued, claimed, running}`. Passed to all
    three `ChatPanel` instances.
  - `client/src/components/ChatPanel.tsx` — `isSendDisabled = isSendingMessage || isSendLocked`
    gates `handleSend` (so Enter is blocked too), the send button, and the
    suggested-action chips. The text `Input` stays gated only on `isSendingMessage`,
    so the user can keep typing while locked (per contract).
- **Verified:** client `tsc`; **live browser (both windows):**
  - Window 1 (initial forecast, `queued` session): input enabled, send button
    disabled even with text typed.
  - Window 2 (unanswered follow-up on a `done` session): a real `sent` follow-up
    with the "Waiting for response" indicator showing — send button disabled with
    text typed, input still enabled.

### T4 · Follow-up "thinking" indicator — **DONE (indicator pre-existing; hardened)**
- **Reality found:** the "Waiting for response" bubble already existed in
  `ChatPanel.tsx`, driven by `isAwaitingAssistantResponse`. It was functionally
  correct even before this sprint, because the hub flips `sent → answered` in the
  same batch as the reply, so the old timestamp heuristic and the status check fire
  at the same instant.
- **Changed:** `client/src/App.tsx` — reworked `isAwaitingAssistantResponse` from a
  timestamp comparison to a status check: scan for the latest user message; awaiting
  iff its status is `'pending'` or `'sent'`. More legible, no clock-ordering
  reliance. (Depends on T1's `'answered'`.)
- **Verified:** client `tsc`; live browser — indicator shown for a `sent` follow-up
  (see T3 Window 2).

### T5 · Suggested action chips — **DONE (pre-existing)**
- **Reality found:** already fully built. `ChatPanel.tsx` renders chips from
  `suggestedActions` (derived in `DashboardPage.tsx` from `prediction.suggestedActions`,
  sliced to 3); `handleActionClick` sends `action.prompt` via the normal message
  path. BFF passthrough already in place.
- **Changed:** nothing functional. The chips did pick up T3's lock (now gated on
  `isSendDisabled`) so they can't fire while the session is busy.
- **Verified:** client `tsc`; visual in live dashboard.

---

## Phase 4 · The only genuinely new UI surface

### T6 · Reasoning panel · live stream from `sessions/{id}/agentEvents` — **DONE**
Two rules, both implemented.

**Event `status` vocabulary — canonical enum is ours.** The event status enum is
`pending | running | done | failed`, as defined on `AgentEvent` in
`client/src/types/index.ts` and rendered by `AgentEventsTimeline`. Ron's original
Sprint 25 contract used `in_progress` / `complete` for the same two states; the
hub is standardizing to emit **our** four values instead — it aligned to the
frontend enum, not the reverse — so there is **no mapping shim** on either side.
Do not (re)introduce `in_progress` / `complete` anywhere in the frontend.

**Rule A · live-only:**
- **Reality found:** the in-progress branch of `DashboardPage.tsx` showed the panel,
  but `Dashboard.tsx` (the `done`-state view) *also* rendered `<AgentEventsTimeline>`
  unconditionally — a latent contract break (invisible only because the pipeline
  emits no events yet). Additionally, the in-progress guard was `status !== 'done'`,
  which also covered `failed` / `awaiting_clarification`.
- **Changed:**
  - `client/src/components/Dashboard.tsx` — removed the `AgentEventsTimeline` import,
    props, and render entirely. The finished report never shows the panel (incl.
    re-opening an old `done` session).
  - `client/src/pages/DashboardPage.tsx` — the single remaining render is now guarded
    by `['queued','claimed','running'].includes(status)`, so the panel is strictly
    live-only. `failed` / `awaiting_clarification` keep their status panel but show
    no timeline.

**Rule B · render only the current run (`runId == session.currentRunId`):**
- **Changed:**
  - `client/src/types/index.ts` + `client/src/services/session.service.ts` — added
    `runId: string | null` to `AgentEvent` and read it in `mapAgentEventDoc`.
  - `client/src/lib/agentEvents.ts` **(new)** — pure helper `selectCurrentRunEvents(events, currentRunId)`:
    returns `[]` when `currentRunId` is null, otherwise filters to
    `runId === currentRunId` and sorts by `sequence` ascending.
  - `client/src/App.tsx` — `filteredAgentEvents` memo calls the helper and is passed
    to the dashboard.
- **Structural decision:** the filter is a pure, unit-tested helper (not an inline
  memo) and sorts by `sequence` **explicitly** rather than relying on the Firestore
  query order from `subscribeToAgentEvents`. Behavior-identical for real (already
  ordered) inputs, but the ordering guarantee is now self-contained and testable.
- **Verified:** client `tsc`; `client/src/lib/agentEvents.test.ts` **(new, 4/4)** —
  interleaved two-run out-of-order array → only current run, ordered by sequence;
  null `currentRunId` → `[]`; null/non-matching `runId` excluded; no-match → `[]`.
  Live browser — empty path confirmed (panel shows empty state when no live run).
  **Not verifiable locally:** the multi-`runId` filter against real pipeline data
  (pipeline emits no events yet — see Open item 2).

**Follow-up answers do not emit events** — the panel is for the main forecast only.

---

## Phase 5 · Shared Firestore deploy

### T7 · Collection-group index on `messages` — **DONE (deployed)**
- **Changed:** `server/firebase/firestore.indexes.json` — added the `messages`
  `COLLECTION_GROUP` index (`role` ASC, `status` ASC), matching the contract spec
  and the structural form of the existing `evidence` CG index.
- **Verified:** JSON shape confirmed programmatically (COLLECTION_GROUP, 2
  ASCENDING fields). **Deployed** to `anizai-ai` via
  `firebase deploy --only firestore:indexes` → "deployed indexes in
  firestore.indexes.json successfully"; the index is live. Ron's side confirmed
  the definition is a **verbatim match** to what the hub's cross-session follow-up
  listener filters on. The one remaining check — that the query actually returns
  results in production — is tied to the **agent-image deployment, not to any
  sprint**: the cloud is still running a Sprint ~21-era image (see the cloud-state
  caveat below), so no code is up there yet that would run this query. Sprint 25's
  e2e only exercises the cloud *if* the refreshed image is already deployed. The
  check runs when the agent image is rebuilt; Ron will flag us. Nothing for us to
  do meanwhile (the BFF does not exercise this index either).

### T8 · Security rules — client READ access to `agentEvents` and `messages` — **DONE (deployed parity verified)**
- **Reality found:** already present in `server/firebase/firestore.rules`:
  - `messages` — `allow read: if isSessionOwner(sessionId)` (original rules commit).
  - `agentEvents` — `allow read: if isSessionOwner(sessionId)` (added in commit
    `8755224`). Hub writes via Admin SDK and bypass rules, so no write rule needed.
- **Changed:** nothing (repo already correct).
- **Verified (now confirmed, not assumed):** ran
  `firebase deploy --only firestore:rules` against `anizai-ai`, which reported
  "latest version of firestore.rules already up to date, skipping upload" — i.e.
  the deployed ruleset already matched the repo, and the client READ rules for
  `messages` and `agentEvents` are live and correct. Repo↔deployed parity is now
  **established, no longer an open assumption**. Ron's side confirmed no rule work
  is needed for hub writes (Admin SDK bypass).

---

## Automated check summary (end of sprint)

- `client` `tsc --noEmit`: clean (exit 0).
- `server` `tsc --noEmit`: clean (exit 0).
- `server` Vitest: 11/11 (`sessions.service`, `sessions.repository`, `health`).
- `client` Vitest: 31/31 (incl. new `agentEvents.test.ts` 4/4).
- Live browser: landing + logged-in session render, zero console errors; T3 both
  windows, T6 Rule A two-way (queued shows panel, done hides it) all confirmed.
- Firestore deploys to `anizai-ai`: rules already up to date ("skipping upload",
  so repo↔deployed parity confirmed); `messages` collection-group index deployed
  successfully and is live.

---

## Open items (only these remain)

> The two former deploy items (rules parity, index deploy) are now **done** — see
> T7 and T8 above. What remains is one upstream pipeline dependency and one scoped
> cleanup.

1. **Upstream dependency — the hub writes `session.currentRunId` at run start
   (hub's Sprint 25).** *Answered, not an open question.* Ron's side confirmed this
   is a **ratified task in their Sprint 25 plan**, but it is **not implemented yet**
   because their Sprint 25 hasn't been built. So today the hub does not write
   `currentRunId`, and our reasoning panel rendering **empty is the correct,
   expected behavior** until that sprint lands — not a bug. When implemented, the
   hub mints a `runId` per run and writes exactly `currentRunId` on the session doc
   on the **`running` transition, before any event is emitted**. Our Rule B filter
   already reads the right field by the right name — nothing to change on our side.
   This is a known upstream dependency with a known landing point.
2. **T6 Rule B untested against real multi-`runId` data.** Gated on **two**
   separate things, both of which must land:
   (a) the **hub's Sprint 25 being built** — `currentRunId` does not exist in their
   code yet; and
   (b) the **agent image being rebuilt and deployed** — so that code actually runs
   in the cloud (the deployed image is still Sprint ~21-era; see the cloud-state
   caveat below).
   Covered today only by the `selectCurrentRunEvents` unit test (4/4) and the live
   empty path. Re-verify once both land and real multi-run events exist.
3. **Scoped cleanup — drop dead `AgentEvent.parentMessageId`.** *Do not do now —
   recorded for later.* A leftover from the cancelled follow-up-events design;
   follow-ups emit no events, so the hub will never populate it and it is
   permanently `null`. It currently drives an unreachable "Follow-up" badge and row
   styling in `AgentEventsTimeline.tsx`. Scope: **5 references across 4 files** — the
   type (`client/src/types/index.ts`), the mapper (`mapAgentEventDoc` in
   `client/src/services/session.service.ts`), two render branches
   (`client/src/components/cards/AgentEventsTimeline.tsx`), and the test factory
   (`client/src/lib/agentEvents.test.ts`). Pure dead-code removal, no behavior
   change (guard with `tsc` + Vitest afterward).

---

## Structural notes to preserve

- **`replyToMessageId` is top-level** on the assistant message doc (not nested in
  `meta`), grounded in `data-pipeline/agent/followup/nodes/write_message.py`.
- **The Rule B filter lives in `client/src/lib/agentEvents.ts`** (`selectCurrentRunEvents`),
  a pure helper with an explicit `sequence` sort rather than relying on the Firestore
  query order. Unit-tested in `client/src/lib/agentEvents.test.ts`.
- **Rule A and Rule B read from different sources — respect the asymmetry.**
  Rule B's `currentRunId` and `agentEvents` come **exclusively from direct Firestore
  listeners** (`subscribeToSession` / `subscribeToAgentEvents`), never the BFF.
  `currentRunId` isn't even a field on the BFF-facing type or its session mapper,
  which is what makes the "BFF silently drops the field → Rule B renders nothing"
  failure mode **structurally impossible**. Rule A's display gate, by contrast,
  reads `status` from the BFF's REST `SessionDetail` — which is fine, because the
  BFF explicitly forwards `status`. Implication for anyone extending the panel:
  **any new field the panel needs must come from a listener, not the BFF session
  mapper** (the mapper forwards only an explicit field list).

---

## Known cloud-state caveat (deployment gap — not a bug)

The deployed cloud is running a **Sprint ~21-era agent image**
(`AGENT_VERSION 0.4.0-sprint21-…`, last deployed May). Everything from Sprint 22
onward — **including the entire follow-up mechanism built in Sprint 24** — exists
and passes **locally**, but is **not in the deployed image**. It waits on a
cumulative agent-image rebuild. This is a **separate track** from the hub's
Sprint 25 landing (Sprint 25 adds `currentRunId`; the image rebuild is what makes
any post-Sprint-21 code actually run in the cloud). Both tracks are independent
and both must land for the full contract to be live in production.

- **Permanent send-lock against the cloud today (expected, not a bug).** Because
  the follow-up mechanism isn't in the deployed image, a follow-up sent against the
  **cloud** will never be answered — the message stays at `status: 'sent'`
  permanently. Since `isSendLocked` derives from `isAwaitingAssistantResponse`,
  which keys off exactly that unanswered state (T3 / T4), the chat input **locks
  permanently for that session**, and a refresh won't clear it because the message
  is persisted in Firestore with that status. This is **correct per-contract
  behavior** — the contract assumes the hub answers; it only *looks* broken because
  the upstream half isn't deployed. Worth knowing before any cloud demo or manual
  check. Resolves itself once the agent image is rebuilt.
- **Visibility fix incoming.** Once the hub's Sprint 25 closes, Ron will expose
  their Domain docs (deployed-vs-local state), which will let us see what's actually
  running in the cloud directly — closing the visibility gap that caused this
  confusion.
