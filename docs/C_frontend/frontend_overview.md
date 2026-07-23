# frontend_overview.md
> Domain: C — Frontend / BFF
> Type: Overview
> Last updated: 2026-07-20
> TL;DR: The macro view of the Anizai client and BFF — what the React SPA renders, what the Express server mediates, and the two independent read paths (REST and direct Firestore) that feed the dashboard. Open this first to orient before the detailed spec files.

## Navigation
- §1 — Purpose & Scope — what this domain is and what it is explicitly not
- §2 — Architecture — the two read paths, request lifecycle, where each layer stops
- §3 — Components — every moving part, its role, its module, and status
- §4 — Status — pointer to the live status in `frontend_sprints.md`
- §5 — Navigation Map — where to jump for contracts, API surface, UI, gaps, and closed work

---

## §1 — Purpose & Scope

Domain C is the **user-facing half** of the Anizai forecasting platform: a React SPA
(`client/`) and an Express BFF (`server/`). It collects a forecast question, enqueues
it for the agent, and renders the structured forecast that comes back.

**In scope:** the SPA (screens, dashboard cards, view-model mapping), the BFF (auth,
session CRUD, idempotency, usage charging, Firestore reads), the wire contracts between
them, and the client's direct Firestore listener path.

**Not in scope (other domains):** the Medallion data pipeline that fills the vaults
(Domain A, `data-pipeline/docs/A_pipeline/`) and the LangGraph Agentic Hub that
produces forecasts (Domain B, `data-pipeline/agent/`).

The single most important boundary: **the BFF is a mediator, not a brain.** It does
not call OpenAI, does not query pgvector, and does not generate forecasts. It writes a
queue document and later reads the agent's answer back out of Firestore. Confirmed by
absence — `server/src/` contains no OpenAI client, no Postgres driver, and no LangGraph
import; its only external dependency of substance is `firebase-admin`
(`server/package.json`).

Both packages typecheck clean as of this doc's date (`tsc --noEmit` on `server/`,
`tsc -b` on `client/`).

---

## §2 — Architecture

There are **two independent read paths** into the dashboard. Most stale documentation in
this repo describes only the first and asserts the second does not exist. Both are live.

```
                          ┌──────────────────────────────────────────────┐
  PATH 1 — REST (pull)    │  PATH 2 — Firestore listeners (push)         │
  ────────────────────    │  ──────────────────────────────────          │
                          │                                              │
  client/src/lib/api.ts   │  client/src/services/session.service.ts      │
    fetch, Bearer token   │    subscribeToSession        (session doc)   │
    base = /api           │    subscribeToSessionMessages(messages/*)    │
         │                │    subscribeToAgentEvents    (agentEvents/*) │
         ▼                │         │                                    │
  Vite dev proxy          │         │  firebase/firestore Web SDK        │
    /api → :3000, rewrite │         │  gated by firestore.rules          │
         │                │         │                                    │
         ▼                │         │                                    │
  Express BFF (server/)   │         │                                    │
    requestId → pino      │         │                                    │
    → json → CORS         │         │                                    │
    → authMiddleware      │         │                                    │
    → routes → services   │         │                                    │
    → repositories        │         │                                    │
         │                │         │                                    │
         ▼                ▼         ▼                                    │
  ┌──────────────────────────────────────────────────────────────────────┘
  │                        FIRESTORE  (project anizai-ai)
  │   sessions/{id}                        ← BFF writes, client reads live
  │     messages/{id}                      ← BFF + agent write, client reads live
  │     evidence/{id}                      ← agent writes
  │     predictionSeries/{id}              ← agent writes (empty today, §3)
  │     agentEvents/{id}                   ← agent writes, client reads live
  │   sessionResults/{id}                  ← agent writes
  │   forecastQueries/{id}                 ← BFF writes, agent claims  (server-only)
  │   users/{uid}  trendingForecasts/{id}*  canonicalForecasts/{key}
  └──────────────────────────────────────────────────────────────────────
                                  ▲
                                  │  claims queue docs, writes results
                        Agentic Hub worker (Domain B)
```

> \* `trendingForecasts` is **orphaned as of 2026-07-20.** It was only ever read by the
> BFF's fallback path, which KG-C-11 deleted; `/trending` now serves the live Polymarket
> fetch or fails. The collection name, the `scripts/seed.ts` writer and the
> `firestore.rules` read block all still exist and are harmless, but nothing reads the
> data. Left in place deliberately — removing the rule needs a Firestore deploy.

**Forecast request lifecycle.** `POST /sessions` validates the body (zod, requires a
UUID `idempotencyKey`), short-circuits on a recent duplicate key, charges monthly usage,
then writes two documents in one batch: `sessions/{id}` with `status: 'queued'` and
`forecastQueries/{id}` with `status: 'pending'` (`server/src/repositories/session.repository.ts`,
`createSession`). The agent claims the queue doc, runs its graph, writes
`sessionResults/{id}` and the `evidence` subcollection, and flips the session status.
The client sees the flip through `subscribeToSession` (Path 2) and refetches the full
detail through `GET /sessions/:id` (Path 1).

> **Two status vocabularies, one lifecycle.** `sessions/{id}.status` is `'queued'`;
> the paired `forecastQueries/{id}.status` is `'pending'`. They are different fields on
> different documents and are not interchangeable. Field-by-field detail in
> `frontend_contracts.md §2`.

**Why two paths.** REST returns the whole aggregate (session + messages + evidence +
result + series) in one round trip, but only when asked. The listeners deliver
low-latency updates for the three surfaces that change *during* a run — status,
messages, and agent events — without polling. There is no streaming HTTP anywhere in
this domain.

> **`agentEvents` is wired but dark in production.** The client read/render path is
> complete and verified in this repo (listener, Rule B run scoping, timeline component).
> Events do not appear in production because the **deployed agent image predates the
> feature** — cloud is running a Sprint ~21-era build (`AGENT_VERSION`
> `0.4.0-sprint21-*`), and everything from Sprint 22 onward, including the entire
> `agentEvents` emission mechanism, exists in Domain-B source but is not in that image.
> It waits on a cumulative agent-image rebuild, which is its own track rather than any
> single sprint. Rule B additionally needs the hub to write `currentRunId` on the
> session doc — ratified in the Domain-B Sprint 25 plan, not yet built. **Relayed from
> the Domain-B owner; not verifiable from this repo.** Tracked KG-C-7.

---

## §3 — Components

### §3.1 Server (`server/`)

Express 4.21, TypeScript 5.7, firebase-admin 13.6, zod 3.24, pino 9.6, Vitest 3.0.
Node >= 20, ESM (`"type": "module"`, `.js` import specifiers throughout).

| Component | Role | File / Module | Status |
|---|---|---|---|
| Entry point | `listen()`, SIGTERM/SIGINT graceful shutdown, 10s force-exit | `src/index.ts` | Active |
| App factory | Middleware chain + route registration | `src/server.ts` | Active |
| Env config | zod-validated env; exits on invalid | `src/config/env.ts` | Active |
| Request ID | Correlation id, registered first | `src/middleware/requestId.ts` | Active |
| Auth | Firebase ID token verify → `req.user` | `src/middleware/auth.ts` | Active |
| Error handling | `AppError`, structured error envelope, 404 handler | `src/middleware/error.ts` | Active |
| Firebase admin | Admin SDK init (ADC) | `src/lib/firebase.ts` | Active |
| Logger | pino instance | `src/lib/logger.ts` | Active |
| Root / health routes | `GET /`, `GET /health` — public | `src/routes/root.ts`, `src/routes/health.ts` | Active |
| Trending routes | `GET /trending` — public | `src/routes/trending.ts` | Active |
| Me routes | `GET /me`, `PATCH /me/plan` — authed | `src/routes/me.ts` | Active |
| Session routes | 7 authed endpoints incl. `/clarify`, `/retry` | `src/routes/sessions.ts` | Active |
| Demo routes | 3 unauthed fixtures | `src/routes/demo.ts` | Dev-only, double-gated (KG-C-4) |
| Sessions service | Ownership checks, lifecycle rules, retry orchestration | `src/services/sessions.service.ts` | Active |
| Users service | Profile, plan, usage increment | `src/services/users.service.ts` | Active |
| Trending service | Trending list | `src/services/trending.service.ts` | Active |
| Firebase service | `collectionRef`, `batch`, `now`, collection names | `src/services/firebase.service.ts` | Active |
| Session repository | All session Firestore I/O; the highest-risk file | `src/repositories/session.repository.ts` | Active |
| User repository | Usage counting; raises `PLAN_LIMIT_EXCEEDED` | `src/repositories/user.repository.ts` | Active |
| Trending repository | Live Polymarket Gamma `/events` fetch + 5-min in-memory cache; event mapping, noise filtering, topic filtering | `src/repositories/trending.repository.ts` | Active — as of 2026-07-20: Firestore fallback removed (KG-C-11), ranks by `volume24hr` (KG-C-12), queries `/events` and returns event cards (KG-C-13), constrained to the pipeline's 13 forecastable topic domains (KG-C-15). Holds a **mirror** of those domain names — canonical source is `data-pipeline/processing/keyword_sniper.py` |
| API types | `ApiSuccessResponse`, `ApiErrorResponse`, `AuthUser` | `src/types/api.ts` | Active |
| Firestore rules | Client read/write gating; server-only collections | `firebase/firestore.rules` | Active |
| Tests | 3 suites (health, repository, service) | `tests/*.test.ts` | Active |

### §3.2 Client (`client/`)

React 19, TypeScript 5.6, Vite 6, Tailwind 3.4, Firebase Web SDK 10, recharts 2.15,
react-markdown 9, lucide-react, Vitest 2.1.

| Component | Role | File / Module | Status |
|---|---|---|---|
| Root component | Pseudo-router (`AppState` union), data fetching, all view-model mapping, listener wiring | `src/App.tsx` | Active (962 lines — KG-C-3) |
| API client | `fetch` wrapper, Bearer headers, `{ data }` unwrap, `ApiError`/`ApiAuthError` | `src/lib/api.ts` | Active |
| Firebase client | App/auth/firestore init from `VITE_FIREBASE_*` | `src/lib/firebase.ts` | Active |
| Agent-event selection | `selectCurrentRunEvents` — Rule B run scoping | `src/lib/agentEvents.ts` | Active (unit-tested) |
| Session service | REST wrappers **and** the 3 Firestore listeners; wire types | `src/services/session.service.ts` | Active |
| Auth service | Google/email sign-in, `subscribeToAuthState` | `src/services/auth.service.ts` | Active |
| User service | `/me`, `/me/plan` | `src/services/user.service.ts` | Active |
| Trending service | `/trending` + the `TrendingForecast` type mirror | `src/services/trending.service.ts` | Active — demo fallback removed 2026-07-20 (KG-C-5); logs and returns `[]` on failure. Type mirrors the BFF declaration, unenforced (KG-C-1) |
| UI types | `Prediction`, `TimelineEvent`, `AgentEvent`, `KeyFactor`, … | `src/types/index.ts` | Active |
| Dashboard shell | Panel orchestration, drawers, modals, create/select/chat | `src/pages/DashboardPage.tsx` | Active (744 lines) |
| Forecast view | Card container; factor→evidence highlight coordination | `src/components/Dashboard.tsx` | Active |
| Prediction overview | 6 components + 2 tested lib modules | `src/components/cards/predictionOverview/` | Active |
| Market comparison | recharts bar; tier-aware empty state | `src/components/cards/MarketComparison.tsx` | Active — no live data (KG-C-6) |
| Sentiment analysis | recharts area | `src/components/cards/SentimentAnalysis.tsx` | Active — no live data (KG-C-6) |
| Evidence timeline | Evidence feed, filters, key evidence, highlight target | `src/components/cards/EvidenceTimeline.tsx` | Active |
| Agent events timeline | Live reasoning trace, run-scoped | `src/components/cards/AgentEventsTimeline.tsx` | Active — blocked on deployed agent image (KG-C-7) |
| Sidebar / chat | Session list; follow-up conversation | `src/components/Sidebar.tsx`, `src/components/ChatPanel.tsx` | Active |
| Landing sections | 6 sections | `src/components/landing/` | Active |
| Page shells | `PageShell`, `AuthShell` | `src/components/site/` | Active |
| UI primitives | shadcn-style `card`, `button`, `badge`, `input` + `StateMessage`, `ConfirmDialog` | `src/components/ui/` | Active |
| Settings | Modal + 6 sections | `src/components/SettingsModal.tsx`, `src/components/settings/` | Active |
| ~~Mock data~~ | ~~Demo fixtures~~ | ~~`src/data/mockData.ts`~~ | **Deleted 2026-07-20** (KG-C-5). `src/data/` no longer exists |
| ~~Trending forecasts card~~ | ~~Trending list w/ inline fixtures~~ | ~~`src/components/TrendingForecasts.tsx`~~ | **Deleted 2026-07-20.** Unimported and never rendered; held four hardcoded forecasts inline, so it survived the `mockData.ts` sweep. The client now ships no fixtures at all |

---

## §4 — Status

Full slice/sprint status, open work, deferred items, and the Domain C Known Gaps
(`KG-C-*`) → `frontend_sprints.md`. Closed-slice detail → `frontend_archive.md`.

The `KG-C-*` identifiers referenced in §3 are defined in `frontend_sprints.md §4`.

---

## §5 — Navigation Map

Use this to jump straight to the right file/section without reading whole files.

- **Wire shapes — what a field is called and what it holds** →
  `frontend_contracts.md`. Session lifecycle §2; REST types §3; Firestore direct-read
  types (incl. `currentRunId` / `runId`) §4; the view-model mapping layer §5.
- **HTTP surface — routes, auth, envelopes, error codes, env** → `frontend_api.md`.
- **Screens and components — what renders where, and which UI states exist** →
  `frontend_ui.md`.
- **Known Gaps (KG-C-*)** → `frontend_sprints.md §4`.
- **Closed work** → `frontend_archive.md` (slices 0.x–13, Sprints 24–25, and the
  process/safety-review artifacts).
- **Cross-domain contracts:**
  - What the agent is expected to send for the Market and Sentiment cards →
    `../backend-specs/market-sentiment-spec.md`.
  - The as-built Sprint 24+25 record, incl. pipeline-side grounding →
    `sprint-24-25-frontend-tasks.md`.
  - What the pipeline produces upstream → `../../data-pipeline/docs/A_pipeline/pipeline_overview.md`.
