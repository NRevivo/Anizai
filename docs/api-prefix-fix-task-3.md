# API Prefix Fix - Task 3

## 1. Summary

The frontend now defaults to a relative `/api` base instead of a hardcoded `http://localhost:3000` URL. This makes active client requests follow a single convention:

- frontend request paths stay service-local such as `/me`, `/sessions`, and `/trending`
- the shared API client prefixes them with `/api`
- Vite dev proxy forwards `/api/*` to the backend and strips the prefix before hitting Express routes

This keeps business logic unchanged while making the client, env handling, and dev proxy behavior consistent.

## 2. Files Changed

- `client/src/lib/api.ts`
  - changed the default API base from `http://localhost:3000` to `/api`
  - added `buildApiUrl()` so base and path are joined safely without double slashes
- `docs/api-prefix-fix-task-3.md`
  - task summary and validation notes

## 3. Final API Convention

Final convention after this task:

- frontend effective calls: `/api/...`
- env override: `VITE_API_BASE_URL` is still supported
- default base when env is unset: `/api`
- Vite dev behavior:
  - `/api/sessions` -> proxy -> backend `/sessions`
  - `/api/me` -> proxy -> backend `/me`
  - `/api/trending` -> proxy -> backend `/trending`
- Express route mounting:
  - unchanged
  - routes are still mounted without `/api`
  - alignment is provided by the existing Vite rewrite rather than backend remounting

Conceptual request checks:

- load current user:
  - frontend `apiRequest('/me')`
  - effective request `/api/me`
  - proxied backend route `/me`
- create session:
  - frontend `apiRequest('/sessions', { method: 'POST' })`
  - effective request `/api/sessions`
  - proxied backend route `/sessions`
- fetch sessions:
  - frontend `apiRequest('/sessions')`
  - effective request `/api/sessions`
- fetch session detail:
  - frontend `apiRequest('/sessions/:id')`
  - effective request `/api/sessions/:id`
- send follow-up message:
  - frontend `apiRequest('/sessions/:id/messages', { method: 'POST' })`
  - effective request `/api/sessions/:id/messages`
- plan limit response:
  - same request path behavior as create session
  - error shape handling remains unchanged in `apiRequest()`

## 4. Validation Results

- `git status --short`
  - showed the intended modified API client file and this doc
- `npx tsc -p tsconfig.app.json --noEmit --pretty false`
  - passed
- `npm run lint`
  - failed only due to the known external ESLint config blocker:
    - `Cannot find package '@eslint/js' imported from .../eslint.config.js`

## 5. Risks / Notes

- No backend route logic or business logic was changed.
- Express is still mounted at unprefixed paths, so production environments must either:
  - serve the frontend behind a reverse proxy that forwards `/api/*`, or
  - set `VITE_API_BASE_URL` to an appropriate `/api`-aware base
- Because service call sites still pass bare route paths such as `/sessions`, the `/api` convention is centralized entirely in the shared API client, which reduces drift across services.
