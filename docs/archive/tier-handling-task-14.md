# Tier 1 / Tier 2 UI Handling - Task 14

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_ui.md §7`](../C_frontend/frontend_ui.md).
> Index: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §3.

## 1. Summary
- Hardened result UI handling for `tier_1` and `tier_2` forecasts.
- Kept normal market comparison behavior for benchmark-backed forecasts.
- Added a safe compact empty state when `marketProbability` is `null`, especially for freeform `tier_2` forecasts.

## 2. Files Changed
- `client/src/types/index.ts`: allowed `Prediction.marketProbability` to remain `null` safely.
- `client/src/App.tsx`: preserved `marketProbability: null` when session results do not include a benchmark.
- `client/src/components/Dashboard.tsx`: passed `tier` through to the market comparison card.
- `client/src/components/cards/MarketComparison.tsx`: handled `tier_2` and missing market benchmark with a safe empty state.

## 3. tier_1 Behavior
- `tier_1` forecasts continue to render the comparison chart normally when `marketProbability` is present.
- Existing benchmark messaging and chart layout remain unchanged for benchmark-backed sessions.

## 4. tier_2 Behavior
- `tier_2` forecasts now show a compact empty state when there is no market benchmark.
- Empty-state copy is:
  - `No market benchmark available for this freeform forecast.`
- The UI no longer implies a market gap or consensus comparison when the benchmark is missing.

## 5. Backward Compatibility
- Old sessions with no `tier` still render safely.
- Sessions with missing `marketProbability` now fall back to the empty state instead of trying to compare against a null benchmark.
- No probability storage or API behavior changed.

## 6. Validation Results
- `git status --short`
- `cd client && npx tsc -p tsconfig.app.json --noEmit --pretty false`: passed
- `cd client && npm run lint`: failed only because root `eslint.config.js` still cannot resolve `@eslint/js`

## 7. Risks/Notes
- Missing benchmark handling is still driven primarily by `marketProbability == null`, which is the safest backward-compatible signal for old sessions without an explicit `tier`.
- `tier_1` sessions with a missing benchmark will also show the generic no-benchmark state, which is preferable to rendering misleading comparison math.
