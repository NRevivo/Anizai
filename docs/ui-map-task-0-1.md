# UI Map - Task 0.1

<!-- archive-banner -->
> ⚠️ **SUPERSEDED — contains inaccuracies.** Historical record only; do not
> cite as current. Corrected content: [`frontend_ui.md`](C_frontend/frontend_ui.md) §2, §3, §5, §6.
> Why this doc is wrong: [`frontend_archive.md`](C_frontend/frontend_archive.md) §2.

## 1. Summary

The current frontend is a Vite/React single page app. It does not use a route library; `client/src/App.tsx` owns a local `appState` union and conditionally renders each "route" or screen. The dashboard itself is rendered through `client/src/pages/DashboardPage.tsx`, which owns the sidebar/chat drawer state and switches the center panel between the active forecast dashboard and the forecast creation view.

Data is loaded through service modules in `client/src/services`. Those services call the localhost API through `client/src/lib/api.ts` and fall back to local demo data in `client/src/data/mockData.ts` when the API/authenticated request fails. Frontend UI types live in `client/src/types/index.ts`; API/session types are also declared in `client/src/services/session.service.ts`.

## 2. Screens and Routes

| Screen / route | Route/path if applicable | Main file | Purpose | Main child components | Important state/data dependencies |
|---|---|---|---|---|---|
| Landing | No URL route; `appState === 'landing'` | `client/src/pages/LandingPage.tsx` | Public marketing/home page with top nav and CTA. | `Hero`, `ProductExplanation`, `UIShowcase`, `HowItWorks`, `WhoItsFor`, `FinalCTA`, `Footer` | Navigation callbacks from `App.tsx`; `onAuth` enters demo dashboard rather than setting `login`. |
| Login | No URL route; `appState === 'login'`, but current landing auth path calls demo dashboard directly | `client/src/pages/LoginPage.tsx` | Google or email sign-in UI. | `GoogleAuthButton`, `Input`, `Button` | Local email/password/email-mode state; `authError` toast rendered by `App.tsx`; handlers call `enterDemoDashboard`. |
| Signup | No URL route; `appState === 'signup'` | `client/src/pages/SignupPage.tsx` | Account creation UI, then plan selection. | `GoogleAuthButton`, `Input`, `Button` | Local name/email/password state; `handleCreateAccount` creates a demo profile and moves to plan selection. |
| Plan selection | No URL route; `appState === 'plan-selection'` | `client/src/pages/PlanSelection.tsx` | Choose Free or Premium after signup or pricing navigation. | `PlanCard` | `updateUserPlan` via `App.tsx`; `authError` may be set but is not rendered on this screen. |
| Main dashboard / active session view | No URL route; default final render in `App.tsx` | `client/src/pages/DashboardPage.tsx` | Primary logged-in workspace with session navigation, active forecast result, chat, create forecast flow, settings, and delete confirmation. | `Sidebar`, `Dashboard`, `ChatPanel`, `CreateForecastView`, `TrendingContext`, `ConfirmDialog`, `SettingsModal` | `sessions`, `activeSessionId`, `prediction`, `sentimentData`, `timelineEvents`, `messages`, `trendingForecasts`, `userProfile`, `isLoading`; local drawer/current-view/delete/settings state. |
| Forecast creation form/screen | Internal dashboard view; `currentView === 'new-forecast'` | `client/src/components/CreateForecastView.tsx` | Collects a forecast question. | `Button` | Local `question`, `isSubmitting`, rotating placeholder, validation hints; calls `onCreateSession`. |
| Forecast/session list | Part of dashboard layout | `client/src/components/Sidebar.tsx` | Lists active forecasts and lets user select/delete sessions. | `Button` | `sessions: PredictionSession[]`; active session id; no explicit empty state when `sessions` is empty. |
| Forecast result view | Part of dashboard layout | `client/src/components/Dashboard.tsx` | Displays the active prediction result and analysis cards. | `PredictionOverview`, `MarketComparison`, `SentimentAnalysis`, `EvidenceTimeline` | Requires non-null `Prediction`; uses sentiment and evidence arrays from selected session detail. |
| Chat/follow-up panel | Part of dashboard layout | `client/src/components/ChatPanel.tsx` | Displays session messages, suggested next steps, and follow-up input. | `Button`, `Input`, `ReactMarkdown` | `messages`, static suggested actions from `DashboardPage`; no explicit no-messages copy. |
| Settings modal | Modal opened from sidebar profile menu | `client/src/components/SettingsModal.tsx` | Account settings surface. | `ProfileSettings`, `AccountSettings`, `SubscriptionSettings`, `PreferenceSettings`, `NotificationSettings`, `SecuritySettings` | Local active settings section; depends on Firebase `auth` in several sections and `userProfile` props. |
| Subscription/settings billing states | Settings section | `client/src/components/settings/SubscriptionSettings.tsx` | Shows plan, usage meter, upgrade/cancel/reactivate/payment form flows. | Internal `PaymentForm`, `CancelConfirm`, `PlanBadge` | Local `view`, `isProcessing`, `alert`; calls `updateUserPlan`; contains monthly limit warning text when usage is `>= 3`. |
| Contact | No URL route; `appState === 'contact'` | `client/src/pages/ContactPage.tsx` | Public contact form/content page. | `SiteHeader` not used; page has its own header/back. | Local form fields and submitted state. |
| Features | No URL route; `appState === 'features'` | `client/src/pages/FeaturesPage.tsx` | Public features content. | Page-local card sections, `Button` | Static content; callbacks for back and get started. |
| Methodology | No URL route; `appState === 'methodology'` | `client/src/pages/MethodologyPage.tsx` | Public methodology content. | Page-local sections | Static content; back callback. |
| Changelog | No URL route; `appState === 'changelog'` | `client/src/pages/ChangelogPage.tsx` | Public changelog content. | Page-local cards | Static content; back callback. |
| About | No URL route; `appState === 'about'` | `client/src/pages/AboutPage.tsx` | Public about content. | Page-local sections/buttons | Static content; callbacks for back/get started/methodology. |
| Blog | No URL route; `appState === 'blog'` | `client/src/pages/BlogPage.tsx` | Public blog listing/content. | Page-local cards | Static content; back callback. |
| Terms | No URL route; `appState === 'terms'` | `client/src/pages/TermsPage.tsx` | Legal terms page. | Page-local content | Static content; back callback. |
| Privacy | No URL route; `appState === 'privacy'` | `client/src/pages/PrivacyPage.tsx` | Legal privacy page. | Page-local content | Static content; back callback. |
| Cookies | No URL route; `appState === 'cookies'` | `client/src/pages/CookiesPage.tsx` | Cookie policy page. | Page-local content | Static content; back callback. |
| Demo API routes | Backend, not frontend routes | `server/src/routes/demo.ts` | Demo session/user endpoints for testing without auth. | Repository methods | Not currently called by frontend services, which call `/sessions`, `/trending`, `/me` and use local fallback data. |

## 3. Component Map

| Component name | File path | What it does | Where it is used | Notes/problems spotted |
|---|---|---|---|---|
| `App` | `client/src/App.tsx` | Owns app-level pseudo-routing, auth/demo loading, service calls, and data mapping into UI types. | `client/src/main.tsx` | Does routing, data fetching, demo auth behavior, and DTO mapping in one file. No browser URL ownership. |
| `DashboardPage` | `client/src/pages/DashboardPage.tsx` | Dashboard shell and responsive panel orchestration. | `App` | Large component with layout, view switching, modal state, delete state, and chat/sidebar drawer logic. |
| `Dashboard` | `client/src/components/Dashboard.tsx` | Active forecast result container. | `DashboardPage` | Result screen assumes a valid prediction; no per-card empty/error handling. |
| `Sidebar` | `client/src/components/Sidebar.tsx` | Logo, new forecast button, search input, session list, user menu. | `DashboardPage` | Search input has no filtering logic. Empty session list renders only the section title. Some mojibake separator text is visible in source. |
| `CreateForecastView` | `client/src/components/CreateForecastView.tsx` | Forecast question form with dynamic placeholders, validation, hints, and submit CTA. | `DashboardPage` | Uses a local 500ms fake analyzing delay; hardcodes "Uses 1 of 3 free forecasts"; does not enforce plan limits. |
| `TrendingContext` | `client/src/components/CreateForecastContext.tsx` | Right/secondary panel for trending forecast suggestions during creation. | `DashboardPage` | Has an empty state for no trending forecasts. Source contains mojibake arrow characters. |
| `ChatPanel` | `client/src/components/ChatPanel.tsx` | Follow-up messages, suggested action buttons, message input. | `DashboardPage` | No explicit empty state; suggested actions currently log only. `onNewPrediction`, `currentQuestion`, and `currentAnswer` props are accepted but not used. |
| `PredictionOverview` | `client/src/components/cards/PredictionOverview.tsx` | Main BI/forecast card with probability gauge, confidence, fixed labels, and explanation. | `Dashboard` | Hardcodes "High Confidence", "Evidence Vol. High", "Consensus Strong", and a fixed bottom-line sentence instead of using available result fields. Uses `Math.random()` for SVG gradient id each render. |
| `MarketComparison` | `client/src/components/cards/MarketComparison.tsx` | Recharts bar comparison of Anizai vs market probability. | `Dashboard` | Has no empty/error state; uses `marketProbability || 0`, so missing market data appears as 0%. |
| `SentimentAnalysis` | `client/src/components/cards/SentimentAnalysis.tsx` | Recharts area chart for expert vs public sentiment. | `Dashboard` | No explicit empty state; footer reads latest values with optional chaining and can show `undefined%`. Contains exploratory comment block in production component. |
| `EvidenceTimeline` | `client/src/components/cards/EvidenceTimeline.tsx` | Evidence feed with filter tabs, key evidence, and timeline items. | `Dashboard` | No "no evidence" or "no matching filter results" state. Sorts display dates by `new Date(event.date)`, but UI dates can be strings like `Jan 5`. |
| `SettingsModal` | `client/src/components/SettingsModal.tsx` | Modal shell with settings navigation. | `DashboardPage` | Fixed two-column modal body; no mobile-specific stacking. |
| `ProfileSettings` | `client/src/components/settings/ProfileSettings.tsx` | Display name/email settings. | `SettingsModal` | Uses Firebase `auth.currentUser`; demo profile can be present without a Firebase user, causing save to no-op. |
| `AccountSettings` | `client/src/components/settings/AccountSettings.tsx` | Account details and sign-out action. | `SettingsModal` | Mixes Firebase auth values and demo `userProfile`. Source contains mojibake fallback dash. |
| `PreferenceSettings` | `client/src/components/settings/PreferenceSettings.tsx` | LocalStorage-backed theme/compact/explanation preferences. | `SettingsModal` | Preferences are saved but mostly not applied to the UI; note says dark mode not yet applied. |
| `NotificationSettings` | `client/src/components/settings/NotificationSettings.tsx` | Disabled notification toggles. | `SettingsModal` | All controls are "Coming soon"; no persisted user state. |
| `SecuritySettings` | `client/src/components/settings/SecuritySettings.tsx` | Provider display and re-auth controls. | `SettingsModal` | Depends on Firebase `auth.currentUser`; demo dashboard can show unknown provider/sign-in data. Source contains mojibake ellipsis/dash. |
| `SubscriptionSettings` | `client/src/components/settings/SubscriptionSettings.tsx` | Plan overview, usage meter, payment form, cancel/reactivate flows. | `SettingsModal` | Implements payment/loading/error/success states locally; plan limit is warning text only, not a blocking modal. Contains several mojibake strings. |
| `ConfirmDialog` | `client/src/components/ui/ConfirmDialog.tsx` | Generic confirm modal. | `DashboardPage` for deleting forecasts. | No loading indicator even though delete may be in progress; parent suppresses repeated actions. |
| `PlanCard` | `client/src/components/plans/PlanCard.tsx` | Pricing card. | `PlanSelection` | Static plan feature display. |
| `GoogleAuthButton` | `client/src/components/auth/GoogleAuthButton.tsx` | Branded auth button. | `LoginPage`, `SignupPage` | Reusable auth UI. |
| `Button`, `Input`, `Card`, `Badge` | `client/src/components/ui/*` | Shared primitive UI components. | Multiple pages/components | Thin Tailwind/class-variance wrappers. |
| Landing components | `client/src/components/landing/*` | Public landing sections. | `LandingPage` | Static marketing sections, responsive grid classes. |
| `TrendingForecasts` | `client/src/components/TrendingForecasts.tsx` | Standalone trending forecast list component. | Not found in current usage. | Appears unused by current app shell. |
| `SiteHeader` | `client/src/components/SiteHeader.tsx` | Reusable public header component. | Not found in current usage. | Public pages currently use local header/back patterns instead. |

## 4. Forecast Flow Map

1. User enters the app at `LandingPage`. Clicking the account/auth control calls `handleGoToLogin`, which currently calls `enterDemoDashboard` when no `userProfile` exists.
2. `enterDemoDashboard` in `App.tsx` loads sessions via `fetchSessions()` and trending items via `fetchTrendingForecasts(20)`, sets a demo user profile, loads the first session detail if present, and switches to the dashboard.
3. `fetchSessions`, `fetchSessionDetail`, `createSession`, `addSessionMessage`, and `deleteSession` try authenticated API requests first. On failure they use mutable local demo state derived from `mockData.ts`.
4. `App.tsx` maps API session detail into UI types: `Prediction`, `SentimentDataPoint[]`, `TimelineEvent[]`, and `ChatMessage[]`.
5. In `DashboardPage`, the sidebar `New Forecast` button sets `currentView` to `new-forecast`. The center panel becomes `CreateForecastView`; the right panel becomes `TrendingContext` on wide desktop, or appears below the form on smaller layouts.
6. Submitting a forecast calls `handleCreateSession` in `App.tsx`, which calls `createSession`, refreshes sessions, loads the created session detail, and returns to `currentView === 'dashboard'`.
7. The active result is displayed by `Dashboard`, which renders `PredictionOverview`, `MarketComparison`, `SentimentAnalysis`, and `EvidenceTimeline`.
8. Evidence is shown only through `EvidenceTimeline`. Evidence is mapped from `SessionDetail.evidence`; market evidence type is converted to `expert` in `App.tsx`.
9. Follow-up/chat is handled by `ChatPanel`. Sending a message calls `addSessionMessage`, then reloads the session detail. There is no visible assistant streaming/running response state in the frontend.

## 5. Existing UI States

| State | Is it implemented? | Where it is implemented | Notes |
|---|---:|---|---|
| loading | Yes, partial | `App.tsx` auth hydration; `DashboardPage` center panel; `CreateForecastView`; `SubscriptionSettings`; `ProfileSettings`; `SecuritySettings` | Dashboard loading only replaces center panel with "Loading forecasts..."; sidebar/chat remain visible. |
| empty | Yes, partial | `DashboardPage` no prediction; `TrendingContext` no trending forecasts | No generic empty state across cards. |
| error | Yes, partial | `App.tsx` `authError` toast; settings forms/subscription alerts; `api.ts` error classes | Main dashboard API errors show a top toast only. Service fallbacks can hide API errors by switching to demo data. |
| running | Partially | `SessionStatus` includes `running`; `App.mapSessionStatus` maps it to sidebar `volatile` | There is no distinct running UI, progress state, or result placeholder. |
| done | Partially | Demo/API session status; `createSession` fallback creates `done`; result renders when `prediction` exists | UI does not display `done`; it maps backend status to `stable`/`volatile`. |
| failed | Partially | `SessionStatus` includes `failed`; `App.mapSessionStatus` maps it to `volatile`; API/session types include `errorCode` and `errorMessage` | No failed-result screen or visible error message from session fields. |
| awaiting_clarification | No | Not found in frontend types or UI | No clarification state detected. |
| no sessions | Partially | `DashboardPage` shows "No forecasts yet" when no `prediction`; `Sidebar` silently has an empty list | Center panel covers the no-active-prediction case; sidebar has no no-sessions message. |
| no evidence | No | `EvidenceTimeline` | Empty evidence array renders only header/summary/timeline line with no copy. |
| no follow-up messages | No | `ChatPanel` | Empty messages array leaves blank scroll area above suggested actions/input. |
| plan limit exceeded | Partially | `SubscriptionSettings` usage meter warning when `monthlyForecastsUsed >= 3`; server `user.repository.ts` can throw monthly limit error | No plan limit modal found. `CreateForecastView` hardcodes usage text and does not block. |

## 6. Responsive Layout Notes

Mobile is handled primarily in `DashboardPage` with `lg:hidden`: a fixed top mobile header, slide-out left sidebar (`w-80`), slide-out right chat panel (`w-96`), and a floating chat button. The `w-96` chat drawer is wider than some small mobile viewports, so horizontal clipping/overflow risk exists.

Tablet / smaller desktop is handled with `hidden lg:grid xl:hidden` using a two-column layout: 280px sidebar plus center content. Chat becomes a fixed right drawer (`w-96`) opened by the floating chat button. During new forecast creation, `TrendingContext` is rendered below the center panel instead of as a persistent right column.

Desktop / wide desktop is handled with `hidden xl:grid xl:grid-cols-[280px_minmax(0,1fr)_360px]`, giving sidebar, center dashboard, and fixed right panel.

Inside the result screen, `Dashboard` uses a single-column card stack, with market comparison and sentiment side-by-side at `lg`. `PredictionOverview` switches from stacked to horizontal at `xl`, and its metric cards switch to three columns at `sm`.

Public pages and landing sections use Tailwind breakpoints such as `sm`, `md`, and `lg` for grids and typography. Settings modal has `max-w-3xl`, fixed sidebar `w-48`, and only content padding changes at `sm`; there is no small-screen modal stacking.

Obvious layout risks: fixed mobile chat width may exceed viewport, settings modal two-column layout can be tight on mobile, evidence filter tabs may crowd on narrow widths, long forecast questions rely on line clamping in sidebar but dashboard header may grow substantially, and chart labels/tooltips may be cramped on small cards.

## 7. Components That Need Polish Later

- `DashboardPage`: split layout orchestration from dashboard state/actions; clarify ownership of `currentView`, drawers, modals, and deletion.
- `Sidebar`: implement search or remove the inactive control; add no-sessions state; fix mojibake separator.
- `CreateForecastView`: replace hardcoded usage text with real plan/usage data and real submission/loading behavior.
- `ChatPanel`: add no-message state, define suggested action behavior, and remove unused props or use them.
- `PredictionOverview`: bind labels and bottom-line copy to result fields instead of hardcoded values.
- `MarketComparison`, `SentimentAnalysis`, `EvidenceTimeline`: add empty/error states and better missing-data handling.
- `SettingsModal`: improve mobile behavior.
- `SubscriptionSettings`: separate payment mock/state from settings display and clean up mojibake strings.
- Public page headers: decide whether to use `SiteHeader` consistently or remove unused header component later.

## 8. Missing or Incomplete UI States

- Distinct active/running forecast state.
- Distinct failed forecast state using `errorCode`/`errorMessage`.
- Awaiting clarification state.
- No-evidence state.
- No matching evidence filter state.
- No follow-up messages state.
- No sessions state in the sidebar.
- Plan limit exceeded modal/blocking flow.
- API unavailable/offline state that is visible to users instead of silently falling back to demo data.
- Empty chart states for sentiment and market comparison.
- Loading/sending state for follow-up chat messages.

## 9. Risk Notes Before Editing

- `App.tsx` combines pseudo-routing, data loading, fallback demo behavior, auth-ish state, DTO mapping, and dashboard event handlers.
- There is no real URL router, so "routes" are not deep-linkable and browser back/forward will not map to screens.
- Services silently fall back to local demo data on API/auth failure, which can mask integration problems.
- Backend statuses (`draft`, `running`, `done`, `failed`) are collapsed into frontend `stable`/`volatile`, losing important UI state.
- Several result fields available in `SessionResult` are not used by the cards; some card content is hardcoded.
- `SessionDetail.result` may be null, but `toPrediction` still creates an "Analysis in progress." prediction from session fields.
- `TrendingForecasts` and `SiteHeader` appear unused, suggesting duplicated UI patterns.
- Settings components depend directly on Firebase `auth.currentUser`, while the main app often uses a demo `userProfile`.
- `SubscriptionSettings` contains mock payment processing and real plan update calls in one component.
- Multiple files contain mojibake characters in visible strings/comments, likely from encoding issues.
- The dashboard mobile layout uses fixed-width drawers, which may be fragile on narrow devices.
