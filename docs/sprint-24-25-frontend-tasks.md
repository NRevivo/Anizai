# Anizai · Frontend / BFF Tasks · Sprint 24 + 25 — **AS-BUILT**

Sources: `sprint24_followups_frontend_contract.md` + `sprint25_frontend_contract.md`
Owner: Noam (`client/`, `server/`) + shared Firestore project `anizai-ai`

> **This is an as-built record.** The original planning doc was written before we
> read the code and marked several already-built tasks as open. This version
> records what was actually changed, the real file paths, and how each item was
> verified. Task IDs (T0–T8) are preserved for traceability. Status legend:
> **DONE** (built this sprint) · **DONE (pre-existing)** (already built before
> this sprint, confirmed working) · **OPEN** (still outstanding — see Open items).

Recommended original order was `T0 → T1,T2 → T3,T4,T5 → T6 → T7,T8`. All
frontend/BFF code work is now complete; the only outstanding items are deploy /
pipeline-side dependencies (Open items section).

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
  (pipeline emits no events yet — see Open item 4).

**Follow-up answers do not emit events** — the panel is for the main forecast only.

---

## Phase 5 · Shared Firestore deploy

### T7 · Collection-group index on `messages` — **DONE (repo) / OPEN (deploy)**
- **Changed:** `server/firebase/firestore.indexes.json` — added the `messages`
  `COLLECTION_GROUP` index (`role` ASC, `status` ASC), matching the contract spec
  and the structural form of the existing `evidence` CG index.
- **Verified:** JSON shape confirmed programmatically (COLLECTION_GROUP, 2 ASCENDING
  fields). **Deploy not run** — see Open item 3. This index is a hub-side
  requirement; the BFF does not use it.

### T8 · Security rules — client READ access to `agentEvents` and `messages` — **DONE (pre-existing) / OPEN (deploy parity)**
- **Reality found:** already present in `server/firebase/firestore.rules`:
  - `messages` — `allow read: if isSessionOwner(sessionId)` (original rules commit).
  - `agentEvents` — `allow read: if isSessionOwner(sessionId)` (added in commit
    `8755224`). Hub writes via Admin SDK and bypass rules, so no write rule needed.
- **Changed:** nothing (repo already correct).
- **Verified:** rules read in-repo. **Not verifiable locally:** that the *deployed*
  ruleset in `anizai-ai` matches the repo — see Open item 2.

---

## Automated check summary (end of sprint)

- `client` `tsc --noEmit`: clean (exit 0).
- `server` `tsc --noEmit`: clean (exit 0).
- `server` Vitest: 11/11 (`sessions.service`, `sessions.repository`, `health`).
- `client` Vitest: 31/31 (incl. new `agentEvents.test.ts` 4/4).
- Live browser: landing + logged-in session render, zero console errors; T3 both
  windows, T6 Rule A two-way (queued shows panel, done hides it) all confirmed.

---

## Open items (only these remain)

1. **Hub must write `session.currentRunId` at run start.** T6 Rule B filters the
   reasoning panel strictly by `runId == currentRunId`. If the hub emits events but
   never writes `currentRunId`, the panel stays empty forever even though the event
   docs are stored — and mid-run-refresh repopulation would also render empty. This
   is a **pipeline↔frontend contract dependency**. Sent to Ron for confirmation;
   unverifiable on our side until the pipeline emits agentEvents.
2. **Verify deployed Firestore rules match the repo.** A rule in the repo is not a
   rule deployed. In the `anizai-ai` console → Firestore → Rules, confirm both
   `messages` and `agentEvents` have client READ rules (`allow read: if isSessionOwner(sessionId)`).
   CLI validation (does not diff against live): `firebase deploy --only firestore:rules --dry-run --project anizai-ai`.
3. **Deploy the `messages` collection-group index**, then confirm with Ron.
   `cd server/firebase && firebase deploy --only firestore:indexes --project anizai-ai`.
   This is a hub-side requirement (his cross-session `role`/`status` collection-group
   listener); nothing on our side exercises it, so confirm his listener returns
   results in production after deploy.
4. **T6 Rule B untested against real multi-`runId` data.** The pipeline emits no
   agentEvents yet. Covered only by the `selectCurrentRunEvents` unit test (4/4) and
   the live empty path. Re-verify once real multi-run events exist.

---

## Structural notes to preserve

- **`replyToMessageId` is top-level** on the assistant message doc (not nested in
  `meta`), grounded in `data-pipeline/agent/followup/nodes/write_message.py`.
- **The Rule B filter lives in `client/src/lib/agentEvents.ts`** (`selectCurrentRunEvents`),
  a pure helper with an explicit `sequence` sort rather than relying on the Firestore
  query order. Unit-tested in `client/src/lib/agentEvents.test.ts`.
