# frontend_sprints.md
> Domain: C — Frontend / BFF
> Type: Sprints
> Last updated: 2026-07-20
> TL;DR: Live status of the client and BFF — what's closed, what's open, what's deferred, and the Domain C Known Gaps (KG-C-1 … KG-C-15). Open this to see what frontend/BFF work remains and which debts are tracked.

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
| Gap pass — KG-C-9, KG-C-5, KG-C-11, KG-C-12 | Closed | 2026-07-20 | Usage charging made atomic via `runTransaction`; fabricated-data fallbacks removed from **both** the client and the BFF; `mockData.ts` deleted; trending re-ranked by 24h volume. Server tests 11→24; opened KG-C-13 and KG-C-14 |
| Trending `/events` migration — KG-C-13 | Closed | 2026-07-20 | `/markets` → `/events`; `TrendingForecast` reshaped to an event (contract change, §3.9 of `frontend_contracts.md`); binary vs multi-outcome rendering in both consumers; single-fixture noise dropped via the `Games` tag. Server tests 24→28. Closed KG-C-14 incidentally (the rewritten card renders no trend label and formats volume) |
| Trending topic filter — KG-C-15 | Closed | 2026-07-20 | Trending constrained to the pipeline's 13 forecastable topic domains via an excluded-category check plus a Polymarket tag → topic map; `debug` log for unmapped tags, `warn` + `[]` when nothing is on topic. No contract change. Server tests 28→33 |
| Trending cleanup — finish the section | Closed | 2026-07-20 | Dead `trend` removed (KG-C-14 residual); `slug` dropped from the contract; `url` wired as the row link on both surfaces; strike-ladder outcomes deduped on rendered percentage; orphaned `trendingForecasts` collection constant removed; **deleted `components/TrendingForecasts.tsx`** — an unimported component holding four inline hardcoded forecasts, which survived the `mockData.ts` deletion because its fixtures were inline rather than imported. Server tests 33→35 |
| BFF production-hardening — KG-C-10 | Closed | 2026-07-22 | Path-prefix app-level auth (structural, no more per-route opt-in); env-driven CORS via `CORS_ORIGINS` (no hardcoded localhost in prod); `GET /me` made idempotent (`findById` pure read, migration + expiry-downgrade moved to write paths, downgrade enforced live in `incrementUsage`); zod field detail forwarded via a `validationError` helper. Server tests 35→47. See §4 design note |

> Slices 2.5–2.7 are documented in `../archive/backend-audit.md` and referenced by CLAUDE.md,
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
scheduled. KG-C-9, previously the highest-severity gap, was closed on 2026-07-20
along with KG-C-5, KG-C-11 and KG-C-12. **No High-priority gap remains open.**
KG-C-13, KG-C-14 and KG-C-15 also closed the same day, and KG-C-10 closed
2026-07-22. The remaining Mediums are KG-C-1, KG-C-2, KG-C-3 and KG-C-8.

> **Committed to `main` on 2026-07-21.** The 2026-07-20 work closing KG-C-5, KG-C-9,
> KG-C-11, KG-C-12, KG-C-13, KG-C-14 and KG-C-15 — plus these doc updates — landed in a
> single commit. §5 now describes `main`. Two related items were left out of the commit
> and remain open: the dead `trendingForecasts` read rule in `firestore.rules:111`
> (removing it needs a Firestore deploy) and the unwired `scripts/seed.ts` writer.

---

## §3 — Deferred Items

| Item | Deferred from | Reason | Condition to revisit |
|---|---|---|---|
| Market comparison card (live data) | Slice 13 | Agent emits `marketProbability: null` and `marketComparison: []` | Agent populates them; shapes specified in `../backend-specs/market-sentiment-spec.md` |
| Sentiment time series | Slice 13 | Agent writes no points | As above |
| `predictionSeries` feature | Task 10 | **Closed on the client side.** The product decision was made: `cards/MarketPriceHistory.tsx` renders the market's own price history against the Anizai forecast, with pure geometry in `lib/marketPriceChart.ts` (19 unit tests). Remaining dependency is Domain B actually writing the documents | — |
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
| KG-C-5 | ~~`client/src/services/trending.service.ts` catches API failure and silently returns `mockSessions` fixtures, so trending displays fabricated data as real while looking healthy.~~ **CLOSED 2026-07-20.** Fallback removed — the catch now `console.error`s and returns `[]`, and consumers fall through to their existing empty states. `client/src/data/mockData.ts` deleted (`trending.service.ts` was its last importer); `client/src/data/` is now gone. Scope was wider than recorded — see note below | Doc rewrite 2026-07-18 | ~~Medium~~ Closed | — |
| KG-C-6 | Market Comparison and Sentiment Analysis cards render permanent empty states — the agent emits `marketProbability: null`, `marketComparison: []`, and no sentiment points | Task 14 | Low (Domain C) | Domain B populates them. Expected shapes: `../backend-specs/market-sentiment-spec.md` |
| KG-C-7 | `agentEvents` never appear in production. The Domain C read path is complete and verified in-repo (listener, Rule B run scoping, timeline component, 4/4 unit tests); the deployed agent image is a Sprint ~21-era build (`AGENT_VERSION 0.4.0-sprint21-*`) that predates the Sprint 22+ emission mechanism. Rule B additionally requires the hub to write `currentRunId`, ratified in the Domain-B Sprint 25 plan but not yet built. **Relayed from the Domain-B owner — not verifiable from this repo** | Sprint 25 | Medium | Cumulative agent-image rebuild (a separate track from any single sprint), then `currentRunId` emission |
| KG-C-8 | Orphaned `forecastQueries` document after clarify-then-delete. `POST /sessions` writes its queue doc at `forecastQueries/{sessionId}`, but `POST /sessions/:id/clarify` writes at a Firestore auto-id (`session.repository.ts:462`); `deleteSession` only deletes `forecastQueries/{sessionId}` (`:548`). A clarified session that is later deleted appears to leave its clarify-path queue doc behind. **Reasoned from the code paths; not reproduced against live Firestore** | Doc rewrite 2026-07-18 | Medium | Query queue docs by `sessionId` on delete, or write the clarify doc under a deterministic id |
| KG-C-9 | ~~**Free-tier usage limit is bypassable under concurrency.** `incrementUsage` was a read-then-write with no `runTransaction`, so two concurrent `POST /sessions` could read the same count and both write `n+1`, letting a free user exceed `FREE_FORECAST_LIMIT = 3`.~~ **CLOSED 2026-07-20.** The read, limit check and write now run inside `firestore.runTransaction` via a new `runTransaction` helper in `firebase.service.ts`; Firestore aborts and retries the callback if the doc changes underneath it. Covered by `tests/user.repository.test.ts` (7 tests) and reproduced-then-verified against the Firestore emulator (see note below) | Doc rewrite 2026-07-18 | ~~High~~ Closed | — |
| KG-C-10 | ~~**Production-hardening cluster — the BFF is dev-shaped, not deploy-ready.** Four independent findings share one root cause: nothing about the BFF's configuration or defaults assumes a hostile caller. (a) **Auth is opt-in per route** — `authMiddleware` is attached to individual handlers, not to the protected routers, so a route added to `routes/sessions.ts` without it is silently public. (b) **CORS is hardcoded to localhost** — the allowlist is a literal `Set` of the two Vite dev origins (`server.ts:18-21`) with no env-driven production origin. (c) **`GET /me` mutates on read** — `findById` performs a `set(..., {merge:true})` migration write during a GET (`user.repository.ts:39-60`), so a read endpoint is not idempotent. (d) **zod issue detail is discarded** — every `safeParse` failure collapses to `'Invalid request body'`, so 400s are not field-actionable for any client.~~ **CLOSED 2026-07-22.** All four fixed — see the design note below | Doc rewrite 2026-07-18 | ~~Medium~~ Closed | — |
| KG-C-11 | ~~**The BFF had its own fabricated-data fallback.** `trendingRepository.fetchFresh` caught any Polymarket failure and served seeded Firestore documents from `trendingForecasts` — its own comment called them *"mock data"* — so a dead upstream rendered as a healthy feed. The same defect as KG-C-5, one layer down; fixing the client alone left it live.~~ **CLOSED 2026-07-20.** Fallback removed; failures propagate through the route's `next(error)` to a 500, and the client (post-KG-C-5) logs and renders empty. Guarded by two tests asserting a 503 and a network error each reject | KG-C-5 follow-up 2026-07-20 | ~~Medium~~ Closed | — |
| KG-C-12 | ~~**Trending ranked by lifetime volume, and the sort key was not the displayed field.** `order=volumeNum` ranks by all-time volume, filling the panel with long-dead novelty and candidate-field markets — *"Will Jesus Christ return before 2027?"* ($64M lifetime, ~$4K/day) outranked every live market. Separately, `popularityScore` read `volume24hr` while the query sorted by `volumeNum`, so the list was ordered by one metric and labelled with another, rendering a visibly non-monotonic "Popularity" column. The mapper's OR-fallback from `volume24hr` to `volume` also swapped in lifetime volume whenever 24h volume was zero.~~ **CLOSED 2026-07-20.** Now `order=volume24hr`, with `popularityScore` reading that same field only. Verified against live data: the landing page matches Polymarket's own cards number-for-number | KG-C-5 follow-up 2026-07-20 | ~~Medium~~ Closed | — |
| KG-C-13 | ~~**Trending queries `/markets`, but Polymarket's UI groups by `/events`.** `/markets` returns individual binary legs, so single candidates and per-match esports markets surfaced as top-level questions (*"Estoril Open: Titouan Droguet vs Camilo Ugo Carabelli"*, *"Will Lionel Messi win the 2026 Ballon d'Or?"*) instead of the event cards a user recognises.~~ **CLOSED 2026-07-20.** Migrated to `/events`; `TrendingForecast` reshaped to an event (**contract change — shape in `frontend_contracts.md §3.9`**); both consumers updated; single-fixture noise dropped by a tag filter. Server tests 24→28 | KG-C-12 follow-up 2026-07-20 | ~~Low~~ Closed | — |
| KG-C-14 | ~~**The trending "trend" indicator is fabricated from a static probability.** `toTrendingView` derived `up/down/stable` by thresholding the *current* probability, and the trending card rendered those as **"Rising" / "Falling" / "Steady"** — movement language for a value never compared against anything. A market flat at 2% for months displayed as "Falling" forever, so the column carried no information. Same surface rendered `Popularity: 4774.118238000001`, a raw float.~~ **CLOSED 2026-07-20, incidentally, by the KG-C-13 migration** — neither half was fixed deliberately. The rewritten card renders a probability and a formatted volume (`$8.2M 24h vol`) and **no trend label at all**: searching `client/src` for the strings "Rising", "Falling" and "Steady" now returns zero hits. The raw float is gone with `popularityScore` → `volume24h`. The dead `trend` field it left behind — computed but read by nothing — was removed in the same day's cleanup pass, along with its three view-type declarations | User observation 2026-07-20 | ~~Medium~~ Closed | — |
| KG-C-15 | ~~**Trending surfaced events the pipeline cannot forecast.** `/trending` returned the globally hottest Polymarket events with nothing constraining them to topics Domain A ingests sources for, so sports championships, box-office, weather and tweet-count markets reached the panel and a user could start a forecast we have no evidence for.~~ **CLOSED 2026-07-20.** `trending.repository.ts` now classifies each event by tag: an excluded-category tag drops it outright, then it must carry a tag mapping to one of the pipeline's 13 topic domains. Measured live: 25 kept / 23 dropped of 48. See the design note below | User request 2026-07-20 | ~~Medium~~ Closed | — |

> KG-C-8, KG-C-9, and KG-C-10 were all raised by reading source during the 2026-07-18
> documentation rewrite, not by any prior audit. KG-C-9 was the only functional defect
> among them and was **closed on 2026-07-20** in its own code pass. KG-C-8 and KG-C-10
> remain open.
>
> **KG-C-11 … KG-C-14 were raised on 2026-07-20** while closing KG-C-5, and are split
> by failure class on purpose. KG-C-11 is *fabrication* — the BFF substituted seeded
> data for a live feed, the same defect as KG-C-5 one layer down. KG-C-12 and KG-C-13
> are *wrong query* — nothing is fabricated, the data is real but the wrong slice of it.
> KG-C-14 is *misrepresentation* — the data is real and correctly fetched, but the UI
> labels it as something it is not. Fixing one class does not address the others; do
> not collapse them.
>
> **KG-C-15 — the topic filter, and why it is a mirror.**
>
> The pipeline's covered topics are defined by `MASTER_KEYWORD_LIST` in
> `data-pipeline/processing/keyword_sniper.py:109` — ~188 keywords whose section
> comments group them into exactly **13 domains**. `silver_job.py` runs `snipe()` over
> every article and the score gates OpenAI enrichment in Gold, so a topic absent from
> that list is a topic we ingest no evidence for. It is Domain A, read-only for us.
>
> **Consumption strategy: mirror the 13 domain names only, never the keywords.**
> A shared config artifact does not exist — `data-pipeline/config/` is pure Python — so
> reading the canonical list at runtime is impossible; the file also forbids
> cross-module import (Service Isolation), so it would be wrong even if the languages
> matched. Mirroring is the house pattern: `ingestion/newsapi_producer.GENERAL_KEYWORDS`
> mirrors a subset of the same list with an "update BOTH files" note.
>
> Mirroring only the domains, and not the keywords, was a measurement rather than a
> preference. Against 100 live events:
>
> | Layer | Admitted (of 50 eligible) |
> |---|---|
> | All 188 keywords matched against event text | 47 |
> | Polymarket tag → domain map alone | 49, and 50 once region tags were added |
> | Both | 50 |
>
> The keyword half buys one event and is where the churn lives (Phase 7B deleted ~10
> terms as "too broad"), so it was dropped. The tag map that does the work describes
> **Polymarket's** taxonomy, which is ours to maintain and does not drift when Ron
> retunes keywords.
>
> **A tag map is required, not a convenience.** The vocabularies genuinely differ:
> Polymarket says `Fed` / `Fed Rates` / `fomc`, the sniper says "federal reserve" /
> "rate cut". Matching pipeline keywords against event text dropped **both** Fed
> Decision events — a false negative, since FRED is one of the nine producers.
>
> **Exclusion is checked first and wins.** "Will Trump be in the WC Champions Photo?"
> carries both `Trump` (admitted) and `Soccer`; the Elon tweet-count markets carry both
> `Politics` and `Tweet Markets`. Without that ordering, both ride in on a political tag.
>
> **Failure mode.** An unmapped tag means a silent drop, so events rejected for having
> *no* recognised tag log their tags at `debug` — the gap is discoverable rather than
> invisible. If nothing survives, the repository returns `[]` and logs at `warn`; it
> never relaxes the filter to fill the panel, which would be KG-C-11's defect in a new
> costume.
>
> **Verification of the KG-C-9 fix — reproduced, then closed.** The race was confirmed
> against the Firestore emulator before being declared fixed:
>
> | Run | Setup | Result |
> |---|---|---|
> | Control (pre-fix read-then-write) | 10 concurrent `incrementUsage`, free user, limit 3 | **10 charged**, stored count clobbered to `1` — race reproduced |
> | Fixed (`runTransaction`) | same | **3 charged, 7 refused**, stored count `3` |
> | End-to-end via HTTP | 10 concurrent `POST /sessions`, distinct `idempotencyKey`s, BFF on the emulator | **3 × 201, 7 × 403 `PLAN_LIMIT_EXCEEDED`**; `GET /me` reports `monthlyForecastsUsed: 3`; exactly 3 sessions persisted |
>
> Distinct idempotency keys were used deliberately so the 60s idempotency window could
> not mask the race. Repeatable via `server/scripts/probe-usage-race.ts` (refuses to run
> unless `FIRESTORE_EMULATOR_HOST` is set). The unit tests in
> `tests/user.repository.test.ts` cover the logic; the emulator probe covers the
> concurrency.
>
> **Scope note on KG-C-5.** The gap as originally written named only
> `trending.service.ts`. Two facts surfaced while closing it:
> - There is a **second consumer** — `components/landing/QuestionsWeTrack.tsx` — so the
>   fabricated data was also served to logged-out visitors on the public landing page,
>   under the heading *"Live questions from Polymarket."* Both consumers already had
>   empty states (`StateMessage` in the dashboard panel, a bespoke `EmptyState` on the
>   landing card), so no new UI was needed.
> - Returning `[]` rather than rethrowing is **load-bearing.** `App.tsx enterDashboard`
>   awaits this call inside a `Promise.all` whose rejection sets `authError` and blocks
>   dashboard entry, so a rethrow would take the entire dashboard down on any trending
>   outage. A comment in `trending.service.ts` records this; do not "simplify" it into a
>   rethrow.
>
> Verification differed by surface, deliberately: the **landing** empty state was driven
> in-browser with the BFF down (zero rows rendered, one `console.error` per call). The
> **dashboard** panel was verified by code path only — it needs both auth and a live
> BFF, which is incompatible with a BFF-down test.
>
> **Verification of KG-C-11 / KG-C-12.** KG-C-12 was confirmed against the live Gamma
> API before and after: the pre-fix query reproduced the reported sidebar exactly
> (*Jesus Christ · Gedion Timothewos · LeBron 2028 · Oprah …*, in that order), and the
> post-fix landing page matched Polymarket's own cards number-for-number (Fed "no
> change" 93%, "25 bps increase" 6%, Harry Kane 38%). KG-C-11's failure path cannot be
> triggered on demand against a live upstream, so it is pinned by two tests instead —
> a 503 response and a network-level throw must each reject rather than return
> substitute data (`tests/trending.repository.test.ts`).
>
> **KG-C-10 — the production-hardening close (2026-07-22).** Fixed as four
> independent changes, each targeting one finding:
>
> - **(a) Auth is now structural, gated by path prefix.** `authMiddleware` was
>   removed from every per-route handler and from the routers; `server.ts` mounts
>   `app.use(['/me', '/sessions'], authMiddleware)` **before** the two routers, so
>   every path under those prefixes is protected by construction and a newly added
>   handler cannot ship public. Router-level `router.use(authMiddleware)` was tried
>   first and rejected: the routers mount at `/`, so router-level middleware also
>   intercepted unmatched paths and turned the 404 handler into a 401. Path-scoped
>   app-level auth protects the prefixes while letting unknown routes fall through
>   to 404 (asserted in `tests/server.hardening.test.ts`).
> - **(b) CORS is env-driven.** New `CORS_ORIGINS` env var (comma-separated,
>   parsed in `config/env.ts`). `server.ts` allows the Vite dev origins **only when
>   `NODE_ENV !== 'production'`**; production allows exactly what `CORS_ORIGINS`
>   lists and nothing else. Documented in `.env.example`.
> - **(c) `GET /me` is idempotent.** `findById` is now a pure read — the legacy
>   migration write and the expiry-downgrade write were removed from it; both values
>   are still normalized **in memory** for the returned snapshot. Persistence moved
>   to write paths: `reconcileProfile` (the extracted migration) runs on the login
>   path via `syncFromAuth`, and `incrementUsage` evaluates the expiry downgrade
>   **live inside its transaction** — so free-limit enforcement no longer depends on
>   a prior read having persisted the flip (the latent hole where an expired premium
>   who never loaded `/me` got unlimited forecasts). Covered by three new
>   `user.repository.test.ts` cases.
> - **(d) zod detail is forwarded.** New `validationError(zodError)` helper in
>   `middleware/error.ts` wraps `error.flatten()` into `AppError.details`; the error
>   middleware already surfaces `details`. All four `safeParse` sites (three in
>   `routes/sessions.ts`, one in `routes/me.ts`, which also stopped hand-rolling its
>   400) now use it. 400s carry `{ formErrors, fieldErrors }`.
>
> Server tests 35 → 47 (9 hardening + 3 downgrade). Both packages still typecheck.

---

## §5 — Verification Baseline

Re-executed on **2026-07-20** against the working tree, after the gap pass. This is the
baseline a future change should not regress.

| Check | Command | Result |
|---|---|---|
| Server typecheck | `npx tsc --noEmit` in `server/` | ✅ pass |
| Client typecheck | `npx tsc -b` in `client/` | ✅ pass |
| Server tests | `npx vitest run` in `server/` | ✅ **47/47 across 6 files** (11/11 across 3 files before the 07-20 work; 24 after the gap pass, 28 after the `/events` migration, 33 after the topic filter, 35 after the cleanup; 47 after the KG-C-10 hardening on 07-22) |
| Client tests | `npx vitest run` in `client/` | ✅ 31/31 across 3 files — unchanged, no regression |
| Client lint | `npx eslint .` in `client/` | ❌ fails — KG-C-2 |
| App boot | Vite dev server + browser load | ✅ no console errors |
| Public-page responsive | `scrollWidth` vs `clientWidth` @ 375/768/1280 | ✅ no horizontal overflow (`frontend_ui.md §6.2`) |
| Dashboard responsive | Signed-in browser pass @ 375/768/1280 across 8 surfaces | ✅ zero unclipped overflow, zero console errors (`frontend_ui.md §6.2`) |

Client test files: `src/lib/agentEvents.test.ts` (4), `…/predictionOverview/lib/extractDeadline.test.ts` (11),
`…/predictionOverview/lib/deriveVerdict.test.ts` (16).

Server test files: `tests/health.test.ts` (3), `tests/sessions.repository.test.ts` (5),
`tests/sessions.service.test.ts` (3), `tests/user.repository.test.ts` (10 — KG-C-9 plus
the KG-C-10c expiry-downgrade cases), `tests/trending.repository.test.ts` (17 —
KG-C-11/12/13/15 plus the outcome-dedupe rules), `tests/server.hardening.test.ts`
(9 — KG-C-10 a/b/d).

> **Two stale claims corrected here.** `hub-handoff-summary-task-16.md §13` and
> `../archive/final-validation-task-16.md` both state that *"no client test runner is currently
> wired in `client/package.json`."* A runner **is** wired (`"test": "vitest"`, vitest
> 2.1 in devDependencies) and 31 tests pass. Only the **lint** blocker in those docs
> is still real.
