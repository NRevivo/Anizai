# Forecast Result UI Optimization - Task 0.5

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_ui.md §3`](../C_frontend/frontend_ui.md).
> Index: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §3.

## 1. Summary of Changes

The completed forecast result UI now makes the final answer more prominent. The top result card emphasizes the final probability, confidence, and a bottom-line answer derived from the existing probability value, while the detailed explanation remains visible but visually secondary.

Secondary analysis cards were tightened and clarified. Market comparison, sentiment analysis, and evidence now read as supporting signals rather than equally weighted headline cards. Missing market, sentiment, or evidence data now renders safe empty states instead of awkward zeroes or blank areas.

## 2. Files Changed

- `client/src/components/Dashboard.tsx`: Passed the existing evidence count into the result overview and stopped forcing missing market probability to `0`.
- `client/src/components/cards/PredictionOverview.tsx`: Reworked the completed result summary hierarchy around final probability, confidence, bottom-line answer, evidence count, and detailed explanation.
- `client/src/components/cards/MarketComparison.tsx`: Made market probability optional in the UI, added a missing-benchmark state, and softened the card copy as a secondary comparison.
- `client/src/components/cards/SentimentAnalysis.tsx`: Added a no-data state, reduced chart dominance, removed stale exploratory comments, and made latest sentiment values responsive.
- `client/src/components/cards/EvidenceTimeline.tsx`: Renamed and tightened the evidence area, improved filter wrapping, added evidence/no-filter empty states, and made evidence copy secondary to the final forecast.
- `docs/forecast-result-ui-task-0-5.md`: Added this summary.

## 3. Result UI After Changes

- Final answer section: The top card is now labeled `Final Forecast` and leads with a `Bottom Line` panel.
- Confidence/probability display: The probability gauge remains primary, with confidence shown as both a badge and numeric value.
- Explanation area: The detailed explanation is kept in the top card but uses smaller supporting text and a readable max line length.
- Market comparison: The chart remains when market data exists. If no benchmark exists, the card shows a compact empty state instead of treating missing data as `0%`.
- Sentiment analysis: The chart is shorter and secondary. If no sentiment data exists, the card shows a compact empty state.
- Evidence area: The timeline is renamed and framed as supporting evidence, with count text, most influential drivers, responsive filters, and safe no-evidence/no-matching-filter states.
- Chat/follow-up placement: Chat behavior and layout were not changed in this task; it remains a secondary side panel from the prior dashboard layout work.
- Mobile/tablet/desktop behavior: Result cards remain single-column on mobile and tablet, with market/sentiment moving to two columns on larger screens. Evidence filter tabs can scroll horizontally when space is tight.

## 4. What Was Intentionally Not Changed

- No backend changes.
- No API route changes.
- No API contract changes.
- No probability standardization.
- No `normalizePercent` removal.
- No new hub fields.
- No agent events timeline.
- No clarification flow.
- No new session statuses.
- No Firestore reads/listeners changed.
- No data contract changes.
- No message sending or chat behavior changes.

## 5. Validation

Commands run:

- `git status --short`: Checked the dirty worktree and existing modified files before editing.
- `Get-Content -Raw` for the required Task 0.1, 0.2, 0.3, 0.3 safety, 0.4, and 0.4 safety docs.
- `Get-Content -Raw` for the result-related components inspected in this task.
- `git diff -- ...`: Reviewed the result UI changes.
- `npm run lint` from `client`: Failed before linting because ESLint still cannot resolve `@eslint/js` from `Anizai/eslint.config.js`.
- `npx tsc -p tsconfig.app.json --noEmit --pretty false` from `client`: Passed.

Known issue:

- Existing lint blocker remains: `Error [ERR_MODULE_NOT_FOUND]: Cannot find package '@eslint/js' imported from ...\Anizai\eslint.config.js`. This was not fixed because it is unrelated to Task 0.5.

Manual review notes:

- Completed sessions still render through the existing `Dashboard` and result card path.
- The existing probability value and `formatProbability` behavior are preserved.
- Missing optional market, sentiment, and evidence data now render safe UI states without changing service or API contracts.
- No business logic, backend behavior, API behavior, Firestore behavior, or data contracts were intentionally changed.
