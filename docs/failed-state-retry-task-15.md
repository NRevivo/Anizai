# Failed State and Retry - Task 15

<!-- archive-banner -->
> ⚠️ **SUPERSEDED — contains inaccuracies.** Historical record only; do not
> cite as current. Corrected content: [`frontend_api.md`](C_frontend/frontend_api.md) §3.3.
> Why this doc is wrong: [`frontend_archive.md`](C_frontend/frontend_archive.md) §2.

## 1. Summary
- Added a clearer failed-session UI with a dedicated retry action.
- Reused the existing session-creation flow to retry failed forecasts as fresh requests.
- Kept retry logic frontend-only by generating a new idempotency key and posting through the existing `POST /sessions` path.

## 2. Files Changed
- `client/src/App.tsx`: added a retry handler that reuses the original session question and generates a fresh idempotency key.
- `client/src/pages/DashboardPage.tsx`: added failed-session retry UI, loading state, safe fallback copy, and local retry error handling.

## 3. Failed UI Behavior
- Failed sessions continue to avoid normal result and running views.
- The active session panel now shows:
  - a clear failed state
  - `session.errorMessage` when available
  - a safe fallback message when `errorMessage` is missing
  - a `Retry forecast` button

## 4. Retry Behavior
- Retry creates a fresh session request using the original failed session question.
- Each retry uses `crypto.randomUUID()` to generate a new idempotency key.
- Successful retry follows the existing session creation flow, including loading the new session automatically.
- While retry is in progress:
  - the button is disabled
  - the label changes to `Retrying forecast...`
- If the original question is missing, retry is disabled and the UI shows a safe explanation.

## 5. Validation Results
- `git status --short`
- `cd client && npx tsc -p tsconfig.app.json --noEmit --pretty false`: passed
- `cd client && npm run lint`: failed only because root `eslint.config.js` still cannot resolve `@eslint/js`

## 6. Risks/Notes
- Retry currently depends on the failed session being the active session so the original question can be read safely from loaded detail data.
- The retry flow intentionally creates a new session instead of mutating the failed one, which keeps behavior aligned with the existing idempotent session creation path.
