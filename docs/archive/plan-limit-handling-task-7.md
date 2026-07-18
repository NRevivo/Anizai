# Plan Limit Handling - Task 7

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_api.md §6.1`](../C_frontend/frontend_api.md).
> Index: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §3.

## 1. Summary

This task standardizes plan-limit handling across the backend and frontend.

On the backend, free-tier forecast exhaustion now returns a structured `PLAN_LIMIT_EXCEEDED` error with usage details instead of a generic forbidden response. On the frontend, forecast creation detects that error code, shows a dedicated blocking state in the form, and links the user into the existing Subscription settings flow instead of surfacing the limit as a generic crash.

## 2. Files Changed

- `server/src/middleware/error.ts`
  - Added support for structured `error.details` on `AppError` responses.
- `server/src/repositories/user.repository.ts`
  - Standardized the free-tier limit error code and response details.
- `server/tests/sessions.service.test.ts`
  - Added a backend safety test to confirm plan-limit failures stop before session creation.
- `client/src/lib/api.ts`
  - Preserves backend `error.details` on thrown API errors.
- `client/src/components/CreateForecastView.tsx`
  - Detects `PLAN_LIMIT_EXCEEDED`, renders a dedicated blocking state, and shows usage/reset details.
- `client/src/components/SettingsModal.tsx`
  - Allows opening the modal directly on the Subscription section.
- `client/src/pages/DashboardPage.tsx`
  - Opens Subscription settings from the forecast-creation plan-limit state.
- `client/src/App.tsx`
  - Avoids showing a generic top-level crash toast for explicit plan-limit errors and refreshes local usage count when details are available.

## 3. Backend Response Shape

When a free user has already used the allowed monthly forecasts, the backend now returns:

```json
{
  "error": {
    "code": "PLAN_LIMIT_EXCEEDED",
    "message": "You've used your free forecasts this month",
    "details": {
      "used": 3,
      "limit": 3,
      "planTier": "free",
      "resetAt": "2026-05-01T00:00:00.000Z"
    }
  }
}
```

Behavior notes:

- The limit is still enforced before session creation.
- No session document is created after a plan-limit failure.
- No `forecastQueries` document is created after a plan-limit failure.
- Existing usage counting behavior remains in place.

## 4. Frontend Handling

Forecast creation now checks `error.code === 'PLAN_LIMIT_EXCEEDED'`.

When present, the form shows:

- the blocking limit message
- used / limit
- plan tier
- reset date when available
- a CTA to review plans in the existing Subscription settings section

Fallback behavior:

- If the backend ever returns an older generic message, the existing text-based fallback still remains safe.
- If Subscription settings are not opened, the inline blocking state still explains what happened instead of looking like a generic crash.

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

- The existing free-tier limit remains hardcoded at `3` in backend enforcement.
- The frontend now relies on the structured `PLAN_LIMIT_EXCEEDED` contract when available, but still keeps a safe fallback for older generic errors.
- This task does not add a new billing backend or a dedicated plan-limit modal.
- This task does not change idempotency, probability handling, session statuses, API prefix behavior, or demo behavior.
