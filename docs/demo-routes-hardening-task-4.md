# Demo Routes Hardening - Task 4

## 1. Summary

Demo routes were found registered in `server/src/server.ts` and previously enabled for every development run. They are now gated behind both:

- development mode
- `ALLOW_DEMO_ROUTES === true`

This makes demo routes unavailable by default and unavailable in production without changing any UI, session, probability, plan, or other business behavior.

## 2. Files Changed

- `server/src/config/env.ts`
  - added parsed optional `ALLOW_DEMO_ROUTES` environment flag
- `server/src/server.ts`
  - changed demo route registration from `isDev` only to `isDev && env.ALLOW_DEMO_ROUTES`
- `docs/demo-routes-hardening-task-4.md`
  - task summary and validation notes

## 3. Demo Route Registration Found

Registration location:

- `server/src/server.ts`
  - previously:
    - `if (isDev) { ... app.use(demoRoutes.default) }`
  - now:
    - `if (isDev && env.ALLOW_DEMO_ROUTES) { ... app.use(demoRoutes.default) }`

Route file:

- `server/src/routes/demo.ts`
  - `GET /demo/sessions`
  - `GET /demo/sessions/:id`
  - `GET /demo/user`

Other demo references found:

- `server/scripts/seed.ts`
  - explicit manual Firestore seed script using `DEMO_USER_ID = 'demo-user-001'`
- `client/src/services/user.service.ts`
  - local frontend demo fallback user
- `client/src/services/session.service.ts`
  - local frontend demo fallback sessions and messages

## 4. Chosen Approach: Removed or Gated

Chosen approach:

- gated, not removed

Reason:

- `routes/demo.ts` may still be useful for explicit local development/testing
- default server startup should not expose unauthenticated demo data routes

Final rule:

- demo routes load only when:
  - `NODE_ENV === 'development'`
  - and `ALLOW_DEMO_ROUTES=true`

## 5. Production Safety Notes

- Demo routes are not mounted in production because they still require development mode and the explicit allow flag.
- Demo routes are not mounted by default in normal development runs anymore.
- `server/src/routes/demo.ts` only exposes read routes; it does not create or mutate session/user data directly.
- The remaining `demo-user-001` write path in `server/scripts/seed.ts` is a manual script, not a default runtime route.
- Frontend demo fallback data in `client/src/services/user.service.ts` and `client/src/services/session.service.ts` is local client-side fallback behavior and does not expose server demo routes by default.

## 6. Validation Results

- `git status --short`
  - showed only the intended server hardening files and this doc
- `npx tsc -p tsconfig.app.json --noEmit --pretty false` from `client`
  - passed
- `npm run lint` from `client`
  - failed only due to the known external ESLint config blocker:
    - `Cannot find package '@eslint/js' imported from .../eslint.config.js`
