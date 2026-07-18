# frontend_sprints.md
> Domain: C — Frontend / BFF
> Type: Sprints
> Last updated: 2026-07-18
> TL;DR: Live status of the client and BFF — what's closed, what's open, what's deferred, and the Domain C Known Gaps (KG-C-1 … KG-C-10). Open this to see what frontend/BFF work remains and which debts are tracked.

## Navigation
- §1 — Status Summary — every slice/sprint, status, close date, key outcome
- Phase Context / Rationale — how the work reorganized itself three times
- §2 — Open Work
- §3 — Deferred Items — parked work and the condition to revisit
- §4 — Known Gaps — the KG-C-* register
- §5 — Verification Baseline — what passes today, and how it was checked

---

## §1 — Status Summary

Dates are **commit dates from `git log`** on `client/` and `server/`, not the dates
written inside the task documents. Where a task document exists but no matching commit
could be identified, the date column says so.

| Slice / Sprint | Status | Closed | Key outcome |
|---|---|---|---|
| Backend skeleton | Closed | 2026-01-27 | Express + TS + firebase-admin (ADC); users service w/ lazy create; sessions API; pino, zod, health, first tests |
| Firestore foundation | Closed | 2026-01-27 | `firestore.rules` + `firestore.indexes.json` |
| Frontend init | Closed | 2026-01-27 | Vite + React + Tailwind SPA shell |
| Trending + auth + limits | Closed | 2026-03-22 | Polymarket trending integration, email auth flow, subscription tracking limits |
| Subscription UI | Closed | 2026-04-25 | Plan/payment/cancel flows in settings |
| Probability standardization | Closed | 2026-04-26 | All probability + sentiment values normalized to 0–1 floats end-to-end |
| Tasks 0.1–0.8 — frontend audit + optimization | Closed | 2026-04-27 | UI map, consistency audit, layout/creation/result optimization, states, responsive, microcopy |
| Session status ownership | Closed | 2026-04-27 | 6-status lifecycle + `forecastQueries` queue writes on the BFF |
| Tasks 2–15 — hub integration V1 | Closed | 2026-04-28 → 04-29 | API prefix, demo hardening, idempotency, real statuses, plan-limit errors, clarification flow, agent-events timeline, result + evidence contracts, suggested actions, follow-ups, tier handling, failed-state retry |
| Task 16 — final validation + handoff | Closed | 2026-04-29 | Contract handoff summary to the hub owner |
| Idempotency index + demo flag | Closed | 2026-04-30 | Composite index for the idempotency lookup; `ALLOW_DEMO_ROUTES` documented |
| Real auth + pipeline schema alignment | Closed | 2026-05-06 | Restored real Firebase auth; client aligned to the agent's payload; demo session fixtures removed |
| Dashboard decision-first redesign | Closed | 2026-05-17 | `PredictionOverview` decomposed; verdict-led layout; brand identity |
| Real-time updates | Closed | 2026-05-18 | Firestore listeners for session doc + messages; `agentEvents` read path prepared |
| Slices 11 + 12 | Closed | 2026-05-20 | Sanitized agent error UI; failed sessions replaced via retry |
| Slice 13 — landing/marketing redesign | Closed | 2026-05-20 | Shared `PageShell`; 6-section landing |
| Slices 2.5–2.7 — payload audit follow-ups | Closed | date not identified | Backend payload audit; canonical `impactOnForecast`; `consensusScore` plumbing removed |
| Sprint 24 + 25 — frontend/BFF contract | Closed | 2026-07-15 | `'answered'` status, `replyToMessageId`, send-lock, thinking indicator, suggested-action chips, Rule A + Rule B agent timeline, CG index + rules deployed |

> Slices 2.5–2.7 are documented in `../backend-audit.md` and referenced by CLAUDE.md,
> but no commit on `client/`/`server/` could be matched to them by message. Their
> outcomes are visible in current code (the canonical-`impactOnForecast` read, the
> absent `consensusScore`), so the work landed — only the dating is unverified.

Full per-slice detail → `frontend_archive.md`. The Sprint 24+25 as-built record is
kept in full at `sprint-24-25-frontend-tasks.md`.

---

## Phase Context / Rationale

Domain C's history reorganized itself three times, which is why its documentation
fragmented so badly before this rewrite:

1. **Product-first build (Jan–Apr 2026).** The SPA and BFF were built against demo
   fixtures and a mock payload, before the agent produced anything real. Screens
   existed for data that did not.
2. **Contract catch-up (Apr 2026).** Once the hub began writing real documents, ~16
   numbered tasks retro-fitted the real contracts onto the existing UI. Each produced
   a change log rather than a spec, so the contracts were described 16 times and
   specified zero times — the direct cause of the field-name drift corrected in
   `frontend_contracts.md §3.4`.
3. **Redesign + real-time (May–Jul 2026).** The dashboard was rebuilt verdict-first,
   demo fallbacks were removed from the session path, and the Firestore listener
   layer landed — invalidating most of the Phase-1 and Phase-2 documentation without
   superseding it in place.

The `docs/C_frontend/` spec set exists to end that pattern: as-built specs are
maintained here, closed work is archived, and change logs stop being the system of
record.

---

## §2 — Open Work

**No sprint is currently open.** All frontend/BFF code work from Sprint 24+25 is
complete and both Firestore deploys (rules + indexes) have landed on `anizai-ai`.

Two items are outstanding but are **not Domain C work**:

| Item | Owner | Blocking |
|---|---|---|
| Agent-image rebuild — unblocks `agentEvents` emission and hub-written `currentRunId` | Domain B | KG-C-7 |
| Live market + sentiment data | Domain B | KG-C-6 |

Domain C's own next candidates are the gaps in §4, none of which is currently
scheduled. KG-C-9 is the highest-severity of them.

---

## §3 — Deferred Items

| Item | Deferred from | Reason | Condition to revisit |
|---|---|---|---|
| Market comparison card (live data) | Slice 13 | Agent emits `marketProbability: null` and `marketComparison: []` | Agent populates them; shapes specified in `../backend-specs/market-sentiment-spec.md` |
| Sentiment time series | Slice 13 | Agent writes no points | As above |
| `predictionSeries` feature | Task 10 | Plumbed end-to-end but agent writes nothing and no client mapper reads it | A product decision to build probability-over-time; otherwise delete the plumbing |
| Tracking / follow feature | — | `followEnabled` / `isFollowing` written `false`, never updated, read by no UI | Product decision |
| Router adoption | — | No router library; navigation is a `useState` union. Costs deep links, history, shareable URLs | A deliberate routing sprint — not a local fix |
| Reasoning-trace retention | Sprint 25 | Rule A drops the agent timeline at `done`; events persist in Firestore but are never shown again | Product decision on whether a finished forecast should show how it was produced |
| Unused client dependencies | — | `axios` and `react-hook-form` are in `client/package.json` with **zero imports** in `client/src` (verified by grep) | Any dependency-hygiene pass |

---

## §4 — Known Gaps

| ID | Description | Raised in | Priority | Condition to address |
|---|---|---|---|---|
| KG-C-1 | Contract types duplicated between `server/src/services/sessions.service.ts` and `client/src/services/session.service.ts` — no shared package, no codegen, no cross-package type test. Identical as of 2026-07-18; nothing enforces it | Doc rewrite 2026-07-18 | Medium | A shared types package, or a CI check asserting the two declarations match |
| KG-C-2 | Client lint is broken — `npx eslint .` in `client/` fails with `Cannot find package '@eslint/js' imported from /Users/noamrevivo/Documents/Anizai/eslint.config.js`. The root flat config imports devDeps that are installed per-package, not at the root | Task 10 / re-verified 2026-07-18 | Medium | Install the ESLint deps at the repo root, or give `client/` its own flat config |
| KG-C-3 | `App.tsx` is a 962-line god-component holding pseudo-routing, all data fetching, all view-model mapping, and the three listener subscriptions simultaneously | Task 0.1 / re-verified 2026-07-18 | Medium | Split routing, the mapping layer, and the listener lifecycle into separate modules |
| KG-C-4 | Demo routes exist in the BFF (`routes/demo.ts`), hard-code `demo-user-001`, bypass the service layer and every ownership check, and are marked `REMOVE IN PRODUCTION`. Double-gated behind `isDev && ALLOW_DEMO_ROUTES` so they cannot mount in production. `GET /demo/sessions/:id` also returns a divergent shape (no `sentimentTimeSeries`, evidence capped at 20) | Task 4 | Low | Delete them, or replace with a seeded emulator fixture. No frontend code calls them |
| KG-C-5 | `client/src/services/trending.service.ts` still catches API failure and silently returns `mockSessions` fixtures — the last live importer of `data/mockData.ts`. The Task-3 "demo fallbacks removed" work covered `session.service.ts` only, so trending can display fabricated data as real while looking healthy | Doc rewrite 2026-07-18 | Medium | Surface a real error/empty state and drop the fallback; then delete `mockData.ts` |
| KG-C-6 | Market Comparison and Sentiment Analysis cards render permanent empty states — the agent emits `marketProbability: null`, `marketComparison: []`, and no sentiment points | Task 14 | Low (Domain C) | Domain B populates them. Expected shapes: `../backend-specs/market-sentiment-spec.md` |
| KG-C-7 | `agentEvents` never appear in production. The Domain C read path is complete and verified in-repo (listener, Rule B run scoping, timeline component, 4/4 unit tests); the deployed agent image is a Sprint ~21-era build (`AGENT_VERSION 0.4.0-sprint21-*`) that predates the Sprint 22+ emission mechanism. Rule B additionally requires the hub to write `currentRunId`, ratified in the Domain-B Sprint 25 plan but not yet built. **Relayed from the Domain-B owner — not verifiable from this repo** | Sprint 25 | Medium | Cumulative agent-image rebuild (a separate track from any single sprint), then `currentRunId` emission |
| KG-C-8 | Orphaned `forecastQueries` document after clarify-then-delete. `POST /sessions` writes its queue doc at `forecastQueries/{sessionId}`, but `POST /sessions/:id/clarify` writes at a Firestore auto-id (`session.repository.ts:462`); `deleteSession` only deletes `forecastQueries/{sessionId}` (`:548`). A clarified session that is later deleted appears to leave its clarify-path queue doc behind. **Reasoned from the code paths; not reproduced against live Firestore** | Doc rewrite 2026-07-18 | Medium | Query queue docs by `sessionId` on delete, or write the clarify doc under a deterministic id |
| KG-C-9 | **Free-tier usage limit is bypassable under concurrency.** `incrementUsage` is a read-then-write — `userRef.get()`, compute `newUsage + 1`, `set(..., {merge:true})` — with no `runTransaction` and no `FieldValue.increment` (`user.repository.ts:211-254`). Two concurrent `POST /sessions` can read the same count and both write `n+1`, letting a free user exceed `FREE_FORECAST_LIMIT = 3`. The 60s idempotency window narrows the race but does not close it — it only catches requests sharing an `idempotencyKey`. **Reasoned from the code; not reproduced under load** | Doc rewrite 2026-07-18 | **High** | Wrap the read-modify-write in `firestore.runTransaction`, or restructure to `FieldValue.increment` with a follow-up limit check. Scheduled as a separate code pass |
| KG-C-10 | **Production-hardening cluster — the BFF is dev-shaped, not deploy-ready.** Four independent findings share one root cause: nothing about the BFF's configuration or defaults assumes a hostile caller. (a) **Auth is opt-in per route** — `authMiddleware` is attached to individual handlers, not to the protected routers, so a route added to `routes/sessions.ts` without it is silently public. (b) **CORS is hardcoded to localhost** — the allowlist is a literal `Set` of the two Vite dev origins (`server.ts:18-21`) with no env-driven production origin. (c) **`GET /me` mutates on read** — `findById` performs a `set(..., {merge:true})` migration write during a GET (`user.repository.ts:39-60`), so a read endpoint is not idempotent. (d) **zod issue detail is discarded** — every `safeParse` failure collapses to `'Invalid request body'`, so 400s are not field-actionable for any client | Doc rewrite 2026-07-18 | Medium | A deliberate production-hardening pass: router-level auth, env-driven CORS, move the migration write out of the read path, forward zod issues into `AppError.details` |

> KG-C-8, KG-C-9, and KG-C-10 were all raised by reading source during the 2026-07-18
> documentation rewrite, not by any prior audit. KG-C-9 is a functional defect and is
> queued for its own code pass; **no fix was applied during the doc work.**

---

## §5 — Verification Baseline

Everything below was executed on **2026-07-18** against the working tree. This is the
baseline a future change should not regress.

| Check | Command | Result |
|---|---|---|
| Server typecheck | `npx tsc --noEmit` in `server/` | ✅ pass |
| Client typecheck | `npx tsc -b` in `client/` | ✅ pass |
| Server tests | `npx vitest run` in `server/` | ✅ 11/11 across 3 files |
| Client tests | `npx vitest run` in `client/` | ✅ 31/31 across 3 files |
| Client lint | `npx eslint .` in `client/` | ❌ fails — KG-C-2 |
| App boot | Vite dev server + browser load | ✅ no console errors |
| Public-page responsive | `scrollWidth` vs `clientWidth` @ 375/768/1280 | ✅ no horizontal overflow (`frontend_ui.md §6.2`) |
| Dashboard responsive | Signed-in browser pass @ 375/768/1280 across 8 surfaces | ✅ zero unclipped overflow, zero console errors (`frontend_ui.md §6.2`) |

Client test files: `src/lib/agentEvents.test.ts` (4), `…/predictionOverview/lib/extractDeadline.test.ts` (11),
`…/predictionOverview/lib/deriveVerdict.test.ts` (16).

> **Two stale claims corrected here.** `hub-handoff-summary-task-16.md §13` and
> `final-validation-task-16.md` both state that *"no client test runner is currently
> wired in `client/package.json`."* A runner **is** wired (`"test": "vitest"`, vitest
> 2.1 in devDependencies) and 31 tests pass. Only the **lint** blocker in those docs
> is still real.
