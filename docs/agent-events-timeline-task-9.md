# Agent Events Timeline - Task 9

## 1. Summary

This task adds a compact agent-events timeline to the frontend.

The client now listens to `sessions/:sessionId/agentEvents`, sorts events by `sequence`, cleans up the listener when the active session changes, and renders a compact reasoning timeline in both the completed-result view and the non-done active-session states.

## 2. Files Changed

- `client/src/App.tsx`
  - Owns the active-session agent-events listener lifecycle.
  - Stores event data and loading state.
- `client/src/components/Dashboard.tsx`
  - Renders the agent timeline in completed forecast views.
- `client/src/components/cards/AgentEventsTimeline.tsx`
  - New compact timeline component for reasoning and follow-up events.
- `client/src/lib/firebase.ts`
  - Exports Firestore client access.
- `client/src/pages/DashboardPage.tsx`
  - Renders the timeline for non-done active sessions as well.
- `client/src/services/session.service.ts`
  - Defines the subscription helper and event mapping logic.
- `client/src/types/index.ts`
  - Adds `AgentEvent`, event type/status unions, and related typing.

## 3. Listener Behavior

- Uses a Firestore listener on:
  - `sessions/{sessionId}/agentEvents`
- Orders events by:
  - `sequence` ascending
- Listener lifecycle:
  - starts when there is an active session id
  - cleans up when the active session changes
  - cleans up when there is no active session
- If the listener errors:
  - the app clears the current agent events
  - the timeline stops loading
  - the rest of the forecast/result UI continues rendering

## 4. UI Behavior

The compact timeline shows, per row:

- status indicator
- title
- optional description
- status label
- duration when present
- failed styling for failed/error events

Additional behavior:

- events with `parentMessageId !== null` are labeled as `Follow-up`
- empty state is shown when there are no events yet
- loading state is shown while the listener is waiting for initial data
- missing events do not block forecast, result, evidence, or chat rendering

## 5. Validation Results

Commands run:

- `git status --short`
- `cd client`
- `npx tsc -p tsconfig.app.json --noEmit --pretty false`
- `npm run lint`

Results:

- TypeScript: passed
- Lint: failed only because root `eslint.config.js` cannot resolve `@eslint/js`

## 6. Risks / Notes

- The listener assumes the active session has a readable `agentEvents` subcollection in Firestore.
- The timeline is intentionally compact and presentation-focused; it does not attempt rich grouping or payload inspection yet.
- When events are absent, the UI shows a compact empty state instead of treating that as an error.
- This task does not change idempotency, probability handling, demo behavior, API-prefix behavior, plan-limit behavior, or clarification behavior.
