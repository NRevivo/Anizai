# Project File Map - Task 1.1

<!-- archive-banner -->
> ⚠️ **SUPERSEDED — contains inaccuracies.** Historical record only; do not
> cite as current. Corrected content: [`frontend_overview.md`](../C_frontend/frontend_overview.md) §2–§3 (and `frontend_api.md` §3).
> Why this doc is wrong: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §2.

## 1. Summary

The current Anizai app uses a React frontend with a thin `fetch`-based API client and Express backend routes. Session and result contracts are defined separately on the frontend and backend rather than in a shared package. Frontend forecast, result, and chat flows are orchestrated mainly from `client/src/App.tsx`, while backend session reads and writes are centralized in `server/src/repositories/session.repository.ts` and exposed through `server/src/routes/sessions.ts`.

The new main merge introduced backend session lifecycle statuses (`queued`, `claimed`, `running`, `done`, `failed`, `awaiting_clarification`), plus `errorMessage`, `clarificationCandidates`, and `forecastQueries` writes during session creation. Those contracts are now present in both the backend session service types and the frontend session service types.

There are no Firestore listeners implemented in the frontend or backend code checked here. The only listener mention is in Firestore rules comments describing an external agentic hub watching `forecastQueries`, which is not implemented inside this repo.

## 2. Concern Map

| Concern | File path | What it does | Notes |
| --- | --- | --- | --- |
| App-level forecast/session orchestration | `client/src/App.tsx` | Loads sessions and trending items, selects active session, creates sessions, sends follow-up messages, deletes sessions, and maps backend session/result data into UI view models | Main frontend data-flow entry point |
| Dashboard shell and split views | `client/src/pages/DashboardPage.tsx` | Switches between create-forecast view and dashboard view; wires sidebar, dashboard, chat panel, settings modal, and mobile drawers | Important for session selection and responsive layout |
| Frontend API client | `client/src/lib/api.ts` | Builds auth headers, calls backend endpoints, parses `{ data }` responses, throws typed API/auth errors | Used by frontend services |
| Frontend session types and API wrappers | `client/src/services/session.service.ts` | Defines `SessionStatus`, `SessionListItem`, `SessionResult`, `SessionDetail`, and wraps `/sessions` and `/sessions/:id/messages` endpoints with demo fallbacks | Frontend copy of session/result contract |
| Frontend user API wrapper | `client/src/services/user.service.ts` | Fetches `/me`, patches `/me/plan`, and supplies demo user fallback | Related to dashboard bootstrapping |
| Frontend trending API wrapper | `client/src/services/trending.service.ts` | Fetches `/trending` and supplies demo fallback | Used when entering dashboard |
| Vite frontend dev config | `client/vite.config.ts` | Proxies `/api` to `http://localhost:3000` in dev | Current frontend services call raw routes like `/sessions`, not `/api/sessions` |
| Backend route registration | `server/src/server.ts` | Registers public, demo, and protected routes on the Express app | Demo routes are only enabled in dev |
| Session API routes | `server/src/routes/sessions.ts` | Exposes `GET /sessions`, `GET /sessions/:id`, `POST /sessions`, `POST /sessions/:id/messages`, `DELETE /sessions/:id` | Main backend surface for forecast and chat flow |
| Demo routes | `server/src/routes/demo.ts` | Exposes unauthenticated demo session and demo user endpoints in dev | Marked "REMOVE IN PRODUCTION" |
| Backend API response types | `server/src/types/api.ts` | Defines `ApiSuccessResponse`, `ApiErrorResponse`, `HealthResponse`, and `AuthUser` | Shared only within the server code |
| Backend session/domain types | `server/src/services/sessions.service.ts` | Defines backend `Session`, `SessionResult`, `SessionDetail`, `ClarificationCandidate`, and create-message/session inputs | Backend copy of session/result contract |
| Backend session Firestore repository | `server/src/repositories/session.repository.ts` | Reads/writes sessions, results, messages, evidence, prediction series, sentiment series, and deletes related documents | Most important Firestore file for next tasks |
| Backend Firestore helpers | `server/src/services/firebase.service.ts` | Exposes `collectionRef`, `batch`, `now`, `toISOString`, and collection names including `forecastQueries` | Used by repositories |
| Firestore rules | `server/firebase/firestore.rules` | Defines client read/write permissions for users, sessions, subcollections, sessionResults, trending, and `forecastQueries` | `forecastQueries` is server-only |
| Seed/demo data script | `server/scripts/seed.ts` | Seeds demo user, sessions, messages, prediction series, evidence, sentiment series, results, and trending docs into Firestore | Useful for understanding expected data shapes |
| Session result dev script | `server/scripts/test-session-result.ts` | Writes a full `sessionResults` document and verifies retrieval through the service layer | Covers newer result fields |
| Session repository test fixture | `server/tests/sessions.repository.test.ts` | Tests that `createSession` writes a `queued` session and a `forecastQueries` document with the expected shape | Most direct test for the new main merge behavior |

## 3. Status/Data Contract Locations

- Frontend session status contract:
  - `client/src/services/session.service.ts`
  - `SessionStatus = 'queued' | 'claimed' | 'running' | 'done' | 'failed' | 'awaiting_clarification'`
- Backend session status contract:
  - `server/src/services/sessions.service.ts`
  - `Session.status` uses the same union
- Frontend UI status mapping:
  - `client/src/App.tsx`
  - `mapSessionStatus()` collapses backend statuses into UI buckets `stable` and `volatile`
- Legacy UI-only presentation types:
  - `client/src/types/index.ts`
  - `Prediction.status` and `PredictionSession.status` are still `stable | volatile`
- Session result types:
  - Frontend: `client/src/services/session.service.ts`
  - Backend: `server/src/services/sessions.service.ts`
- New main merge fields:
  - `errorMessage`: frontend and backend session service files
  - `clarificationCandidates`: frontend and backend session service files
  - `forecastQueries` write shape: `server/src/repositories/session.repository.ts`

Notes:

- There is no shared cross-package types directory in the repo for session/result contracts.
- The same domain types are duplicated between frontend and backend service files.

## 4. API Route Locations

- App bootstrap and route mounting:
  - `server/src/server.ts`
- Session routes:
  - `server/src/routes/sessions.ts`
  - `GET /sessions`
  - `GET /sessions/:id`
  - `POST /sessions`
  - `POST /sessions/:id/messages`
  - `DELETE /sessions/:id`
- Demo routes:
  - `server/src/routes/demo.ts`
  - `GET /demo/sessions`
  - `GET /demo/sessions/:id`
  - `GET /demo/user`
- Other relevant supporting routes:
  - `server/src/routes/me.ts`
  - `server/src/routes/trending.ts`
  - `server/src/routes/health.ts`
  - `server/src/routes/root.ts`

## 5. Frontend Data Flow Locations

- App orchestration:
  - `client/src/App.tsx`
- Forecast creation flow:
  - `client/src/App.tsx`
    - `handleCreateSession()`
  - `client/src/pages/DashboardPage.tsx`
    - `handleSubmitForecast()`
  - `client/src/services/session.service.ts`
    - `createSession()`
- Session selection and result loading:
  - `client/src/App.tsx`
    - `loadSession()`
    - `handleSessionSelect()`
    - `toPrediction()`
    - `toSentimentPoints()`
    - `toTimelineEvents()`
    - `toChatMessages()`
- Chat/message flow:
  - `client/src/App.tsx`
    - `handleSendMessage()`
  - `client/src/services/session.service.ts`
    - `addSessionMessage()`
- Session list loading:
  - `client/src/App.tsx`
    - `enterDemoDashboard()`
  - `client/src/services/session.service.ts`
    - `fetchSessions()`
- Demo/data fallback behavior:
  - `client/src/services/session.service.ts`
  - `client/src/services/user.service.ts`
  - `client/src/services/trending.service.ts`

## 6. Backend Data Flow Locations

- Session route handlers:
  - `server/src/routes/sessions.ts`
- Session business/service layer:
  - `server/src/services/sessions.service.ts`
  - `listSessions()`
  - `getSession()`
  - `getSessionResult()`
  - `getSessionDetail()`
  - `createSession()`
  - `addMessage()`
  - `deleteSession()`
- Firestore read/write layer:
  - `server/src/repositories/session.repository.ts`
  - Firestore reads:
    - `listSessions()`
    - `getSession()`
    - `getSessionResult()`
    - `getMessages()`
    - `getPredictionSeries()`
    - `getEvidence()`
    - `getSentimentTimeSeries()`
  - Firestore writes:
    - `createSession()`
    - `addMessage()`
    - `deleteSession()`
- New main merge `forecastQueries` write:
  - `server/src/repositories/session.repository.ts`
  - `createSession()` writes:
    - a session document with `status: 'queued'`
    - a `forecastQueries/{sessionId}` document with pending/claim fields
- Firestore permissions:
  - `server/firebase/firestore.rules`

## 7. Risky or Important Files for Next Tasks

- `server/src/repositories/session.repository.ts`
  - Highest-risk backend file for session lifecycle, Firestore writes, and forecast query ownership behavior
- `server/src/services/sessions.service.ts`
  - Source of backend session/result contract assumptions
- `client/src/services/session.service.ts`
  - Source of frontend session/result contract assumptions and demo fallback shapes
- `client/src/App.tsx`
  - Main frontend adapter between backend statuses/contracts and UI view models
- `client/src/pages/DashboardPage.tsx`
  - Main frontend interaction shell for create/select/chat flows
- `server/firebase/firestore.rules`
  - Important if future tasks touch client/server access patterns
- `server/tests/sessions.repository.test.ts`
  - Best existing test reference for `forecastQueries` and queued-session behavior
- `server/scripts/seed.ts`
  - Useful for expected Firestore document examples, but may lag behind newer required fields if not kept in sync

Additional note:

- No in-repo Firestore listener implementation was found in `client/src`, `server/src`, `server/scripts`, or `server/tests`.
- The only listener-related reference is the `forecastQueries` rules comment describing an external hub that watches pending query docs outside this repo.
