# Plan Limit Audit - Task 1.3

## 1. Summary

There is already a real backend free-tier forecast limit in place. It is enforced during session creation through usage counting on the user record, with free users blocked after 3 forecasts in the current calendar month. The backend also exposes user plan and usage fields through `/me`, and allows plan changes through `/me/plan`.

Frontend handling exists, but it is partial and mostly presentational. The settings subscription UI shows plan state, usage, and upgrade controls. The forecast creation form can display a plan-limit-style inline message, but only by inspecting generic error text for words like `limit`, `upgrade`, or `premium`. There is no dedicated plan-limit modal, no dedicated frontend error code handling, and no explicit `PLAN_LIMIT_EXCEEDED` contract.

## 2. Existing Backend Enforcement, If Any

Existing enforcement is present in the backend:

- `server/src/services/sessions.service.ts`
  - `createSession()` calls `usersService.incrementUsage(userId)` before writing the session document.
- `server/src/services/users.service.ts`
  - `incrementUsage()` delegates to `userRepository.incrementUsage(uid)`.
- `server/src/repositories/user.repository.ts`
  - `incrementUsage()`:
    - reads the user doc
    - computes the current `usageMonth`
    - lazily resets usage to `0` when the stored month differs from the current calendar month
    - blocks free users when `monthlyForecastsUsed >= 3`
    - increments and persists `monthlyForecastsUsed`

Current enforcement rule:

- Free users are blocked at `3` forecasts per month.
- Premium users are not blocked by this check.

Exact backend block behavior:

- Throws `new AppError('Monthly forecast limit reached for free plan (3/3). Please upgrade to Premium.', 403, 'FORBIDDEN')`

Important detail:

- The limit is enforced only through session creation right now, not through Firestore rules.
- Firestore rules do not contain plan-limit logic.

## 3. Existing Frontend Handling, If Any

Existing frontend handling is partial:

- `client/src/components/CreateForecastView.tsx`
  - has inline form error handling
  - converts any error message containing `limit`, `upgrade`, or `premium` into:
    - `You have reached your forecast limit. Upgrade to Premium or wait for your monthly limit to reset.`
  - shows static helper text:
    - `Uses 1 free forecast`
  - does not read live usage counts or enforce plan logic directly
- `client/src/components/settings/SubscriptionSettings.tsx`
  - reads `userProfile.plan`, `monthlyForecastsUsed`, `usageMonth`, and `planExpiresAt`
  - shows:
    - current plan badge
    - free vs premium feature cards
    - usage meter for free users
    - warning state when `monthlyForecastsUsed >= 3`
    - upgrade CTA
    - cancel/reactivate subscription UI
  - warning copy:
    - `Free forecast limit reached`
    - `Upgrade to Premium for unlimited forecasts, or wait for the monthly limit to reset.`
- `client/src/components/Sidebar.tsx`
  - displays only `Free plan` or `Premium plan` label in the footer
- `client/src/pages/PlanSelection.tsx`
  - exposes free and premium plan selection UI
- `client/src/services/user.service.ts`
  - fetches `/me`
  - updates plan via `/me/plan`
  - falls back to demo plan data when API is unavailable

What is not implemented on the frontend:

- No dedicated plan-limit modal
- No dedicated `PLAN_LIMIT_EXCEEDED` error-code handling
- No automatic redirect/opening of subscription UI when a limit is hit
- No authoritative create-forecast usage indicator tied to real profile usage
- No guarantee that the create-form copy stays in sync with the real backend limit

## 4. Current Error Response Shape

Server error shape is centralized in:

- `server/src/middleware/error.ts`
- `server/src/types/api.ts`

Current shape:

```ts
{
  error: {
    message: string,
    code?: string,
    details?: unknown
  },
  meta?: {
    requestId?: string,
    timestamp?: string
  }
}
```

Current plan-limit-related backend response characteristics:

- HTTP status: `403`
- error code: `FORBIDDEN`
- message: `Monthly forecast limit reached for free plan (3/3). Please upgrade to Premium.`

Frontend API parsing:

- `client/src/lib/api.ts`
  - reads `error.message` and `error.code`
  - throws `ApiError` or `ApiAuthError`
  - the create-forecast UI currently uses message text matching rather than checking a dedicated plan-limit code

## 5. Files Involved

Backend:

- `server/src/services/sessions.service.ts`
- `server/src/services/users.service.ts`
- `server/src/repositories/user.repository.ts`
- `server/src/routes/me.ts`
- `server/src/middleware/error.ts`
- `server/src/types/api.ts`
- `server/firebase/firestore.rules`
- `server/scripts/seed.ts`

Frontend:

- `client/src/lib/api.ts`
- `client/src/services/user.service.ts`
- `client/src/components/CreateForecastView.tsx`
- `client/src/components/settings/SubscriptionSettings.tsx`
- `client/src/components/Sidebar.tsx`
- `client/src/pages/PlanSelection.tsx`
- `client/src/App.tsx`

Existing docs with relevant prior findings:

- `docs/ui-map-task-0-1.md`
- `docs/status-empty-error-states-task-0-6.md`
- `docs/ui-microcopy-task-0-8.md`

Tests and fixtures:

- `server/scripts/seed.ts`
  - seeds `plan: 'free'`, `usageMonth`, and `monthlyForecastsUsed`
- No direct plan-limit test was found in `server/tests`
- No frontend tests for plan-limit handling were found

## 6. Gaps vs TASK 7 Requirements

Based on the current implementation, the likely gaps for a later plan-limit task are:

- No dedicated backend error code like `PLAN_LIMIT_EXCEEDED`
  - current code is generic `FORBIDDEN`
- No dedicated frontend branch that handles plan-limit errors by code
  - current form logic depends on error-message text matching
- No blocking modal for limit hits
  - only inline form messaging and settings warning exist
- No shared limit constants
  - the `3` limit is hardcoded in backend enforcement and implied separately in frontend copy/features
- No direct test coverage for plan-limit enforcement or UI handling
- No plan-limit-specific fixture or integration test
- No canonical shared type for plan-limit errors
- No real billing backend
  - `SubscriptionSettings.tsx` includes mock payment processing and optimistic plan changes via `/me/plan`
- No server-driven upgrade CTA flow
  - upgrade is purely UI-driven

## 7. Recommendation for TASK 7

Recommended approach for TASK 7:

- Build on the existing backend enforcement rather than re-implementing limit counting.
- Introduce a dedicated backend error code for plan-limit failures instead of relying on generic `FORBIDDEN`.
- Keep the `/me` user profile fields as the source of truth for current plan and usage display.
- Reuse the existing subscription/settings UI as the upgrade destination rather than inventing a separate competing flow.
- Replace create-form message sniffing with explicit `error.code` handling once the backend exposes a dedicated code.
- Centralize the free-tier limit value so backend enforcement and frontend messaging stay aligned.
- Add direct test coverage for:
  - backend free-limit blocking
  - monthly reset behavior
  - frontend plan-limit error presentation

Practical caution for later work:

- `client/src/services/user.service.ts` and `client/src/components/settings/SubscriptionSettings.tsx` already assume a simple `free | premium` model and mock billing behavior. Any later work should preserve that behavior unless the task explicitly includes replacing the billing mock flow.
