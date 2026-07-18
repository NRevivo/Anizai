# Status, Loading, Empty, and Error States - Task 0.6

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_ui.md §5.2`](C_frontend/frontend_ui.md).
> Index: [`frontend_archive.md`](C_frontend/frontend_archive.md) §3.

## 1. Summary of Changes

Improved the current user-facing loading, empty, warning, and error presentation without changing backend behavior or lifecycle ownership. A small `StateMessage` UI helper now gives dashboard states, empty result sections, chat, trending, forecast form errors, and plan-limit warnings a consistent compact presentation.

The dashboard loading and no-session states are clearer and more action-oriented. The sidebar now has a no-sessions message and friendlier labels for the existing `stable`/`volatile` UI statuses. Existing empty result sections from Task 0.5 now use the shared state presentation.

## 2. Files Changed

- `client/src/components/ui/StateMessage.tsx`: Added a small reusable frontend-only loading/empty/error/warning presentation helper.
- `client/src/App.tsx`: Improved app hydration loading copy and generic visible error toast styling.
- `client/src/pages/DashboardPage.tsx`: Improved dashboard loading, no-forecast, and no-active-session states.
- `client/src/components/Sidebar.tsx`: Added no-sessions state and clearer `Stable` / `Watching` labels for existing UI statuses.
- `client/src/components/ChatPanel.tsx`: Standardized the no-follow-up-messages state.
- `client/src/components/CreateForecastView.tsx`: Standardized inline forecast creation validation/API error presentation.
- `client/src/components/CreateForecastContext.tsx`: Standardized the no-trending-forecasts state.
- `client/src/components/cards/MarketComparison.tsx`: Standardized missing market benchmark state.
- `client/src/components/cards/SentimentAnalysis.tsx`: Standardized missing sentiment data state.
- `client/src/components/cards/EvidenceTimeline.tsx`: Standardized no-evidence and no-matching-filter states.
- `client/src/components/settings/SubscriptionSettings.tsx`: Improved existing monthly limit warning presentation.
- `docs/status-empty-error-states-task-0-6.md`: Added this summary.

## 3. State Coverage

| State | Current support after this task | File/component | Notes |
|---|---|---|---|
| queued | Not implemented | N/A | Not present in the current frontend status contracts; no fake UI state added. |
| running | Partial | `App.tsx`, `Sidebar.tsx`, `DashboardPage.tsx` | Backend `running` is still mapped to the existing `volatile` UI status upstream; sidebar now labels this bucket as `Watching`. No new running lifecycle UI added. |
| done | Partial | `App.tsx`, `Sidebar.tsx`, result cards | Completed sessions still render through existing result UI; sidebar labels the stable bucket as `Stable`. |
| failed | Partial, unchanged ownership | `App.tsx`, `Sidebar.tsx` | Backend `failed` remains collapsed into the existing volatile UI bucket. No failed-result ownership added. |
| awaiting_clarification | Not implemented | N/A | Not present in current frontend types; documented as later work. |
| no sessions yet | Improved | `DashboardPage.tsx`, `Sidebar.tsx` | Center panel and sidebar now show clearer empty-state copy. Center panel includes an existing safe `New forecast` action. |
| no evidence yet | Improved | `EvidenceTimeline.tsx` | Shows a compact no-evidence message when the current evidence array is empty. |
| no follow-up messages yet | Improved | `ChatPanel.tsx` | Shows clearer copy inviting follow-up questions. |
| plan limit exceeded | Partial, improved | `SubscriptionSettings.tsx`, `CreateForecastView.tsx` | Existing subscription usage warning is clearer. Create form still only surfaces plan-limit wording if an existing error message contains limit/upgrade/premium wording. |
| generic loading | Improved | `App.tsx`, `DashboardPage.tsx`, `StateMessage.tsx` | App hydration and dashboard loading use compact loading state with clearer copy. |
| generic API error | Improved, partial | `App.tsx`, `CreateForecastView.tsx` | Existing `authError` toast is clearer and form errors are standardized. Service fallbacks may still hide API errors. |

## 4. What Was Intentionally Not Changed

- No backend changes.
- No API route changes.
- No API contract changes.
- No new session status ownership.
- No TypeScript session status union changes.
- No retry implementation.
- No clarification picker.
- No plan-limit backend logic.
- No idempotency.
- No Firestore reads/listeners changed.
- No data contract changes.
- No probability behavior changes.
- No message sending behavior changes.

## 5. Missing States for Later Tasks

- Real queued status ownership.
- Distinct running analysis state and progress UI.
- Distinct failed forecast screen using session failure details.
- Awaiting clarification state and clarification picker.
- Retry actions for failed API/session loads.
- Real plan-limit blocking modal or backend-aware upgrade flow.
- Chat/follow-up sending or assistant pending state.
- API unavailable/offline state that is not masked by demo fallback behavior.

## 6. Validation

Commands run:

- `git status --short`: Checked the dirty worktree before editing and after changes.
- `Get-Content -Raw` for all required Task 0.1 through Task 0.5 docs and safety reviews.
- `Get-Content -Raw` / `rg` for status, loading, empty, error, plan-limit, dashboard, chat, result, and settings components.
- `git diff -- ...`: Reviewed the UI-only state presentation changes.
- `npx tsc -p tsconfig.app.json --noEmit --pretty false` from `client`: Passed.
- `npm run lint` from `client`: Failed before linting because ESLint still cannot resolve `@eslint/js` from `Anizai/eslint.config.js`.

Known issue:

- Existing lint blocker remains: `Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@eslint/js' imported from ...\Anizai\eslint.config.js`. This was not fixed because it is unrelated to Task 0.6.

Manual review notes:

- Changes are limited to frontend presentation of currently reachable state surfaces.
- No fake queued, failed, awaiting clarification, retry, or backend plan-limit behavior was added.
- Existing service/API/session/message/probability behavior was not intentionally changed.
