# frontend_api.md
> Domain: C — Frontend / BFF
> Type: Spec
> Last updated: 2026-07-18
> TL;DR: The Express BFF's HTTP surface — every route with its auth, validation and status codes, the middleware chain, the ownership model, the error-code register, and the env/proxy configuration. Open this when adding a route, changing validation, or debugging a request that never reaches a handler.

## Navigation
- §1 — Overview — what the BFF does and where it stops
- §2 — Response Envelope — the `{ data }` / `{ error }` contract
- §3 — Route Matrix — all 14 routes, auth, validation, status codes
- §4 — Middleware Chain — registration order and why it matters
- §5 — Auth & Ownership — token verification and the ownership check
- §6 — Error Codes — the register, incl. `PLAN_LIMIT_EXCEEDED`
- §7 — Configuration — env vars, the `/api` prefix, CORS, local dev
- §8 — Known Constraints

---

## §1 — Overview

The BFF is an Express 4.21 / TypeScript 5.7 service on Node ≥ 20, ESM throughout
(`"type": "module"`, `.js` import specifiers). It mediates between the SPA and
Firestore: it authenticates, validates, enforces ownership and plan limits, and reads
or writes documents. It generates nothing.

Layering is strict and one-directional:

```
routes/       zod validation, HTTP status, envelope construction
   ↓
services/     ownership checks, lifecycle rules, cross-entity orchestration
   ↓
repositories/ all Firestore I/O — the only layer that touches the database
```

No route imports a repository directly except `routes/demo.ts` (§3.4). Wire types are
specified in `frontend_contracts.md`; this document covers only the transport.

`createApp()` is an `async` factory (`server/src/server.ts`) because the demo router is
conditionally `await import()`-ed. `src/index.ts` calls it, listens on `env.PORT`, and
installs SIGTERM/SIGINT handlers that close the server and force-exit after 10s.

---

## §2 — Response Envelope

Every response is wrapped. Defined in `server/src/types/api.ts`.

```ts
// success
{ data: T,
  meta?: { requestId?: string, timestamp?: string } }

// error
{ error: { message: string, code?: string, details?: unknown },
  meta?: { requestId?: string, timestamp?: string } }
```

- Route handlers construct `{ data }` and generally omit `meta`.
- The error middleware and the 404 handler **always** populate `meta` with
  `requestId` and an ISO `timestamp` (`server/src/middleware/error.ts`).
- The client unwraps `.data` and **discards `meta` entirely**
  (`client/src/lib/api.ts`). The correlation id is therefore only reachable through
  the `x-request-id` response header, not the parsed body.

> In production, a 500 replaces `error.message` with the literal
> `'Internal server error'`. Any other status code passes the real message through
> (`error.ts`, `isProd && statusCode === 500`).

---

## §3 — Route Matrix

14 routes across 6 routers. `Auth` = requires the `authMiddleware` Bearer check.

### §3.1 Public

| Method | Path | Auth | Validation | Success | Handler |
|---|---|---|---|---|---|
| GET | `/` | — | — | 200 `{ ok: true, name: 'anizai-server' }` | `routes/root.ts` |
| GET | `/health` | — | — | 200 `{ ok: true, timestamp }` | `routes/health.ts` |
| GET | `/trending` | — | `?limit` parsed, clamped | 200 `TrendingForecast[]` | `routes/trending.ts` |
| GET | `/trending/:id/markets` | — | — | 200 `TrendingMarket[]`, 404 `NOT_FOUND` | `routes/trending.ts` |

`/trending` parses `limit` with `Number.parseInt`; non-finite or non-positive falls back
to **20**, and any value is clamped to a maximum of **100**
(`Math.min(requested, 100)`).

> **`limit` is an upper bound, not a promise.** `/trending` is a live passthrough of
> Polymarket's `/events`, filtered twice before it returns — excluded categories
> (sport, entertainment, weather) and then a required match against one of the
> pipeline's 13 forecastable topic domains (KG-C-15). Roughly half the upstream page is
> dropped. The repository over-fetches 4× to compensate, but Polymarket's own ceiling
> is 100 events, so a large `limit` can legitimately return fewer rows. Returning fewer
> on-topic events is deliberate — the filter is never relaxed to fill the list, and an
> empty result is returned as `[]` with a `warn` rather than backfilled. Shape:
> `frontend_contracts.md §3.9`.

> **`markets` is not on this payload for multi-outcome events (2026-07-27).** Only
> binary events carry their single market inline; every other field is fetched from
> `GET /trending/:id/markets` when the user opens a picker. Shipping all of them kept
> `?limit=12` at 59.9 KB against 4.5 KB, on an endpoint the landing page calls
> unauthenticated on every visit. **An empty `markets` here means "not loaded", not
> "none exist"** — see `frontend_contracts.md §3.9`.

`GET /trending/:id/markets` takes the Polymarket **event** id (`TrendingForecast.id`),
not a market or condition id. It reuses the list's 5-minute cache — a click on a
visible card costs no upstream call — and falls back to a single-event Gamma fetch
when the cache cannot answer. Returns 404 when the event does not exist upstream, and
`200 []` when it exists but has nothing selectable. Full semantics:
`frontend_contracts.md §3.9b`.

### §3.2 User

| Method | Path | Auth | Validation | Success | Handler |
|---|---|---|---|---|---|
| GET | `/me` | ✅ | — | 200 `UserPublic` | `routes/me.ts:18` |
| PATCH | `/me/plan` | ✅ | `{ plan: 'free' \| 'premium' }` | 200 `UserPublic` | `routes/me.ts:39` |

`GET /me` is **create-on-first-read**: `getPublicProfile(uid, email, name)` creates the
user document if absent. When the auth token carries no email, the handler substitutes
`` `${uid}@anonymous.invalid` ``.

`UserPublic` is a projection of the internal `User` — it drops `admin` and `updatedAt`
(`services/users.service.ts`, `toPublicUser`).

> `PATCH /me/plan` builds its 400 response **inline** rather than throwing `AppError`,
> so that one response has no `meta` block (`routes/me.ts:44-51`). Every other
> validation failure in the codebase throws `AppError` and picks up `meta`.

### §3.3 Sessions

All require auth. All enforce ownership before acting (§5).

| Method | Path | Validation | Success | Notes |
|---|---|---|---|---|
| GET | `/sessions` | — | 200 `Session[]` | Default limit 50, `lastActivityAt` desc |
| GET | `/sessions/:id` | — | 200 `SessionDetail` | Aggregate of 5 subcollection reads |
| POST | `/sessions` | `question` 1–1000, `title?` ≤200, `idempotencyKey` UUID | **201** `Session` | Charges usage; writes session + queue doc |
| POST | `/sessions/:id/messages` | `role` enum, `content` 1–50000, `meta?` | **201** `SessionMessage` | Also bumps `lastActivityAt` |
| POST | `/sessions/:id/clarify` | `{ chosenCandidateId: string \| null }` | 200 `Session` | Requires status `awaiting_clarification` |
| POST | `/sessions/:id/retry` | — | **201** `Session` | Requires status `failed` |
| DELETE | `/sessions/:id` | — | 200 `{ id }` | Cascades 5 subcollections + 2 docs |

Validation schemas are zod, declared at the top of `routes/sessions.ts:14-35`. A
`safeParse` failure throws `AppError('Invalid request body', 400, 'VALIDATION_ERROR')`
— **the zod issue detail is discarded**, so the client learns only that the body was
invalid, not which field.

State-machine guards live in the service, not the route:

| Endpoint | Guard | Failure |
|---|---|---|
| `/clarify` | `status === 'awaiting_clarification'` | 409 `INVALID_SESSION_STATUS` |
| `/clarify` | `chosenCandidateId` must match a candidate on the session (or be `null`) | 400 `INVALID_CLARIFICATION_CANDIDATE` |
| `/retry` | `status === 'failed'` | 400 `INVALID_SESSION_STATUS` |
| `/retry` | original `question` non-empty after trim | 400 `MISSING_QUESTION` |

> `/clarify` and `/retry` both return `INVALID_SESSION_STATUS` but with **different
> HTTP codes** — 409 and 400 respectively (`services/sessions.service.ts:309` and
> `:359`). A client branching on `code` alone will treat them identically; one
> branching on status will not.

### §3.4 Demo (development only)

| Method | Path | Handler |
|---|---|---|
| GET | `/demo/sessions` | `routes/demo.ts:14` |
| GET | `/demo/sessions/:id` | `routes/demo.ts:35` |
| GET | `/demo/user` | `routes/demo.ts:81` |

Unauthenticated. All three hard-code `DEMO_USER_ID = 'demo-user-001'` and every handler
carries a `REMOVE IN PRODUCTION` comment. **Double-gated** — mounted only when
`isDev && env.ALLOW_DEMO_ROUTES` (`server.ts:70`), i.e. `NODE_ENV=development` *and*
`ALLOW_DEMO_ROUTES=true`. Mounting logs `'Demo routes enabled (development mode)'`.

These routers bypass the service layer and call repositories directly, so they skip
every ownership check — safe only because the user id is a constant.

> `GET /demo/sessions/:id` returns a **different shape** from `GET /sessions/:id`: it
> omits `sentimentTimeSeries` and caps evidence at 20 instead of 50
> (`routes/demo.ts:52-68`). It is not a drop-in stand-in for the real endpoint.

No frontend service calls `/demo/*`. Tracked KG-C-4.

---

## §4 — Middleware Chain

Registration order in `createApp()` (`server/src/server.ts`) — order is load-bearing:

| # | Middleware | Purpose |
|---|---|---|
| 1 | `requestIdMiddleware` | `crypto.randomUUID()` → `req.requestId` + `x-request-id` response header. **Must be first** so every downstream log and error envelope can reference it. |
| 2 | `app.disable('etag')` | Prevents 304s on polled JSON |
| 3 | `compression()` | gzip/deflate above the default 1 KB threshold. Added 2026-07-27 — responses were previously uncompressed. Must precede the routers to wrap their `res.json`. Measured on `GET /trending?limit=12`: 59.7 KB → 16.8 KB (3.6×). |
| 4 | `pinoHttp({ logger })` | Request logging |
| 5 | `express.json()` / `express.urlencoded()` | Body parsing |
| 6 | CORS (inline) | Allowlist check; short-circuits `OPTIONS` with **204** |
| 7 | Public routers | root, health, trending |
| 8 | Demo router | conditional (§3.4) |
| 9 | Protected routers | me, sessions — each route applies `authMiddleware` itself |
| 10 | `notFoundMiddleware` | 404 envelope |
| 11 | `errorMiddleware` | Terminal error handler |

**Auth is per-route, not chain-level.** Steps 7–9 register routers, but the Bearer check
is attached to individual handlers (`router.get('/sessions', authMiddleware, …)`). A new
route added to `routes/sessions.ts` without `authMiddleware` is public by default.

CORS allowlist is a hardcoded `Set` of `http://localhost:5173` and
`http://127.0.0.1:5173` (`server.ts:18-21`). It echoes the origin and sets
`Vary: Origin` only on a match; a non-matching origin gets the method/header
allowances but no `Access-Control-Allow-Origin`. **No production origin is
configured** — see §8.

---

## §5 — Auth & Ownership

**Token verification** (`middleware/auth.ts`): requires `Authorization: Bearer <token>`,
verifies via `adminAuth.verifyIdToken`, attaches `{ uid, email, name, picture }` to
`req.user`. Any failure — missing header, missing token, invalid or expired token —
becomes 401 `UNAUTHORIZED`. Firebase's own error is logged at `warn` and replaced with
`'Invalid or expired token'`, so the specific reason never reaches the client.

A `requireAuth` helper also exists in the same file but is **not referenced anywhere**
in `src/`.

**Ownership** is enforced in the service layer by a single choke point:

```ts
// services/sessions.service.ts:212
getSession(sessionId, userId) → throws 404 NOT_FOUND unless session.userId === userId
```

Every session operation routes through it: `getSessionDetail`, `addMessage`,
`deleteSession`, `clarifySession`, `retryFailedSession`, and `getSessionResult`.

> **Non-ownership is reported as 404, never 403.** A session belonging to another user
> is indistinguishable from one that does not exist. Deliberate — it prevents
> id-probing from confirming existence.

`getSessionResult` deserves note: `sessionResults/{id}` documents are written by the
agent **without a `userId` field**, so they cannot be authorized directly. Authorization
is delegated to the parent session doc — `getSessionResult` calls `getSession` first,
then reads the result by doc id (`sessions.service.ts:233-238`).

The client mirrors the auth contract in `lib/api.ts`: it pulls a fresh ID token via
`auth.currentUser.getIdToken()` per request, and maps 401/403 to `ApiAuthError` (a
subclass of `ApiError`) so callers can distinguish auth failure from other errors.

---

## §6 — Error Codes

`AppError(message, statusCode, code?, details?)` — `middleware/error.ts`. Codes emitted
by `src/`:

| Code | HTTP | Raised by | `details` |
|---|---|---|---|
| `UNAUTHORIZED` | 401 | `middleware/auth.ts` | — |
| `VALIDATION_ERROR` | 400 | `routes/sessions.ts`, `routes/me.ts` | — (zod issues discarded) |
| `NOT_FOUND` | 404 | `sessions.service.ts`, `user.repository.ts`, `routes/demo.ts` | — |
| `NOT_FOUND` | 404 | `notFoundMiddleware` (unmatched route) | — |
| `INVALID_SESSION_STATUS` | 409 / 400 | `clarifySession` / `retryFailedSession` | — |
| `INVALID_CLARIFICATION_CANDIDATE` | 400 | `clarifySession` | — |
| `MISSING_QUESTION` | 400 | `retryFailedSession` | — |
| `PLAN_LIMIT_EXCEEDED` | **403** | `user.repository.ts:236` | ✅ structured, below |
| `INTERNAL_ERROR` | 500 | `errorMiddleware` fallback for non-`AppError` | — |

### §6.1 `PLAN_LIMIT_EXCEEDED`

The only error carrying structured `details`:

```ts
{ error: {
    message: "You've used your free forecasts this month",
    code: 'PLAN_LIMIT_EXCEEDED',
    details: {
      used:     number,   // forecasts consumed this month
      limit:    3,        // FREE_FORECAST_LIMIT
      planTier: 'free',
      resetAt:  string,   // ISO8601 — 00:00 UTC on the 1st of next month
    } } }
```

Enforcement (`repositories/user.repository.ts`, `incrementUsage`):

- `FREE_FORECAST_LIMIT = 3` (`user.repository.ts:13`).
- Usage month is a **UTC** `YYYY-MM` string (`getCurrentUsageMonth`).
- Reset is **lazy**: if the stored `usageMonth` differs from the current one, the counter
  is treated as 0 and overwritten on the next write. No scheduled job resets it.
- `resetAt` is computed as 00:00 UTC on the first of the following month.
- Only `plan === 'free'` is gated. Premium is unlimited.
- Charging happens in `createSession` **before** the Firestore writes and **after** the
  first idempotency check (`sessions.service.ts:287`).

See §8 for the concurrency caveat on this counter.

---

## §7 — Configuration

### §7.1 Server environment

zod-validated at boot (`server/src/config/env.ts`); invalid config logs the flattened
field errors and calls `process.exit(1)`.

| Variable | Type | Default | Notes |
|---|---|---|---|
| `PORT` | number (coerced) | `3000` | |
| `NODE_ENV` | `development` \| `production` \| `test` | `development` | Drives `isDev` / `isProd` / `isTest` |
| `ALLOW_DEMO_ROUTES` | `'true'` \| `'false'` | unset → `false` | Transformed to boolean; second gate on §3.4 |
| `FIREBASE_PROJECT_ID` | string, min 1 | — | **Required.** Admin SDK uses ADC |

Emulator support is env-only (no schema entry): `FIREBASE_AUTH_EMULATOR_HOST`,
`FIRESTORE_EMULATOR_HOST`, wired through the `dev:emu` npm script.

### §7.2 Client environment

`client/src/lib/firebase.ts` reads six `VITE_FIREBASE_*` variables through a
`getRequiredEnv()` helper that **throws at module load** if any is missing —
so a misconfigured client fails immediately and loudly rather than at first auth call.

`VITE_API_BASE_URL` is optional and defaults to `'/api'` (`client/src/lib/api.ts:3`).

### §7.3 The `/api` prefix

The BFF mounts its routes at the **root** — there is no `/api` prefix server-side.
The prefix exists only on the client, and the dev proxy strips it:

```
client fetch('/sessions')
  → buildApiUrl → '/api/sessions'
  → Vite proxy: target http://localhost:3000, rewrite ^/api → ''
  → Express receives GET /sessions
```

`client/vite.config.ts` owns the rewrite. In a deployment without that proxy,
`VITE_API_BASE_URL` must be set to an origin that resolves to the server root.

> Two docs in `docs/` disagree about this: `../archive/api-prefix-audit-task-3-1.md` states the
> base defaults to `http://localhost:3000`, which was superseded by
> `../archive/api-prefix-fix-task-3.md`. The code is authoritative — the default is `/api`.

### §7.4 Local dev

Client `http://localhost:5173` (Vite), server `http://localhost:3000`. Server scripts:
`dev`, `build`, `start`, `lint`, `test` (Vitest), `dev:emu`, `emu:token`,
`test:session-result`, `seed` (`seed-forecast.ts`), `seed:clean` (`clean-seed.ts`),
`migrate:demo-data`, `probe:all-sessions`.

Tests: `server/tests/health.test.ts`, `sessions.repository.test.ts`,
`sessions.service.test.ts`. `server/` lint and `tsc --noEmit` both pass as of this
doc's date; `client/` lint does not — see KG-C-2.

---

## §8 — Known Constraints

| Constraint | Detail |
|---|---|
| Usage counter is not transactional | `incrementUsage` is a read-then-write: `userRef.get()`, compute `newUsage + 1`, `userRef.set({...}, {merge:true})` — with no `runTransaction` and no `FieldValue.increment` (`user.repository.ts:211-254`). Two concurrent `POST /sessions` can both read the same count and both write `n+1`, letting a free user exceed 3. The 60s idempotency window narrows but does not close this, since it only catches requests sharing an `idempotencyKey`. **Reasoned from the code; not reproduced under load.** |
| CORS allowlist is localhost-only | Hardcoded to the two Vite dev origins (`server.ts:18-21`); no env-driven production origin. Any non-localhost browser origin is refused `Access-Control-Allow-Origin`. |
| Auth is opt-in per route | `authMiddleware` is attached per handler, not to the protected routers. A route added without it is silently public. |
| zod issue detail discarded | All `safeParse` failures collapse to `'Invalid request body'`; `parsed.error` is never forwarded into `AppError.details`, so 400s are not field-actionable. |
| `INVALID_SESSION_STATUS` maps to two HTTP codes | 409 from `/clarify`, 400 from `/retry` (§3.3). |
| Demo endpoint shape differs from the real one | `/demo/sessions/:id` omits `sentimentTimeSeries` and caps evidence at 20 (§3.4). Tracked KG-C-4. |
| `requireAuth` is dead code | Exported from `middleware/auth.ts`, referenced nowhere in `src/`. |
| Lazy user-document migration on read | `findById` detects legacy fields (`fullName`, `subscriptionStatus`, `membershipTier`) or missing canonical ones and performs a `set(..., {merge:true})` **write during a read** (`user.repository.ts:39-60`). Self-healing, but it means `GET /me` can mutate. |
| Redundant session read per detail request | `getSessionDetail` calls `getSession` for ownership, then `getSessionResult` calls it again inside the same `Promise.all` — two reads of the same document (`sessions.service.ts:245-261`). |
| `retryFailedSession` swallows delete failures | A failed delete is `console.warn`-ed and execution continues to create the replacement session (`sessions.service.ts:367-374`). Deliberate per Slice 12 — but it can leave a partially-deleted session behind. Note it uses `console.warn`, not the pino `logger`, so it bypasses structured logging. |
