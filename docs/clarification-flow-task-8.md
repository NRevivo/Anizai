# Clarification Flow - Task 8

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_api.md §3.3`](C_frontend/frontend_api.md).
> Index: [`frontend_archive.md`](C_frontend/frontend_archive.md) §3.

## 1. Summary

This task adds a minimal clarification flow for sessions in `awaiting_clarification`.

On the frontend, those sessions now render a clarification picker instead of the generic placeholder state. On the backend, a new `POST /sessions/:id/clarify` endpoint validates the user, validates the session state, accepts either a chosen candidate or `null`, re-queues the session, and creates a fresh `forecastQueries` document for the clarified request.

## 2. Files Changed

- `client/src/App.tsx`
  - Adds the clarify-session action and refreshes session data after submit.
- `client/src/pages/DashboardPage.tsx`
  - Replaces the clarification placeholder with a real picker, submit button, and local loading/error handling.
- `client/src/services/session.service.ts`
  - Adds the `clarifySession()` client service method and demo fallback behavior.
- `server/src/routes/sessions.ts`
  - Adds `POST /sessions/:id/clarify`.
- `server/src/services/sessions.service.ts`
  - Adds session-state and candidate validation for clarification submission.
- `server/src/repositories/session.repository.ts`
  - Re-queues clarified sessions and writes a new `forecastQueries` document.
- `server/tests/sessions.service.test.ts`
  - Adds a minimal clarification-flow service test.

## 3. Frontend Behavior

When the active session status is `awaiting_clarification`:

- the dashboard no longer shows the normal running placeholder
- the user sees a clarification picker with:
  - a title asking which market or intent was meant
  - candidates sorted by `matchConfidence` descending
  - `label`
  - `source`
  - `description`
  - visible match percentage
  - radio selection
  - a `None of these` option
  - a submit button
- submit shows a loading state
- submit failures show an inline error state
- successful submit uses the existing refresh flow to reload sessions and the active session

## 4. Backend Endpoint Behavior

New endpoint:

- `POST /sessions/:id/clarify`

Request body:

```json
{
  "chosenCandidateId": "candidate-id-or-null"
}
```

Backend behavior:

- validates the authenticated user owns the session
- validates the session is currently `awaiting_clarification`
- validates the chosen candidate exists when a non-null candidate id is provided
- updates the session to:
  - `status: 'queued'`
  - `canonicalKey: selectedCandidate.id` when a candidate is selected
  - `errorMessage: null`
  - `errorCode: null`
  - `clarificationCandidates: null`
- creates one fresh `forecastQueries` document for the clarified request
- if `chosenCandidateId` is `null`, the session is still safely re-queued without forcing a candidate

## 5. Validation Results

Commands run:

- `git status --short`
- `cd client`
- `npx tsc -p tsconfig.app.json --noEmit --pretty false`
- `npm run lint`
- `cd ..\\server`
- `npm run build`
- `npm test`

Results:

- Client TypeScript: passed
- Client lint: failed only because root `eslint.config.js` cannot resolve `@eslint/js`
- Server build: passed
- Server tests: passed

## 6. Risks / Notes

- The clarification flow intentionally keeps the picker minimal and does not add a more advanced clarification UX.
- `None of these` re-queues the session without forcing a canonical market key.
- The clarified request writes a new `forecastQueries` document instead of mutating the original create-session query record.
- This task does not change idempotency, probability handling, demo route behavior, API prefix behavior, or plan-limit behavior.
