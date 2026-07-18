# frontend_archive.md
> Domain: C — Frontend / BFF
> Type: Archive
> Last updated: 2026-07-18
> TL;DR: Index of every closed Domain-C work item — the 40 historical task logs, audits, safety reviews and process artifacts in `docs/archive/`, each classified by whether its content is still true. Open this to find the history behind a closed slice, or to check whether an old doc can be trusted.

**Append-only.** Never edit an existing entry once written; add new entries at the end.

**All 40 files live under `docs/archive/`**, moved there from the top level of `docs/`
on 2026-07-18 so that `docs/` holds only live material. The `audits/` subfolder was
preserved as `docs/archive/audits/`. Nothing was deleted and no historical content was
edited — each file carries only a banner at the top pointing here.

## Navigation
- §1 — How to read this index — the four classifications
- §2 — Superseded-wrong — do not trust without checking the correction
- §3 — Superseded-correct — accurate history, content absorbed
- §4 — Process artifacts — reviews, checks, git records
- §5 — Not archived — documents that are still live
- §6 — Supersession chains

---

## §1 — How to read this index

Two genres of document are mixed in `docs/`, and they age differently.

A **change log** ("Task N — here is what I changed") describes a point-in-time diff. A
statement like *"no retry flow yet"* was true the day it was written. Later work does
not make it wrong; it makes it history.

A **current-state inventory** ("here is how the system works") claims to describe the
system *now*. When the code moves, these become actively false, and a reader who finds
one first is misled.

| Tag | Meaning | Banner applied |
|---|---|---|
| 🔴 **Superseded-wrong** | Makes claims that are factually false against current code, or documents a contract with wrong field names | `SUPERSEDED — contains inaccuracies` + pointer to the correction |
| 🟢 **Superseded-correct** | Accurate point-in-time record; content verified and absorbed into a `C_frontend/` spec | `Historical change log — superseded by …` |
| ⚙️ **Process artifact** | Review verdict, validation run, or git record. No architectural claims | `Historical process record` |
| ✅ **Still-live** | Not archived — see §5 | none |

A document is tagged 🔴 only for **contract-level or inventory-level errors**, not for
being superseded by later work. That bar matters: most of these files are honest
history and should read as such.

---

## §2 — Superseded-wrong

Ten documents. Each carries a banner naming its correction. **Do not cite these as
current.**

| Doc | Date | What is false | Corrected in |
|---|---|---|---|
| `../archive/ui-map-task-0-1.md` | 2026-04-27 | Lists `ChangelogPage`/`BlogPage` and landing components `ProductExplanation`/`UIShowcase`/`WhoItsFor`/`FinalCTA` — none exist. `cards/PredictionOverview.tsx` as a god-component (now a directory of 8 modules). Demo-data fallbacks on the session path (removed). `awaiting_clarification` "not found in frontend types or UI" (fully implemented). "No failed-result screen" (implemented + retry). "No assistant streaming/running state" (`AgentEventsTimeline` exists). Drawers `w-80`/`w-96` "may exceed viewport" (`min()`-clamped; measured 320 px). Grid `[280px…360px]` (actually `[252px…304px]`). "No mobile modal stacking" (modal stacks). Hardcoded confidence labels and a `Math.random()` gradient id | `frontend_ui.md` §2, §3, §5, §6.1, §6.2 |
| `../archive/audits/landing-audit.md` | 2026-05-20 | Inventories `ProductExplanation.tsx`, `UIShowcase.tsx`, `WhoItsFor.tsx`, `FinalCTA.tsx` — **four of its eight section files do not exist.** Dated the same day as the `PageShell` landing redesign, so it documents the pre-redesign page | `frontend_ui.md` §2 |
| `../archive/project-file-map-task-1-1.md` | 2026-04-27 | "There are **no Firestore listeners** implemented" — three exist and are wired. Session route list omits `/clarify` and `/retry`. "Services call `/sessions`, not `/api/sessions`" — the base defaults to `/api`. "`Prediction.status` still `stable\|volatile`" — carries the full `SessionStatus` union | `frontend_overview.md` §2–§3, `frontend_api.md` §3, `frontend_contracts.md` §5.4 |
| `../archive/session-result-contract-task-10.md` | 2026-04-28 | Documents `KeyFactor` as `{rank, title, explanation, direction, weight, supportingEvidenceIds}` — actual is `{label, description, direction, weight, evidence_ids}`. Documents `ReasoningStep` as `{sequence, description, outcome}` — actual is `{step, title, description}`. Contract-level wrong | `frontend_contracts.md` §3.4 |
| `../archive/evidence-contract-task-11.md` | 2026-04-28 | Lists `impact` under "existing fields preserved" — it is a dead duplicate; the UI reads `impactOnForecast` exclusively. Lists `timestamp` as an evidence field — no such field exists. **Its §4 sourceType mapping table is still correct** and was carried over verbatim | `frontend_contracts.md` §3.5, §5.2 |
| `../archive/hub-handoff-summary-task-16.md` | 2026-04-29 | Presents itself as a cross-team **contract**, so staleness carries real risk. `AgentEvent` field list omits `runId`; session fields omit `currentRunId` and `idempotencyKey`; no `POST /sessions/:id/retry`; evidence list repeats the `impact`/`timestamp` errors; §13 claims no client test runner | `frontend_contracts.md` §3–§4, `frontend_sprints.md` §5 |
| `../archive/backend-audit.md` | date not identified | The most rigorous of the old set, and the model for `frontend_contracts.md` — but drifted. Message `status` missing `'answered'`; no `replyToMessageId`; `agentEvents` missing `runId`; session doc missing `currentRunId`; Drift #1 (`impact` ambiguity) and Drift #4 (`summaryMarkdown` nullability) both **resolved since**; "`keyFactors[].evidence_ids` … IDs never linked" — now a full factor→evidence round trip; Drift #2 describes a `deriveVerdict(consensus_score)` parameter that no longer exists | `frontend_contracts.md` §3–§6 |
| `../archive/api-prefix-audit-task-3-1.md` | 2026-04-28 | States the client default base is `http://localhost:3000` and the Vite proxy is "effectively unused". Superseded the same day by its own fix. See §6 | `frontend_api.md` §7.3 |
| `../archive/failed-state-retry-task-15.md` | 2026-04-29 | "Kept retry logic **frontend-only** … posting through the existing `POST /sessions` path." Retry is now a dedicated server endpoint, `POST /sessions/:id/retry`, with delete-then-create semantics | `frontend_api.md` §3.3 |
| `../archive/final-validation-task-16.md` | 2026-04-29 | "No obvious client test script exists in `client/package.json`" — a runner is wired and 31 tests pass. Also references `enterDemoDashboard`, which no longer exists | `frontend_sprints.md` §5 |

---

## §3 — Superseded-correct

Accurate point-in-time change logs. Content verified against source and absorbed into
the live specs.

| Doc | Date | Key outcome | Absorbed into |
|---|---|---|---|
| `../archive/probability-usage-audit-task-2-1.md` | 2026-04-26 | Audit of probability units across the codebase; found stale `/100` conversions | `frontend_contracts.md` §3.4 (the 0–1 convention callout) |
| `../archive/probability-standardization-task-2.md` | 2026-04-26 | Standardized all probability/sentiment values to 0–1 floats; `×100` only at render | `frontend_contracts.md` §3.4 |
| `../archive/ui-consistency-audit-task-0-2.md` | 2026-04-27 | Design-system audit — shared primitives widely bypassed; no radius/spacing scale | `frontend_ui.md` §6, §7 (constraint row) |
| `../archive/dashboard-layout-optimization-task-0-3.md` | 2026-04-27 | Compacted the dashboard workspace; viewport-safe drawer widths | `frontend_ui.md` §3, §6.1 |
| `../archive/forecast-creation-flow-task-0-4.md` | 2026-04-27 | Real async submit; validation; pending guard | `frontend_ui.md` §5 |
| `../archive/forecast-result-ui-task-0-5.md` | 2026-04-27 | Result hierarchy led by probability + bottom line; empty states | `frontend_ui.md` §3 |
| `../archive/status-empty-error-states-task-0-6.md` | 2026-04-27 | Introduced the shared `StateMessage` primitive | `frontend_ui.md` §5.2 |
| `../archive/responsive-layouts-task-0-7.md` | 2026-04-27 | Overflow guards, tap targets, viewport-clamped drawers. Its own §6 records that it was never browser-tested — that gap is now closed | `frontend_ui.md` §6.1, §6.2 |
| `../archive/ui-microcopy-task-0-8.md` | 2026-04-27 | Standardized product copy and terminology | `frontend_ui.md` §5 |
| `../archive/plan-limit-audit-task-1-3.md` | 2026-04-27 | Found real backend enforcement; frontend handling only presentational | `frontend_api.md` §6.1 |
| `../archive/api-prefix-fix-task-3.md` | 2026-04-28 | Default base changed to `/api`; `buildApiUrl()` added; Express stays unprefixed | `frontend_api.md` §7.3 |
| `../archive/demo-routes-hardening-task-4.md` | 2026-04-28 | Demo routes double-gated behind `isDev && ALLOW_DEMO_ROUTES` | `frontend_api.md` §3.4, KG-C-4 |
| `../archive/idempotency-task-5.md` | 2026-04-28 | UUID `idempotencyKey`; 60 s duplicate window; composite index | `frontend_contracts.md` §2.4 |
| `../archive/session-status-ownership-task-6.md` | 2026-04-28 | Six real statuses replace the `stable`/`volatile` buckets; per-status panels | `frontend_contracts.md` §2.1, `frontend_ui.md` §5.1 |
| `../archive/plan-limit-handling-task-7.md` | 2026-04-28 | Structured `PLAN_LIMIT_EXCEEDED` with `details`; blocking UI state | `frontend_api.md` §6.1 |
| `../archive/clarification-flow-task-8.md` | 2026-04-28 | `POST /sessions/:id/clarify`; candidate picker; re-queue | `frontend_api.md` §3.3, `frontend_ui.md` §5.1 |
| `../archive/agent-events-timeline-task-9.md` | 2026-04-28 | `agentEvents` listener + timeline component. *(Its "renders in the completed-result view" behavior was later inverted by Rule A.)* | `frontend_contracts.md` §4.2, `frontend_ui.md` §4 |
| `../archive/suggested-actions-task-12.md` | 2026-04-28 | Suggested-action chips through the existing message flow | `frontend_ui.md` §5.3 |
| `../archive/follow-up-messages-task-13.md` | 2026-04-29 | Live messages listener; optimistic send; pending indicator | `frontend_contracts.md` §3.6, §4.3 |
| `../archive/tier-handling-task-14.md` | 2026-04-29 | `tier_2` no-benchmark empty state | `frontend_ui.md` §7, KG-C-6 |

---

## §4 — Process artifacts

No architectural claims. Retained as history of how the work was reviewed and verified.

| Doc | Date | What it records |
|---|---|---|
| `../archive/task-0-3-safety-review.md` | 2026-04-27 | Task 0.3 diff review — UI/layout only |
| `../archive/task-0-4-safety-review.md` | 2026-04-27 | Task 0.4 diff review — creation UX scoped |
| `../archive/task-0-5-safety-review.md` | 2026-04-27 | Task 0.5 diff review — result UI scoped |
| `../archive/task-0-6-safety-review.md` | 2026-04-27 | Task 0.6 diff review — state presentation scoped |
| `../archive/task-0-7-safety-review.md` | 2026-04-27 | Task 0.7 diff review. Notes that `components/settings/SettingsModal.tsx` does not exist and the real path is `components/SettingsModal.tsx` — **still accurate** |
| `../archive/task-2-safety-review.md` | 2026-04-26 | Task 2 probability-unit review |
| `../archive/ui-regression-check-task-0-9.md` | 2026-04-27 | Post-0.x regression run — tsc, lint, build |
| `../archive/git-status-task-1-2.md` | 2026-04-27 | Branch/worktree snapshot. Records branch `shahar`, which no longer exists |
| `../archive/main-merge-pr-readiness.md` | 2026-04-27 | `shahar` → `main` merge readiness; 24 ahead, no conflicts |
| `../archive/post-main-pull-integration-check.md` | 2026-04-27 | Post-pull integration check; records conflict resolution in `MarketComparison.tsx` and `PredictionOverview.tsx` |

---

## §5 — Not archived

Two documents remain **live** and are not history.

| Doc | Location | Why |
|---|---|---|
| `backend-specs/market-sentiment-spec.md` | unchanged | A forward-looking cross-team contract addressed to the pipeline owner, specifying what the agent should send to activate the Market and Sentiment cards. Verified correct against `client/src/services/session.service.ts`. Archiving it would bury a live dependency |
| `sprint-24-25-frontend-tasks.md` | moved to `C_frontend/` | The as-built record for the most recent sprint, with its own status legend and as-built disclaimer. Verified low staleness. `frontend_sprints.md` points at it for detail |

---

## §6 — Supersession chains

Where two archived documents conflict, this is the resolution order. The later document
wins; the earlier is retained for the reasoning it captures, not its conclusion.

| Superseded | Superseded by | Resolution |
|---|---|---|
| `../archive/api-prefix-audit-task-3-1.md` — default base is `http://localhost:3000`; the Vite proxy is effectively unused; recommends choosing between two models | `../archive/api-prefix-fix-task-3.md` — model A adopted: default base `/api`, `buildApiUrl()` added, Express left unprefixed, alignment via the Vite rewrite | The **fix** doc reflects current code, verified against `client/src/lib/api.ts:3`. The audit's file-by-file table is still useful as the reasoning behind the choice, but its stated behavior is wrong. Current spec: `frontend_api.md` §7.3 |
| `../archive/agent-events-timeline-task-9.md` — timeline renders in both the completed-result view and non-done states | Sprint 25 Rule A (`sprint-24-25-frontend-tasks.md` T6) — timeline renders **only** during `queued`/`claimed`/`running` | Rule A inverted the original behavior; `Dashboard.tsx` no longer imports the component at all. Current spec: `frontend_ui.md` §4.1 |
| `../archive/failed-state-retry-task-15.md` — retry is frontend-only via `POST /sessions` | Slice 12 — `POST /sessions/:id/retry` server endpoint | Retry is server-side with delete-then-create. Current spec: `frontend_api.md` §3.3 |
