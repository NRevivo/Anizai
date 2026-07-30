# frontend_ui.md
> Domain: C — Frontend / BFF
> Type: Spec
> Last updated: 2026-07-18
> TL;DR: The React SPA's screens and components — the routerless `AppState` model, the 12 screens, the dashboard card composition, the two live-render rules that govern the agent timeline, and which UI states actually exist. Open this before changing a screen, adding a card, or reasoning about what renders during a run.

## Navigation
- §1 — Overview — the routerless model and where state lives
- §2 — Screen Matrix — the 12 `AppState` values and their files
- §3 — Dashboard Composition — shell → cards → subcomponents
- §4 — Live Surfaces — Rule A and Rule B, the agent timeline
- §5 — UI States — status, loading, empty, error, and their implementations
- §6 — Styling — Tailwind, primitives, palettes, markdown
- §7 — Known Constraints

---

## §1 — Overview

The client is a Vite 6 / React 19 SPA. **There is no router library.** Navigation is a
`useState<AppState>` union in `client/src/App.tsx:52-64`, and each screen is rendered
conditionally. Consequences that follow from this and cannot be worked around locally:
no deep links, no browser back/forward, no URL-addressable forecast.

`App.tsx` (962 lines) owns four responsibilities simultaneously: pseudo-routing, all
data fetching, all view-model mapping (`frontend_contracts.md §5`), and the three
Firestore listener subscriptions. `DashboardPage.tsx` (744 lines) owns the dashboard
shell — panel layout, drawers, modals, and the status-panel rendering. Tracked KG-C-3.

Component files are otherwise small and composable. The forecast overview in particular
was decomposed out of a former god-component into `cards/predictionOverview/`
(§3.2).

---

## §2 — Screen Matrix

`AppState` has exactly **12** values (`App.tsx:52-64`). Each maps to one page component.

| `AppState` | Page component | Purpose |
|---|---|---|
| `landing` | `pages/LandingPage.tsx` | Public marketing page |
| `login` | `pages/LoginPage.tsx` | Google + email sign-in |
| `signup` | `pages/SignupPage.tsx` | Account creation |
| `plan-selection` | `pages/PlanSelection.tsx` | Free / Premium choice |
| `dashboard` | `pages/DashboardPage.tsx` | The logged-in workspace |
| `contact` | `pages/ContactPage.tsx` | Public |
| `features` | `pages/FeaturesPage.tsx` | Public |
| `methodology` | `pages/MethodologyPage.tsx` | Public |
| `about` | `pages/AboutPage.tsx` | Public |
| `terms` | `pages/TermsPage.tsx` | Legal |
| `privacy` | `pages/PrivacyPage.tsx` | Legal |
| `cookies` | `pages/CookiesPage.tsx` | Legal |

> `docs/ui-map-task-0-1.md §2` lists two further screens — **Changelog** and **Blog**.
> Neither `ChangelogPage.tsx` nor `BlogPage.tsx` exists in `client/src/pages/`. That
> doc is superseded by this section.

**Landing composition** (`pages/LandingPage.tsx:1-7`) — six sections plus a shell:
`Hero`, `DashboardPreview`, `HowItWorks`, `WhatYouGet`, `QuestionsWeTrack`,
`ClosingCTA`, wrapped in `components/site/PageShell`.

> The same stale doc lists `ProductExplanation`, `UIShowcase`, `WhoItsFor`, and
> `FinalCTA` as the landing children. None of those files exist.

**Auth is real.** Sign-in runs through Firebase (`services/auth.service.ts`), and
`App.tsx:465` subscribes to `subscribeToAuthState` to hydrate the session. There is no
demo-dashboard shortcut on the auth path.

---

## §3 — Dashboard Composition

### §3.1 Shell

```
DashboardPage.tsx                       ← shell: layout, drawers, modals, status panels
├── Sidebar.tsx                         ← session list, search, new-forecast, user menu
├── (center) CreateForecastView.tsx     ← when currentView === 'new-forecast'
├── (center) status panel + AgentEventsTimeline   ← when status !== 'done'  (§4)
├── (center) Dashboard.tsx              ← when status === 'done' and a Prediction exists
│   ├── cards/predictionOverview/       ← §3.2
│   ├── cards/MarketComparison.tsx
│   ├── cards/SentimentAnalysis.tsx
│   ├── cards/MarketPriceHistory.tsx    ← §3.4
│   └── cards/EvidenceTimeline.tsx      ← §3.3
├── (center) CreateForecastView.tsx     ← §3.1a market-first new-forecast screen
│   ├── MarketPicker.tsx                ← choose an outcome within an event
│   └── FreeformQuestionModal.tsx       ← write-your-own question
├── (right) ChatPanel.tsx               ← follow-up conversation
├── SettingsModal.tsx → settings/*      ← 6 sections
└── ui/ConfirmDialog.tsx                ← delete confirmation
```

`DashboardPage` holds 14 `useState` hooks covering drawers, modals, deletion,
clarification submit, retry submit, and `currentView` (`'dashboard' | 'new-forecast'`).

**The right rail is dropped on `new-forecast`.** The xl grid switches from
`[252px_1fr_304px]` to `[252px_1fr]` and the third column is not rendered
(`DashboardPage.tsx`, the `currentView === 'new-forecast'` ternaries around the xl
grid). The markets that column used to hold are now the centre of the page, so
keeping it would have the screen compete with itself.

`CreateForecastContext.tsx` (`TrendingContext`) **was deleted** in the 2026-07-28
redesign — its three render sites (right rail, 2-col stack, mobile stack) are gone
and its card rendering moved into `CreateForecastView`. Docs referring to it are stale.

### §3.1a The new-forecast screen — `CreateForecastView.tsx`

**Markets lead; free text is a peer, not the default.** Until 2026-07-28 this screen
was a single empty textarea with the live markets demoted to a side rail (desktop) or
pushed below the fold (mobile). That put the weakest path first: a self-written
question resolves to no market and therefore can never carry a benchmark, while every
card here does.

Composition, top to bottom: header → **"Ask your own question"** button (opens
`FreeformQuestionModal`) → **search field** → **"Trending on Polymarket"** grid of
event cards (1 column, 2 from `md`). Both entry points open the same class of modal,
so neither is the buried one. `isLoading` renders a six-card skeleton grid rather than
flashing the empty state; an empty feed says so and still offers the free-text path.

**Search** (`GET /trending/search`) replaces the grid in place: the section heading
becomes `Results for "…"` with a match count, and a clear button restores trending.
Queries debounce at 300 ms, require 2 characters, and a `cancelled` flag stops a slow
earlier response overwriting a newer one. Three states are kept visually distinct —
skeletons while searching, a **red** retryable panel on failure, and a neutral empty
panel that explains the topic filter ("Sport and entertainment markets are excluded")
plus a "Back to trending" action. Conflating the last two would make an on-topic-only
search look broken. Browsing alone can only reach ~46 events; search reaches the whole
catalogue.

Card anatomy: event title; a single large percentage for a binary event, or the
leading outcomes with their percentages for a field; then 24h volume, the
**selectable** outcome count (`markets.length`, falling back to `marketCount` — see
`frontend_contracts.md §3.9`), and a Choose/Forecast affordance.

`FreeformQuestionModal` carries the whole question form — min/max length validation,
timeframe and yes/no hints, character counter, plan-limit handling with the "Review
plans" action, and the rotating placeholder. It states in one line at the point of
entry that a self-written question has **no market benchmark**, and submits with
`conditionId: null`.

> **Never call `crypto.randomUUID()` directly — use `randomUUID()` from `lib/utils`.**
> `crypto.randomUUID` exists only in a **secure context** (https or `localhost`), so it
> is `undefined` when the dev server is opened from a phone on the LAN
> (`http://192.168.x.x:5173`). This modal generates an idempotency key during render;
> calling the missing API there threw and unmounted the React tree, producing a
> **blank white screen on mobile and nowhere else**. The helper falls back to
> `crypto.getRandomValues` (available on insecure origins) rather than `Math.random` —
> these are idempotency keys, and a collision would return someone else's forecast.

### §3.1b Market selection — `MarketPicker.tsx`

A trending card does not submit on click. It resolves to **one market's real
question**, because the event title is a display label: submitting "Fed Decision in
July?" gives the pipeline nothing to match, while "Will there be no change in Fed
interest rates after the July 2026 meeting?" is a market with a price behind it.
Measured on the live feed, 15 of the top 20 visible titles could never text-match any
market question. **Display the short label; submit the full question.**

| Event shape | Behaviour |
|---|---|
| Binary, inline market present | **Submits immediately, no picker, no round-trip** — the market ships inline on the list payload |
| 2–8 selectable markets | Lists every market: short label, full question, current % |
| > 8 selectable markets | Opens shortlisted to the 8 most likely, with a **"Show all N outcomes (+K more)"** expander |

Branching is on **`markets[0]` existing**, not on `marketCount === 1`
(`CreateForecastView.tsx`, `handleEventSelect`): a binary event whose only leg is inactive
reports `marketCount: 1` with `markets: []`, and trusting the count there would submit
`undefined`. That case falls through to the picker, which fetches and reports honestly.

**The picker holds no free-text input** (removed 2026-07-28). It briefly offered one on
shortlisted fields, which was wrong twice over: inside a dialog titled "Israel x Iran
ceasefire continues through…?", a control labelled *"Ask your own question"* reads as
"ask about **this** event in my own words", when it actually abandons the event for a
question with no benchmark — and it duplicated the screen-level button added in the
same redesign, under the same name with different behaviour. It also failed to solve
the need it was there for: a user wanting the 9th of 11 outcomes needs *that market*,
which free text cannot reach. The expander does. Free text now lives in exactly one
place, `FreeformQuestionModal` (§3.1a).

**Loading, error and empty are three distinct states.** `fetchTrendingMarkets` rethrows
by design, so a failed fetch renders a red retryable "Couldn't load outcomes" — never
an empty list, which would read as "this event has nothing to forecast". Genuinely
empty (every leg closed) gets its own honest message.

Counts shown to the user come from `markets.length`, not `marketCount`: the Republican
nominee field reports `marketCount: 128` but yields 42 selectable markets, so the
footer reads "Showing the 8 most likely of 42 outcomes."

Accessibility: `role="dialog"` + `aria-modal` + `aria-labelledby`, Escape closes,
initial focus moves to the dialog, the async region is `aria-live="polite"`, options
are real `<button>`s at `min-h-11` with `focus-visible` rings, and the close control
is an SVG with an `aria-label` (no emoji icons).

### §3.2 `cards/predictionOverview/`

Six components plus two unit-tested pure modules:

| File | Role |
|---|---|
| `index.tsx` | Composition root; owns markdown rendering config |
| `ProbabilityRing.tsx` | The probability dial |
| `VerdictBanner.tsx` | Verdict, deadline, thesis |
| `MetricsRow.tsx` | Probability / confidence / consensus tiles |
| `DriversAndHeadwinds.tsx` | `keyFactors` split by `direction` |
| `GapsNotice.tsx` | `whatIDidntFind` |
| `ReasoningChain.tsx` | `reasoningChain` steps |
| `lib/deriveVerdict.ts` | Pure verdict decision table (+ `.test.ts`) |
| `lib/extractDeadline.ts` | Heuristic deadline parse (+ `.test.ts`) |

**`deriveVerdict`** maps `{ finalProbability, confidence }` to one of six actions.
Rules are evaluated in order, first match wins (`lib/deriveVerdict.ts:36-57`):

| # | Condition | Verdict | Tone |
|---|---|---|---|
| 1 | `confidence < 0.2` | `insufficient` — "Don't Bet — Insufficient Evidence" | warning |
| 2 | `probability ≥ 0.7` and `confidence ≥ 0.6` | `strong-bet-yes` — "Strong Yes" | positive |
| 3 | `probability ≤ 0.3` and `confidence ≥ 0.6` | `strong-bet-no` — "Strong No" | negative |
| 4 | `0.4 ≤ probability ≤ 0.6` | `avoid` — "Coin Flip — Avoid" | neutral |
| 5 | `probability ≥ 0.6` | `lean-yes` — "Lean Yes" | positive |
| 6 | `probability ≤ 0.4` | `lean-no` — "Lean No" | negative |

Rule 1 is the product-critical one: **low confidence overrides any probability**, so a
0.9 probability on thin evidence never renders as a bet. The signature takes only
probability and confidence — there is no consensus parameter.

> `docs/archive/backend-audit.md` Drift #2 describes a `consensus_score: number` parameter on
> `deriveVerdict`. That parameter no longer exists. Superseded.

**`extractDeadline`** is a heuristic, LLM-free parse of the question string, tried in
pattern order: explicit ISO `YYYY-MM-DD`, then `before YYYY`, then further patterns.
Month-only matches resolve to the last day of that month. Returns `null` on no match.

**Markdown.** `summaryMarkdown` renders through `react-markdown` with an explicit
`Components` map, because **no Tailwind typography plugin is installed** — `prose`
classes are unavailable, so every element is styled by hand (`index.tsx:14-30`).
Summaries longer than `SUMMARY_COLLAPSE_THRESHOLD = 400` characters collapse into a
`<details>` disclosure.

### §3.4 `MarketPriceHistory.tsx`

The market's own YES price over the life of the market, from
`sessions/{id}/predictionSeries`. Full width, placed directly under the
MarketComparison/SentimentAnalysis row and above the evidence feed: it extends the
market benchmark rather than competing with it — `MarketComparison` shows our number
against one market price, this shows whether that price was stable or moving
underneath it.

- **Absent vs. empty are worded differently.** Both reach the card as `points: []`,
  so it branches on `tier`: `tier_2` says the forecast is freeform and resolves to no
  market; anything else says no price history was recorded. Neither is a loading
  state — the card only mounts inside `Dashboard`, which requires a finished forecast.
- **Density.** ~712 points at ~10-minute resolution is the expected contract volume,
  rendered in full with **no downsampling** — it is a single SVG path, and
  downsampling would erase exactly the spikes a bettor is looking for. `dot={false}`
  above 3 points and `isAnimationActive={false}` keep that cheap.
- **`confidence` is never plotted.** The pipeline writes a constant `1.0`; the field
  is dropped at the mapper so no band can be derived from it (see contracts §6).
- **Axis rules live in `lib/marketPriceChart.ts`** (19 unit tests) because a
  mis-scaled axis still renders a plausible-looking line:
  - `MIN_AXIS_SPAN_PCT = 20` — a market that traded 54–56% is genuinely flat, and an
    auto-fitted axis would magnify that into a dramatic swing. The floor keeps small
    moves honest; a large move still fills the chart. The domain also snaps outward
    to multiples of 5 so ticks are round numbers.
  - The Anizai reference line joins the domain extent, or it can land outside the
    plot area and silently vanish — the one comparison the card exists to make.
  - `buildTimeTicks` guarantees **distinct** x labels. Date-only labels repeat as
    soon as two ticks share a day (a 2.1-day series rendered "Jun 15" five times,
    which reads as a rendering fault), so the clock takes over on repeat.
- **The reference line is unlabelled in-plot.** An in-plot label sits on top of the
  data whenever the forecast lands near the market's range — the common case — so
  the value is carried by a legend below the chart instead.

### §3.3 `EvidenceTimeline.tsx`

The most behavior-dense card (366 lines). Confirmed behaviors:

- **Deduplication** — `dedupeEvents()` (`:82`) keys rows by both `id` and `evidenceId`,
  merging duplicates and OR-ing `isKeyEvidence` across the merged set. A row's
  `evidenceIds` retains every id that collapsed into it.
- **Grouping** — rows are grouped by `sourceType` in a fixed `SOURCE_ORDER`; only
  types actually present render a group or a filter tab (`:266-283`).
- **Filtering** — an `all` tab plus one per present type.
- **Real links** — the title renders as `<a href={row.url ?? undefined}>` (`:203`),
  not a styled non-link.
- **Factor highlight** — `highlightedEvidenceIds` (set by clicking a
  Drivers/Headwinds row) is matched against each row's `evidenceIds`. A non-empty
  highlight **forces the filter back to `all`** so highlighted rows can't hide behind
  an active tab, and scrolls the card into view (`:255-263`).
- **Impact** — read from `impactOnForecast` only; the legacy `impact` field is absent
  from the component.

The factor→evidence round trip is coordinated by `Dashboard.tsx`, which holds
`highlightedEvidenceIds` state and clears it after
`HIGHLIGHT_DURATION_MS = 3500` (`Dashboard.tsx:14-35`). It re-creates the array on every
selection (`[...evidenceIds]`) so re-clicking the same factor re-triggers the scroll.

---

## §4 — Live Surfaces

Two named rules govern what renders while a forecast is in flight. Both are enforced in
code and commented as such.

### §4.1 Rule A — the reasoning panel is live-only

`AgentEventsTimeline` renders **only** when status is `queued`, `claimed`, or `running`
(`DashboardPage.tsx:488`):

```tsx
{['queued', 'claimed', 'running'].includes(activeSessionState.status) && (
    <AgentEventsTimeline events={agentEvents} isLoading={isAgentEventsLoading} />
)}
```

`failed` and `awaiting_clarification` still show their status panel, but never the
timeline. Once status is `done`, the whole status-panel branch is replaced by the
`Dashboard` card stack — so the reasoning trace is not part of the finished forecast.

### §4.2 Rule B — only the current run's events render

`selectCurrentRunEvents(events, currentRunId)` (`client/src/lib/agentEvents.ts`) filters
the subcollection to events whose `runId` matches the session document's
`currentRunId`, sorted by `sequence`. **A null `currentRunId` yields an empty array.**
Full contract in `frontend_contracts.md §4.2`.

### §4.3 `AgentEventsTimeline` rendering

Status → visual mapping (`AgentEventsTimeline.tsx:14-49`):

| Condition | Dot | Label |
|---|---|---|
| `status === 'failed'` **or** `type === 'error'` | rose | Failed |
| `status === 'running'` / `'pending'` | amber | Running / Pending |
| `status === 'done'` | teal | Done |
| anything else | slate | Event |

The final branch is a deliberate degrade-not-crash path for unknown or missing status
values. Durations format as `ms` below 1s, `s` above, dropping the decimal past 10s.
`payload` is never rendered.

**In production this component shows its empty state.** The read path is complete and
verified in this repo; events are absent because the deployed agent image predates the
emission mechanism. See `frontend_overview.md §2` and KG-C-7 — that is upstream
(Domain-B) state relayed from its owner, not verifiable here.

---

## §5 — UI States

### §5.1 Per-status panels

`DashboardPage.renderStatusPanel()` returns a distinct `StateMessage` per session
status (`DashboardPage.tsx:279-451`). All five non-`done` statuses are implemented:

| Status | Surface |
|---|---|
| `queued` | Status panel |
| `claimed` | Status panel |
| `running` | Status panel |
| `failed` | Status panel + **retry action**; `failedRetryError` renders inline on failure |
| `awaiting_clarification` | Candidate picker + submit; `clarificationError` inline |
| `done` | Panel replaced by the `Dashboard` card stack |

Clarification and retry each have their own in-flight flag (`isSubmittingClarification`,
`isRetryingFailedSession`) and each guards on the current status before firing
(`:201`, `:220`).

> `docs/ui-map-task-0-1.md §5` records `awaiting_clarification` as **"No — not found in
> frontend types or UI"** and reports no failed-state screen. Both are implemented.
> Superseded.

### §5.2 The shared state primitive

`components/ui/StateMessage.tsx` is the single presentation primitive for non-content
states — four variants (`empty`, `loading`, `error`, `warning`), with `compact` and
`align` modifiers and an optional `action` slot. The `loading` variant renders a
spinner. It is used across the dashboard, chat, evidence, trending, and settings.

### §5.3 Coverage

| State | Implemented | Where |
|---|---|---|
| Dashboard loading | ✅ | `DashboardPage` center panel |
| No sessions | ✅ | Center panel "No forecasts yet" + create action; `Sidebar` empty state |
| Session selected, none active | ✅ | "Select a forecast" |
| No search results | ✅ | `Sidebar` — "Try a different search term." |
| No evidence / no filter match | ✅ | `EvidenceTimeline:355-360` |
| No chat messages | ✅ | `ChatPanel:81` |
| Awaiting assistant reply | ✅ | `ChatPanel` `ThinkingIndicator`, driven by `followUpPendingState === 'thinking'` |
| Pending follow-up overdue | ✅ | `followUpPendingState === 'stalled'` → static `StateMessage` warning |
| Send lock while busy | ✅ | `isSendDisabled` gates the send button and the suggested-action chips |
| Composer withheld until a result exists | ✅ | `shouldShowFollowUpComposer` (`lib/followUpComposer.ts`) → `DashboardPage:170`, consumed at `ChatPanel:151` |
| Thread pinned to newest message | ✅ | `lib/followUpScroll.ts` + `threadRef` effects in `ChatPanel.tsx` |
| Agent timeline pinned to newest step | ✅ | `findScrollableAncestor` (`lib/autoScroll.ts`) + effects in `AgentEventsTimeline.tsx` |
| Failed session | ✅ | Status panel + retry |
| Clarification | ✅ | Candidate picker |
| Plan limit | ✅ | Structured `PLAN_LIMIT_EXCEEDED` surfaced from the API (`frontend_api.md §6.1`) |
| Empty market / sentiment charts | ✅ | Cards fall back to `StateMessage` |
| Agent events empty | ✅ | `AgentEventsTimeline` empty state |

**Sidebar** implements search (`searchQuery` filter, `Sidebar.tsx:33-34`) and a
per-status dot with label for all six statuses (`:47-61`). It displays probability
(`—` when null) but **not confidence** — see §7.

**ChatPanel** renders assistant content through `react-markdown`, shows up to the
provided `suggestedActions` as chips, and disables the send button and the chips
together while a send is in flight or the session is busy.

**Composer visibility gate.** The composer and the suggested-action chips are not
rendered at all until the session has a completed forecast result — there is nothing
to ask a follow-up about before then. The gate is `isComposerVisible`, derived in
`DashboardPage.tsx:170` via the pure helper `shouldShowFollowUpComposer`
(`src/lib/followUpComposer.ts`, 14 unit tests) as `status === 'done' &&
hasForecastResult`, and passed to all three `ChatPanel` instances. The logic lives in a
helper for the same reason Rule B's does: the dashboard sits behind auth, so the helper
is the part of the gate provable without a signed-in session.

Both halves are required, and neither substitutes for the other:

- `hasForecastResult` comes from `App.tsx` as `activeSessionDetail?.result != null`.
  It cannot be derived from `prediction` — `toPrediction` (`App.tsx:108-121`) returns
  a **non-null** `Prediction` for any loaded session, defaulting probability and
  confidence to `0` while the run is still in flight. `SessionDetail.result` is
  `SessionResult | null` and the BFF returns `null` whenever `sessionResults/{id}` is
  absent (`server/src/repositories/session.repository.ts:203-207`), independent of
  session status.
- `status === 'done'` is still needed because a result can outlive a re-queued run
  (the clarification path re-queues an existing session).

Per-state behaviour:

| Session state | Message history | Composer + chips |
|---|---|---|
| New session, no result yet | visible (empty state) | hidden |
| `queued` / `claimed` / `running` | visible | hidden |
| `awaiting_clarification` | visible | hidden |
| `failed` | visible | hidden — the session has no result to discuss; the recovery path is the **Retry forecast** button in the centre status panel, not a follow-up |
| `done` with a result | visible | **visible** |
| `done` but result doc missing | visible | hidden |
| Reopened session with a prior thread | visible | visible (given `done` + result) |

The history is never gated on `isComposerVisible` — only the input surface is.
`isSendDisabled` also folds in `!isComposerVisible` defensively, so no stray handler
can send without a result even if the markup gate is bypassed.

**Pending follow-up indicator.** A follow-up awaiting its answer renders
`ThinkingIndicator` (local to `ChatPanel.tsx`): three staggered dots in an
assistant-side bubble, so it sits where the answer will land. It replaced a static
"Waiting for response" notice.

- **Motion** — the `thinking-dot` keyframes and `animate-thinking-dot` utility are
  defined in `tailwind.config.js` (the repo's only custom animation). Delays are
  `0 / 160 / 320 ms` via inline `animationDelay`, since no arbitrary
  animation-delay utility is configured. Both opacity and offset live entirely in
  the keyframes, so the un-animated base state is three solid, fully visible dots.
- **Reduced motion** — `motion-reduce:animate-none` on each dot, backed by the
  global `prefers-reduced-motion` rule in `index.css`. Verified in the built CSS:
  the `motion-reduce` block follows the base utility, so `animation: none` wins and
  the fallback is three static dots at full opacity.
- **Accessibility** — the dot row is `aria-hidden`; the wrapper is
  `role="status" aria-live="polite"` carrying an `sr-only` sentence, so the state is
  announced as prose rather than as decorative bullets.

**How the indicator clears.** `followUpPendingState` is derived from the trailing
user message via `lib/followUpPending.ts` (`findPendingFollowUp` +
`resolveFollowUpPendingState`, 15 unit tests). All exit paths were traced in source:

| Path | Mechanism | Clears? |
|---|---|---|
| Success | Hub writes the reply and flips the user message `sent → answered` in one batch; `subscribeToSessionMessages` pushes both | ✅ |
| Hub gives up | Hub marks the user message `failed`; preserved through `toChatMessage` | ✅ |
| `POST /messages` rejects | `App.tsx` rolls the optimistic `pending` message out of `pendingMessages` | ✅ |
| Hub never answers | Message stays `sent` in Firestore forever — the documented deployed-image gap | ⚠️ Ages out to `stalled` after `FOLLOW_UP_STALL_MS` (90 s) |

The last row is why `stalled` exists: without it the animation would spin forever,
and would keep spinning across a reload because the unanswered `sent` status is
persisted. Age is measured from the **message timestamp**, not from mount, so
reopening a session with a long-abandoned follow-up reports `stalled` immediately
instead of animating for another full grace period. `DashboardPage` ticks a 5 s
interval only while something is pending.

Note that `stalled` changes the **indicator only**. `isSendLocked` still derives from
`isAwaitingAssistantResponse`, so the composer's send button stays locked for that
session — the permanent-send-lock caveat in `sprint-24-25-frontend-tasks.md` is
unchanged and remains a separate contract decision.

**Thread auto-scroll.** The follow-up thread keeps itself pinned to the newest
message. Policy lives in `lib/followUpScroll.ts` (19 unit tests); the effects live in
`ChatPanel.tsx`.

The scroll target is **the thread container itself** — the single `overflow-y-auto`
div at `ChatPanel.tsx:117`, held by `threadRef`. It is deliberately *not*
`scrollIntoView`: this panel is nested in the dashboard grid, and scrolling an element
into view walks up every scrollable ancestor and drags the whole page. Verified in a
browser that `container.scrollTo(...)` leaves `window.scrollY` untouched.

| Trigger | Behaviour |
|---|---|
| Opening / switching to a session with existing follow-ups | instant, in `useLayoutEffect` — no scroll-from-top flash |
| New message (user or assistant) | smooth |
| Thinking indicator appearing | smooth |
| Indicator replaced by the answer | smooth |
| Composer or suggested chips mounting | instant re-pin (layout correction, not a new message) |

Three details that are easy to get wrong and are load-bearing here:

- **Pinned-ness is tracked, not measured.** `isPinnedToBottomRef` is updated by the
  container's `onScroll`. Measuring inside the effect would be wrong: by then the new
  content has already pushed the bottom away, so every update after the first would
  read as "user scrolled up" and suppress itself.
- **The initial jump waits on `isLoading`.** `App.tsx` clears `sessionMessages` on a
  session switch but the previous session's messages stay rendered until the new
  snapshot lands. Without the gate the one instant jump would be spent on the outgoing
  thread and the real content would animate in from the top.
- **`useLayoutEffect` runs without a dependency array** so it re-checks each render
  until content exists; `hasPerformedInitialJumpRef` makes it a no-op thereafter.

Suppression: if the user has scrolled more than `AUTO_SCROLL_THRESHOLD_PX` (64px,
under one bubble height) from the bottom, auto-scroll is suppressed — they are reading
history. It resumes as soon as they return to the bottom. **The one exception is their
own just-sent message**, which always scrolls regardless of position.

`prefers-reduced-motion` downgrades smooth to instant (`resolveScrollBehavior`). A
session switch resets pinned state, the initial-jump flag and the last-seen message id,
so nothing carries between sessions — `ChatPanel` takes a `sessionId` prop purely for
this. The `ResizeObserver` is disconnected on unmount; the scroll handler is a React
`onScroll` and needs no manual teardown.

**Agent timeline auto-scroll.** `AgentEventsTimeline` keeps the newest processing step
in view during a run. It shares the *policy* in `lib/followUpScroll.ts` with the
follow-up thread, but the *container semantics differ* — see below.

| | Follow-up thread | Agent timeline |
|---|---|---|
| Scroll container | its own `overflow-y-auto` div, `ChatPanel.tsx:117` | **none of its own** — resolved from the DOM at run time |
| Initial jump | instant, `useLayoutEffect` | not applicable — mounts empty and fills as events arrive |
| Own-message override | yes | no — every event is agent-authored |
| Teardown | `ResizeObserver.disconnect()` | `removeEventListener` + reset container to top |

**The timeline has no scroll container.** It is a card that grows inside the centre
panel. The container is found by `findScrollableAncestor` (`lib/autoScroll.ts`), which
walks up from the card's root and returns the first ancestor whose computed
`overflow-y` is scrollable, stopping at `<body>` and returning `null` rather than ever
falling back to the document.

The ancestor it finds is `DashboardPage`'s centre wrapper — written as
`h-full flex items-center justify-center p-4 sm:p-8 **overflow-x-hidden**`. That
element declares no `overflow-y` utility at all, but CSS forces the other axis away
from `visible` when one axis is not `visible`, so it **computes to `overflow-y: auto`**
and silently is the scroll container. Measured in a browser against the real class
chain: the centre column above it (`overflow-hidden`) is not scrollable, this wrapper
is, and `document.documentElement` is not — the dashboard shell is
`h-screen overflow-hidden`, so **the page itself never scrolls**. Resolving at run time
also handles the three simultaneously-mounted layout trees without breakpoint logic:
each copy finds its own ancestor.

Policy matches the follow-up thread: smooth scroll on a new event, suppressed once the
user is more than `AUTO_SCROLL_THRESHOLD_PX` from the bottom, resumed when they return,
instant under `prefers-reduced-motion`. `decideAutoScroll` is called with
`isOwnNewMessage: false` — the override has no analogue here, and passing the flag keeps
one shared policy instead of a near-duplicate. On unmount (the run finishing, or a
session switch) the resolved container is scrolled back to the top, so the result view
that replaces the timeline does not inherit a scroll position that belonged to the
timeline's height.

**Centring in the centre panel — use `my-auto`, never `items-center`.** All three
`renderCenterPanel` wrappers (`DashboardPage.tsx:497`, `:520`, `:547`) are
`h-full flex justify-center …` with the child carrying `my-auto`. The wrappers are
`flex-direction: row` (never declared, so the default), which makes the **vertical**
axis the cross axis — hence `my-auto`, not `mx-auto`.

They previously used `align-items: center` on the wrapper. Cross-axis centring has no
concept of a scroll origin: when the content is taller than the container it is centred
*around* the container, so the overflow above the top edge sits at a negative offset
that no amount of scrolling can reach. During a long run the "Active forecast" question
card was simply unreachable. An auto cross-axis margin gives the same centred result
when there is free space and collapses to `0` when there is not, so the top stays
reachable.

Measured in a browser across all three layout trees, before → after:

| Tree | Long content, child top at `scrollTop: 0` | Short content, child top |
|---|---|---|
| xl (`:651`) | **−132 → +16** (reachable) | 160 → **160** (unchanged) |
| tablet (`:675`) | **−132 → +16** | 160 → **160** (unchanged) |
| mobile (`:710`) | **−164 → +16** | 128 → **128** (unchanged) |

`+16` is the wrapper's `p-4` padding — i.e. fully visible. With short content the
computed `margin-top` resolves to 144px/112px (the auto margin absorbing free space) and
the rendered position is **identical to before**, so the centred look is preserved.
Content at exactly container height produced no jump and no second scrollbar, and
scroll-to-bottom still lands at `distanceFromBottom: 0` in every case — so neither
`4b06c12` (follow-up scroll) nor `5627a08` (timeline scroll) regresses, and
`findScrollableAncestor` still resolves to the same wrapper.

> One Tailwind trap worth recording: `.my-auto` did not exist in the generated CSS
> before this change, because JIT only emits classes it finds in source. An early
> measurement using `my-auto` in an injected DOM probe was silently inert and produced a
> false "short content regresses to top-aligned" reading. Verify a utility is actually
> emitted before trusting a probe that uses it.

This applies to every state that shares those wrappers — the loading panel, the
"no forecasts yet" / "select a forecast" empty states, and everything
`renderStatusPanel()` returns into `:520`, which includes the **clarification
candidate picker** and the **failed/retry** panel. The clarification picker with several
candidates is the other case that could exceed the viewport, and it is fixed by the same
change. The result view (`<Dashboard>`) does not use these wrappers and is unaffected.

---

## §6 — Styling

- **Tailwind CSS 3.4** utilities only. No CSS modules, no styled-components, no
  runtime CSS-in-JS.
- **shadcn-style primitives** in `components/ui/`: `card`, `button`, `badge`, `input`,
  plus the project-specific `StateMessage` and `ConfirmDialog`. `class-variance-authority`
  + `clsx` + `tailwind-merge` back the variant API.
- **Custom palettes** `anizai-teal`, `anizai-blue`, `anizai-purple`
  (`client/tailwind.config.js:9-33`).
- **Icons** `lucide-react`.
- **Charts** `recharts` 2.15 — `MarketComparison` (bar), `SentimentAnalysis` (area).
- **No typography plugin** — markdown styling is hand-mapped, see §3.2.
- **Page shells** `components/site/PageShell` and `AuthShell` wrap public and auth
  screens respectively.

### §6.1 Responsive layout — measured values

Dashboard grid and drawer widths, read from `pages/DashboardPage.tsx`:

| Breakpoint | Layout | Class |
|---|---|---|
| `< lg` (mobile) | Single column; fixed top bar; both panels are slide-over drawers | `lg:hidden` (`:665`) |
| `lg` → `xl` (tablet) | Two columns: sidebar + main; chat is a right slide-over | `lg:grid-cols-[264px_minmax(0,1fr)]` (`:617`) |
| `xl` (desktop) | Three columns | `xl:grid-cols-[252px_minmax(0,1fr)_304px]` (`:594`) |
| `2xl` (wide) | Three columns, wider rails | `2xl:grid-cols-[272px_minmax(0,1fr)_340px]` (`:594`) |

Drawer widths are **viewport-clamped**, not fixed:

- Sidebar drawer — `w-[min(20rem,calc(100vw-1rem))]` (`:675`)
- Chat drawer — `w-full max-w-[min(24rem,100vw)]` (`:642`, `:689`)

> `docs/ui-map-task-0-1.md §6` records fixed `w-80` / `w-96` drawers and flags "fixed
> mobile chat width may exceed viewport" as a layout risk, and gives the desktop grid
> as `[280px_minmax(0,1fr)_360px]`. All four values are stale — the drawers are
> `min()`-clamped and the grid is narrower. The overflow risk it describes is resolved.

### §6.2 Browser verification — 2026-07-18

Run against the Vite dev server, signed in, with a fully-populated `done` session
selected (`seed-recession-2026` — 10 evidence items, sentiment series, all four cards).

**Method.** Two measurements per surface: (1) `document.documentElement.scrollWidth`
vs `clientWidth` — the page-level overflow signal; (2) a per-element sweep for boxes
whose right edge exceeds the viewport, each classified by whether an ancestor clips it
(`overflow-x: auto|scroll|hidden`) and excluding elements parked off-canvas by a
drawer transform. An escape that no ancestor clips is a real break; a clipped one is a
scroll region.

**Public pages**

| Surface | 375 | 768 | 1280 |
|---|---|---|---|
| Landing | ✅ | ✅ | ✅ |
| Features | ✅ | — | — |
| Signup | ✅ | — | — |

**Dashboard**

| Surface | Viewport | Page overflow | Unclipped escapes | Result |
|---|---|---|---|---|
| Full dashboard, `done` session | 1280 | none | 0 | ✅ |
| Full dashboard, `done` session | 768 | none | 0 | ✅ |
| Full dashboard, `done` session | 375 | none | 0 | ✅ |
| Sidebar drawer open | 375 | none | 0 | ✅ measured 320 px wide |
| Chat drawer open | 375 | none | 0 | ✅ |
| Settings modal open | 375 | none | 0 | ✅ measured 359 px at `left: 8` |
| CreateForecastView | 375 | none | 0 | ✅ |
| `queued` session — status panel + agent timeline | 375 | none | 0 | ✅ |

**No console errors at any viewport, on any surface, across the whole session.**

Two clipped escapes were observed and are **not** breaks:

- The evidence filter-tab strip at 768 (42 px past the edge, parent `overflow-x: auto`).
- The public header nav at 375 (parent `overflow-x-auto no-scrollbar`). The
  `no-scrollbar` class makes the scroll affordance invisible — cosmetic, not tracked.

**Measured values confirm the §6.1 source reading.** The sidebar drawer resolves to
`min(20rem, 100vw − 1rem)` = **320 px** inside a 375 px viewport, and the settings
modal to **359 px** with 8 px margins. Both fit. The overflow risk
`ui-map-task-0-1.md §6` flagged for fixed `w-80`/`w-96` drawers does not occur.

> The settings modal also **does** stack on mobile — its section nav renders as a
> horizontally scrollable tab strip, not a fixed two-column sidebar. `../archive/ui-map-task-0-1.md`
> records "Fixed two-column modal body; no mobile-specific stacking." Superseded.

> **What this pass did not cover:** `SubscriptionSettings` payment/cancel sub-flows,
> the trending panel at tablet width, and the `awaiting_clarification` candidate
> picker — none had reachable state during the run. Their responsive behavior remains
> source-verified only. (The trending panel itself was replaced on 2026-07-28; the
> new-forecast screen that superseded it was browser-checked at 375 and desktop.)

---

## §7 — Known Constraints

| Constraint | Detail |
|---|---|
| No router | Navigation is a `useState` union (`App.tsx:52`). No deep links, no browser history, no shareable forecast URL. Structural — not fixable without adopting a router. |
| `App.tsx` is a four-role god-component | 962 lines covering routing, fetching, mapping, and listener lifecycle. Tracked KG-C-3. |
| `DashboardPage.tsx` is a large shell | 744 lines, 14 `useState` hooks, layout + status panels + modal state in one component. |
| Sidebar omits confidence | `PredictionSession` carries no confidence field and the sidebar renders probability only (`Sidebar.tsx:143`), although `Session.latestConfidence` is available on the wire type. A probability shown without its confidence is exactly the misread the verdict logic exists to prevent (§3.2 rule 1). |
| `mapSessionStatus` is dead code | `App.tsx:74` defines it; `App.tsx:89` immediately does `void mapSessionStatus;`. The `stable`/`volatile` collapse it implements is retired — both `Prediction.status` and `PredictionSession.status` carry the full `SessionStatus` union. |
| Reasoning trace is not retained | Rule A drops `AgentEventsTimeline` the moment status becomes `done`, so the completed forecast has no record of how it was produced even though the events persist in Firestore. Deliberate per Sprint 25 — noted because it is a product decision, not an oversight. |
| Market / sentiment cards are permanently empty | No live data upstream; both render `StateMessage` fallbacks. Tracked KG-C-6. Expected shapes: `../backend-specs/market-sentiment-spec.md`. |
| Landing-page audit not re-verified | `../archive/audits/landing-audit.md` (dated 2026-05-20) inventories landing copy and layout. Component names still match, but **its copy-level claims were not re-checked** during this rewrite. |
| Shared UI primitives are widely bypassed | `components/ui/` provides `Button`, `Card`, `Badge`, and `Input`, but many surfaces build the same affordances by hand with local Tailwind classes instead — raw `<button>` elements throughout the modals, settings, subscription/payment, landing and icon-button surfaces; card-like containers assembled manually in settings, plan cards, chat bubbles and evidence rows; and status/plan/confidence/source badges written as local `<span>`s despite `badge.tsx` existing. There is also no single radius or spacing scale: `Card` uses `rounded-lg`, settings surfaces `rounded-xl`, the modal `rounded-2xl`, and badges range across `rounded` / `rounded-md` / `rounded-lg` / `rounded-full`. The risk is not any individual style but the **number of one-off variants** — a change to a primitive does not propagate to the surfaces that reimplemented it. Carried forward from the Task-0.2 consistency audit; the design-system observations were re-read against current source, but a component-by-component re-audit was **not** performed. |
| Three layout trees mount simultaneously | `DashboardPage` renders the wide-desktop grid, the tablet grid, and the mobile stack as three sibling subtrees, hidden from each other by `hidden`/`lg:hidden`/`xl:hidden`. All three are in the DOM at once — confirmed at runtime (every card heading appears three times) and corroborated by `sprint-24-25-frontend-tasks.md`, which describes passing props to "all three `ChatPanel` instances". Correct, and it keeps each layout independently readable, but it triples the mounted component count and means any per-instance state or effect runs three times. Known and intentional; recorded so it is not rediscovered as a bug. |
| Responsive: three sub-surfaces still source-verified only | The 2026-07-18 browser pass (§6.2) cleared every major dashboard surface at 375/768/1280 with zero unclipped overflow and zero console errors. Not exercised: `SubscriptionSettings` payment/cancel sub-flows, the trending panel at tablet width, and the `awaiting_clarification` candidate picker — no reachable state during the run. (That trending panel was deleted on 2026-07-28; its replacement, the new-forecast screen, was browser-checked at 375 and desktop.) |
