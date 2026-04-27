# UI Text and Microcopy Optimization - Task 0.8

## 1. Summary of Changes
Updated visible product copy across the dashboard, forecast creation flow, result cards, evidence, follow-up panel, loading/empty/error states, and settings/subscription screens.

The copy is now more compact, consistent, and forecast-oriented. It avoids overpromising backend behavior, uses clearer calls to action, and standardizes terms such as forecast, evidence, follow-up, confidence, market benchmark, sentiment, and plan.

## 2. Files Changed
| File | Why |
| --- | --- |
| `client/src/App.tsx` | Clarified loading and generic error copy surfaced by the app shell and dashboard flows. |
| `client/src/pages/DashboardPage.tsx` | Improved empty-state, loading, delete-confirmation, and suggested follow-up labels. |
| `client/src/components/Dashboard.tsx` | Replaced `Live Prediction` with `Active forecast`. |
| `client/src/components/Sidebar.tsx` | Standardized forecast terminology in search, section titles, plan label, and no-forecast state. |
| `client/src/components/CreateForecastView.tsx` | Tightened placeholders, validation errors, loading text, helper copy, and primary CTA. |
| `client/src/components/CreateForecastContext.tsx` | Improved trending panel helper copy, empty state, action label, and trend labels. |
| `client/src/components/ChatPanel.tsx` | Standardized `follow-up` wording and shortened input/empty-state copy. |
| `client/src/components/cards/PredictionOverview.tsx` | Renamed headings and labels around final probability, confidence, evidence, and rationale. |
| `client/src/components/cards/MarketComparison.tsx` | Standardized market benchmark wording and missing-data copy. |
| `client/src/components/cards/SentimentAnalysis.tsx` | Clarified sentiment as supporting context and improved no-data copy. |
| `client/src/components/cards/EvidenceTimeline.tsx` | Tightened evidence labels, empty states, and source/supporting copy. |
| `client/src/components/settings/ProfileSettings.tsx` | Improved profile save/status labels and field copy. |
| `client/src/components/settings/AccountSettings.tsx` | Cleaned account detail labels and unavailable-value copy. |
| `client/src/components/settings/PreferenceSettings.tsx` | Clarified preference descriptions and forecast rationale terminology. |
| `client/src/components/settings/NotificationSettings.tsx` | Simplified inactive notification wording without implying launch behavior. |
| `client/src/components/settings/SecuritySettings.tsx` | Clarified re-authentication copy and replaced placeholder dash/ellipsis text. |
| `client/src/components/settings/SubscriptionSettings.tsx` | Improved plan, payment, limit, cancellation, and subscription status copy. |

## 3. Terminology Decisions
- `forecast`: Preferred for user-created questions and workspace items.
- `evidence`: Preferred for sources and supporting material.
- `follow-up`: Preferred for chat questions after a forecast result.
- `confidence`: Kept for the numeric confidence value.
- `market benchmark`: Preferred over broad `market consensus` where data may be unavailable.
- `sentiment`: Framed as supporting context, not the final answer.
- `plan`: Used for Free/Premium account state; `subscription` kept for billing actions.
- `rationale`: Used for the result explanation area.

## 4. Notable Copy Changes
| Area | Before | After |
| --- | --- | --- |
| Primary forecast CTA | `Analyze forecast` | `Start forecast` |
| Forecast loading | `Starting analysis...` | `Starting forecast...` |
| Forecast helper | `Uses 1 of 3 free forecasts` | `Uses 1 free forecast` |
| Forecast placeholder | `Probability of...` style examples | Direct yes/no forecast questions with clear dates |
| Empty dashboard | `Create your first forecast question to start a new session.` | `Create a forecast to see probability, confidence, and evidence.` |
| Sidebar search | `Search predictions...` | `Search forecasts...` |
| Chat panel | `Reasoning Chat` | `Follow-up` |
| Chat placeholder | `Ask a follow-up (drivers, risks, what could change)...` | `Ask a follow-up...` |
| Trend labels | `up`, `down`, `stable` | `Rising`, `Falling`, `Steady` |
| Result heading | `Final Forecast` | `Forecast Result` |
| Result explanation | `Detailed Explanation` | `Rationale` |
| Market card | `Market comparison unavailable` | `No market benchmark available` |
| Sentiment card | `Sentiment Analysis` | `Sentiment Signals` |
| Evidence card | `Evidence Timeline` | `Evidence` |
| Plan warning | `Monthly forecast limit reached` | `Free forecast limit reached` |
| Subscription success | `Welcome to Premium!` | `Premium is active` |

## 5. What Was Intentionally Not Changed
- No backend changes.
- No API contract changes.
- No Firestore changes.
- No business logic changes.
- No data contract or TypeScript type changes.
- No probability changes.
- No new statuses.
- No idempotency.
- No clarification or retry behavior.
- No message sending changes.
- No plan enforcement changes.
- No layout redesign.

## 6. Validation
Commands run:

- `git status --short`
  - Confirmed the worktree was already dirty from previous tasks and unrelated service files before this task.
- `Get-Content -Raw` for all required Task 0.1 through Task 0.7 docs and safety reviews.
- `Get-Content -Raw`, `Select-String`, and `rg` for copy inspection in the dashboard, result cards, chat, forecast creation, and settings files.
- `npx tsc -p tsconfig.app.json --noEmit --pretty false` from `client`
  - Passed.
- `npm run lint` from `client`
  - Failed before linting because the existing root ESLint config imports missing package `@eslint/js` from `Anizai/eslint.config.js`.

Manual review notes:
- Changes are copy/microcopy focused.
- The only presentational fit adjustments are inherited from prior tasks; this task did not intentionally redesign layout.
- Existing dirty service files were not touched.
- No business logic, API behavior, backend behavior, Firestore behavior, or data contracts were intentionally changed.
