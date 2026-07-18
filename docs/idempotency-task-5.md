# Idempotency Task 5

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_contracts.md §2.4`](C_frontend/frontend_contracts.md).
> Index: [`frontend_archive.md`](C_frontend/frontend_archive.md) §3.

## 1. Summary

We added minimal idempotency support to `POST /sessions` so the frontend sends a unique `idempotencyKey` with each forecast submission attempt, and the backend reuses an existing recent session for duplicate submits with the same key instead of creating another session.

The existing submit-pending UI protection remains in place. No session status, probability, plan, demo, or clarification behavior was intentionally changed.

## 2. Files Changed

- `client/src/App.tsx`
  - Passes `idempotencyKey` into the session creation request.
- `client/src/components/CreateForecastView.tsx`
  - Generates a UUID v4 per submit attempt and rotates it after a successful submit.
- `client/src/pages/DashboardPage.tsx`
  - Threads the `idempotencyKey` through the forecast creation flow and uses fresh keys for quick-submit actions.
- `client/src/services/session.service.ts`
  - Adds `idempotencyKey` to the create-session request contract.
- `server/src/repositories/session.repository.ts`
  - Stores `idempotencyKey` on created sessions and adds recent duplicate lookup logic.
- `server/src/routes/sessions.ts`
  - Requires a non-empty UUID `idempotencyKey` in the `POST /sessions` body.
- `server/src/services/sessions.service.ts`
  - Checks for an existing recent session before creating a new one and returns it when found.
- `server/tests/sessions.repository.test.ts`
  - Updates repository test inputs to include `idempotencyKey`.

## 3. Request Contract

`POST /sessions` now expects:

```json
{
  "question": "Will inflation in the US fall below 3% by Q4?",
  "idempotencyKey": "3f0df0a0-2d78-4b1b-9837-7d6f6efac4e2"
}
```

Contract notes:

- `idempotencyKey` is required.
- It must be a string.
- It must be non-empty.
- It is validated as a UUID on the server.

## 4. Duplicate Detection Logic

Backend duplicate handling now follows this flow:

1. Validate `question` and `idempotencyKey`.
2. Look for an existing session for the same `userId` and `idempotencyKey` created within the last 60 seconds.
3. If found, return that existing session.
4. If not found, continue with normal session creation.
5. Store `idempotencyKey` on the newly created session.

The duplicate lookup uses a recent-session query keyed by:

- `userId`
- `idempotencyKey`
- `createdAt >= now - 60 seconds`

## 5. ForecastQueries Duplicate Prevention

`forecastQueries` documents are only written during the normal session creation path in `session.repository.ts`.

Because duplicate requests return the existing recent session before that creation path runs again, duplicate submit retries with the same key do not create a second `forecastQueries` document in the normal request flow.

## 6. Firestore Index Note

Recommended composite index for the recent duplicate lookup:

- Collection: `sessions`
- Fields:
  - `userId` ascending
  - `idempotencyKey` ascending
  - `createdAt` descending

The current implementation includes a fallback path if Firestore reports a missing composite index, but the index should still be added for efficient production lookups.

## 7. Validation Results

Commands run:

- `git status --short`
- `cd client`
- `npx tsc -p tsconfig.app.json --noEmit --pretty false`
- `npm run lint`
- `cd ..\server`
- `npm run build`
- `npm test`

Results:

- Client TypeScript check: passed
- Client lint: failed only because root `eslint.config.js` cannot resolve `@eslint/js`
- Server build: passed
- Server tests: passed

## 8. Risks / Notes

- This change keeps the API response shape for sessions unchanged.
- Existing frontend double-submit protection remains in place.
- Old sessions remain readable because the client does not require `idempotencyKey` in session responses.
- The duplicate-prevention flow is intentionally minimal and scoped to create-session behavior.
- The recent-session lookup is designed to prevent repeated duplicate submits with the same key without changing unrelated session flow behavior.
