# API Prefix Audit - Task 3.1

<!-- archive-banner -->
> ⚠️ **SUPERSEDED — contains inaccuracies.** Historical record only; do not
> cite as current. Corrected content: [`frontend_api.md`](C_frontend/frontend_api.md) §7.3.
> Why this doc is wrong: [`frontend_archive.md`](C_frontend/frontend_archive.md) §2.

## 1. Summary

The current frontend API client uses `VITE_API_BASE_URL` when provided, and otherwise defaults to a hardcoded absolute backend URL: `http://localhost:3000`. The active frontend services call relative route paths like `/sessions`, `/me`, and `/trending`, which are then prefixed with that base URL inside `client/src/lib/api.ts`.

Vite is configured to proxy `/api/*` to the backend and strip the `/api` prefix before forwarding. However, the active frontend code does not currently call `/api/...`; it calls bare route paths and bypasses the proxy whenever the default absolute base URL is used.

On the backend, Express mounts routes without an `/api` namespace. Routes are exposed directly as `/`, `/health`, `/trending`, `/me`, `/sessions`, and `/demo/...`.

There is a clear mismatch today between:

- the Vite proxy strategy, which expects frontend requests to start with `/api`
- the active client API configuration, which defaults to direct calls against `http://localhost:3000`
- the backend route mounting, which exposes unprefixed routes

Also, there is no `.env.example` file present in the repo root for documenting expected API base URL configuration.

## 2. File Audit Table

| File | Current behavior | Issue | Recommended fix |
| --- | --- | --- | --- |
| `client/src/lib/api.ts` | Uses `import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:3000'`; appends service path directly | Default behavior bypasses Vite proxy and hardcodes localhost | In Task 3.2-3.6, standardize on one strategy: either proxy-first relative `/api` calls or an explicit env-managed absolute base |
| `client/src/services/session.service.ts` | Calls `apiRequest('/sessions')`, `apiRequest('/sessions/:id')`, `apiRequest('/sessions/:id/messages')` | Depends on `api.ts` base behavior; no `/api` prefix in service calls | Keep service paths consistent with chosen API client convention |
| `client/src/services/user.service.ts` | Calls `apiRequest('/me')`, `apiRequest('/me/plan')` | Same mismatch as session service | Align with chosen client base/prefix strategy |
| `client/src/services/trending.service.ts` | Calls `apiRequest('/trending', { requireAuth: false })` | Same mismatch as other services | Align with chosen client base/prefix strategy |
| `client/vite.config.ts` | Proxies `/api` to `http://localhost:3000` and strips `/api` before forwarding | Proxy exists but active client code does not use it by default | Either switch client to `/api` requests or remove the unused proxy approach later |
| `server/src/server.ts` | Mounts `rootRoutes`, `healthRoutes`, `trendingRoutes`, `meRoutes`, `sessionsRoutes`, and dev-only `demoRoutes` at the app root | Backend routes are not namespaced under `/api` | If Task 3.2-3.6 introduces `/api`, either mount under `/api` or rely on Vite rewrite intentionally |
| `server/src/routes/root.ts` | Exposes `GET /` | Root route is unprefixed | Decide whether root stays public/unprefixed or moves under `/api` |
| `server/src/routes/health.ts` | Exposes `GET /health` | Unprefixed health route | Decide whether health remains unprefixed |
| `server/src/routes/trending.ts` | Exposes `GET /trending` | Unprefixed API route | Align with final API prefix decision |
| `server/src/routes/me.ts` | Exposes `GET /me`, `PATCH /me/plan` | Unprefixed protected API routes | Align with final API prefix decision |
| `server/src/routes/sessions.ts` | Exposes `/sessions` and nested message routes | Unprefixed protected API routes | Align with final API prefix decision |
| `server/src/routes/demo.ts` | Exposes `/demo/...` in dev only | Unprefixed demo/test routes | Keep separate and explicit if prefixing is added later |
| `client/src/vite-env.d.ts` | Declares optional `VITE_API_BASE_URL` | Only type declaration exists; no checked-in env example documents it | Add env documentation in a later task if needed |
| `.env.example` | Not present | No repository-level example for backend base URL or frontend API env config | Add a documented example later if configuration is intended to be env-driven |

## 3. Hardcoded URLs Found

Hardcoded URLs or host assumptions currently in use:

- `client/src/lib/api.ts`
  - `http://localhost:3000`
- `client/vite.config.ts`
  - proxy target `http://localhost:3000`
- `server/src/server.ts`
  - allowed CORS origins:
    - `http://localhost:5173`
    - `http://127.0.0.1:5173`
- `server/src/index.ts`
  - startup log prints `http://localhost:${env.PORT}`
- `server/src/repositories/trending.repository.ts`
  - external fetch to `https://gamma-api.polymarket.com/...`

Notes:

- The Polymarket URL is an external backend dependency, not part of the frontend/backend prefix mismatch.
- No active client fetch/axios calls were found that hardcode `localhost:3000` outside `client/src/lib/api.ts`.

## 4. `VITE_API_BASE_URL` Usage

`VITE_API_BASE_URL` is used in:

- `client/src/lib/api.ts`
  - `const API_BASE_URL = import.meta.env.VITE_API_BASE_URL ?? 'http://localhost:3000';`
- `client/src/vite-env.d.ts`
  - declared as `readonly VITE_API_BASE_URL?: string;`

Behavior today:

- If `VITE_API_BASE_URL` is set, all `apiRequest()` calls use that base.
- If it is not set, the client calls `http://localhost:3000` directly.
- There is no checked-in `.env.example` documenting the intended value.

## 5. Vite Proxy Behavior

Configured in:

- `client/vite.config.ts`

Current proxy behavior:

- Requests beginning with `/api` are proxied to `http://localhost:3000`
- The proxy rewrites the path by stripping the `/api` prefix

Example:

- frontend request `/api/sessions`
- proxied backend request `/sessions`

Current mismatch:

- Active frontend services do not call `/api/...`
- Therefore the proxy is effectively unused when `api.ts` defaults to `http://localhost:3000`

## 6. Express Route Mounting Behavior

Express app setup is in:

- `server/src/server.ts`

Current route mounting:

- `app.use(rootRoutes)` => `/`
- `app.use(healthRoutes)` => `/health`
- `app.use(trendingRoutes)` => `/trending`
- `app.use(meRoutes)` => `/me`, `/me/plan`
- `app.use(sessionsRoutes)` => `/sessions`, `/sessions/:id`, `/sessions/:id/messages`
- dev only:
  - `app.use(demoRoutes.default)` => `/demo/...`

Important conclusion:

- The backend is currently designed around unprefixed routes.
- Any `/api` convention must be introduced intentionally in Task 3.2-3.6, not assumed to already exist on the server.

## 7. Recommended Implementation Plan for TASK 3.2-3.6

Recommended direction:

1. Pick one canonical frontend request style.
2. Prefer one of these two models and use it consistently:
   - model A: frontend calls relative `/api/...` paths and Vite/dev proxy handles local routing
   - model B: frontend uses `VITE_API_BASE_URL` everywhere and Vite proxy is optional or removed

Recommended practical choice:

- Use relative `/api/...` requests on the frontend.
- Keep `VITE_API_BASE_URL` for explicit override cases only if needed.
- In dev, let Vite proxy `/api` to the backend and strip the prefix.
- In production, either:
  - serve frontend behind a reverse proxy that forwards `/api`, or
  - configure `VITE_API_BASE_URL` to include `/api`

Concrete next-step outline:

1. Update `client/src/lib/api.ts` so the default base path is relative and consistent with `/api`.
2. Decide whether service call sites should pass:
   - `/api/sessions`, `/api/me`, `/api/trending`
   - or bare `/sessions` plus an `API_BASE_URL` that already includes `/api`
3. Keep one rule only; avoid mixing:
   - raw backend host defaults
   - Vite proxy-only assumptions
   - env-provided path prefixes
4. Document the intended `VITE_API_BASE_URL` format in a future `.env.example`.
5. Optionally postpone backend `/api` route mounting unless production deployment actually needs the server itself to expose `/api` directly.

Safety note for the later implementation:

- Because the current backend mounts routes without `/api`, changing the client to `/api/...` is safe only if the client path strategy and Vite/prod routing strategy are updated together.
