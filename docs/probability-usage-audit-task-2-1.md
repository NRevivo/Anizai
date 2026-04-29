# Probability Usage Audit - Task 2.1

## 1. Summary

The current product mostly uses `0-1` floats for live session, result, market, trending, and sentiment data coming from the backend. Display formatting is usually handled in the frontend by multiplying by `100` at render time.

There are still a few mixed or risky areas:

- Some demo and legacy UI code still uses `0-100` percentage values.
- Frontend sentiment display currently renders raw `0-1` values with a `%` sign in one card, which is visually inconsistent with the rest of the UI.
- The demo fallback in `client/src/services/trending.service.ts` divides already-normalized mock session probabilities by `100`, which would produce incorrect `0-0.01` values on that path.
- There is no `normalizePercent` function in the active code anymore.

## 2. Probability/Confidence Usage Table

| File | Field or function | Current unit if known | Usage type | Notes |
| --- | --- | --- | --- | --- |
| `client/src/lib/utils.ts` | `formatProbability(probability)` | `0-1` input, `%` output | display formatting | Main shared formatter for live probability UI |
| `client/src/App.tsx` | `toSidebarSession()` | `0-1` | transform | Passes `latestProbability` straight through to sidebar view model |
| `client/src/App.tsx` | `toPrediction()` | `0-1` | transform | Uses `result.finalProbability`, `session.latestProbability`, `result.confidence`, `session.latestConfidence`, `result.marketProbability` |
| `client/src/App.tsx` | `mapSessionStatus()` confidence threshold | `0-1` | guessing / derived UI logic | Treats confidence `< 0.4` as `volatile` |
| `client/src/App.tsx` | `toSentimentPoints()` | `0-1` | transform | Passes sentiment values through from backend session detail |
| `client/src/App.tsx` | `toTrendingView()` | `0-1` | transform / derived UI logic | Defaults missing probability to `0.5`; trend thresholds at `0.6` and `0.4` |
| `client/src/services/session.service.ts` | `SessionListItem.latestProbability` | `0-1` | contract | Frontend session contract |
| `client/src/services/session.service.ts` | `SessionListItem.latestConfidence` | `0-1` | contract | Frontend session contract |
| `client/src/services/session.service.ts` | `SessionResult.finalProbability` | `0-1` | contract | Frontend result contract |
| `client/src/services/session.service.ts` | `SessionResult.confidence` | `0-1` | contract | Frontend result contract |
| `client/src/services/session.service.ts` | `SessionResult.marketProbability` | `0-1` | contract | Frontend result contract |
| `client/src/services/session.service.ts` | `PredictionPoint.probability`, `PredictionPoint.confidence` | `0-1` | contract | Frontend detail contract |
| `client/src/services/session.service.ts` | `SentimentDataPoint.*Sentiment` | `0-1` | contract | Frontend detail contract |
| `client/src/services/session.service.ts` | `demoSessions.latestProbability` | derived from mock | demo transform | Uses `session.probability / 100`; currently assumes mock sessions are `0-100`, but current mock data is already `0-1` |
| `client/src/services/session.service.ts` | `buildDemoSessionDetail()` probability fallback | mixed | demo transform | Falls back to `mockCurrentPrediction.probability / 100`; current mock is already `0-1` |
| `client/src/services/session.service.ts` | demo `marketProbability` | mixed | demo transform | Uses `mockCurrentPrediction.marketProbability / 100`; current mock is already `0-1` |
| `client/src/services/session.service.ts` | demo sentiment conversion | mixed | demo transform | Divides mock sentiment values by `100`, but current mock values are already `0-1` |
| `client/src/services/session.service.ts` | `createSession()` demo fallback | `0-1` | mock data | Uses `latestProbability: 0.52`, `latestConfidence: 0.72` |
| `client/src/services/trending.service.ts` | `TrendingForecast.probability` | intended `0-1` | contract | Frontend trending contract |
| `client/src/services/trending.service.ts` | demo fallback `probability: session.probability / 100` | incorrect if mock is `0-1` | demo transform | Likely stale conversion |
| `client/src/data/mockData.ts` | `mockSessions[].probability` | `0-1` | mock data | 0.723, 0.458, 0.124, 0.389 |
| `client/src/data/mockData.ts` | `mockCurrentPrediction.probability`, `confidenceIndex`, `marketProbability` | `0-1` | mock data | Matches current app contract |
| `client/src/data/mockData.ts` | `mockSentimentData.*` | `0-1` | mock data | 0.65, 0.42, etc. |
| `client/src/components/Sidebar.tsx` | `formatProbability(session.probability)` | `0-1` input | display formatting | Session list display |
| `client/src/components/CreateForecastContext.tsx` | `(item.probability * 100).toFixed(0)` | `0-1` input | display formatting | Trending/reference panel display |
| `client/src/components/cards/PredictionOverview.tsx` | `probability`, `confidenceIndex` props | `0-1` | display / derived UI logic | Uses `formatProbability`, confidence label thresholds, `confidenceScore = Math.round(confidenceIndex * 100)` |
| `client/src/components/cards/MarketComparison.tsx` | `anizaiProbability`, `marketProbability` props | `0-1` input, `0-100` chart | display transform | Converts both to percentage for chart and comparison copy |
| `client/src/components/cards/SentimentAnalysis.tsx` | `data` prop | appears `0-1` data, `0-100` display axis | display mismatch | Tooltip and footer append `%` directly to raw values; Y-axis domain is `[0, 100]` |
| `client/src/components/TrendingForecasts.tsx` | `forecast.probability` | `0-100` | unused legacy/mock UI | Static values 72, 46, 12, 39; appears not used by current app shell |
| `client/src/types/index.ts` | `Prediction.probability`, `confidenceIndex` | comments say `0-1` | contract | Core frontend UI type |
| `client/src/types/index.ts` | `SentimentDataPoint.*` | comments say `-1 to 1` | contract comment | Comment conflicts with actual mock/backend usage, which is `0-1` |
| `server/src/services/sessions.service.ts` | `Session.latestProbability`, `latestConfidence` | `0-1` | contract | Backend session contract |
| `server/src/services/sessions.service.ts` | `SessionResult.finalProbability`, `confidence`, `marketProbability` | `0-1` | contract | Backend result contract |
| `server/src/services/sessions.service.ts` | `PredictionPoint.probability`, `confidence` | `0-1` | contract | Backend detail contract |
| `server/src/services/sessions.service.ts` | `SentimentDataPoint.*Sentiment` | `0-1` | contract | Backend sentiment contract |
| `server/src/repositories/session.repository.ts` | session/result/detail mapping | passthrough | storage/read | Reads probability and confidence values from Firestore without conversion |
| `server/src/repositories/trending.repository.ts` | Polymarket `outcomePrices[0]` parse | `0-1` | backend transform | Defaults missing probability to `0.5` |
| `server/scripts/seed.ts` | `sessions.latestProbability`, `latestConfidence` | `0-1` | seed data | 0.72, 0.45, 0.68, etc. |
| `server/scripts/seed.ts` | seeded `predictionSeries` | `0-1` | seed data | Writes probability/confidence floats |
| `server/scripts/seed.ts` | seeded `sessionResults.finalProbability`, `confidence`, `marketComparison[].value` | `0-1` | seed data | Result docs use normalized floats |
| `server/scripts/seed.ts` | seeded sentiment subcollection | `0-1` | seed data | 0.60, 0.65, 0.70, 0.75 |
| `server/scripts/seed.ts` | assistant message / markdown summary text | `%` text from `0-1` values | display seed content | Converts via `Math.round(value * 100)` in strings |
| `server/scripts/test-session-result.ts` | `latestProbability`, `latestConfidence`, `finalProbability`, `confidence`, `marketComparison`, `marketProbability` | `0-1` | test fixture | Uses 0.723, 0.84, 0.685 |
| `client/src/components/landing/UIShowcase.tsx` | hardcoded `72.3%`, `+2.4% today` | `0-100` text | marketing/demo display | Presentational only, not wired to real data |
| `client/src/components/landing/Hero.tsx` | hardcoded `92%` | `0-100` text | marketing/demo display | Presentational only |

## 3. Places Using 0-1 Values

Live app and backend contracts use `0-1` in these places:

- `client/src/App.tsx`
- `client/src/lib/utils.ts`
- `client/src/services/session.service.ts` interfaces and API wrappers
- `client/src/types/index.ts` for `Prediction.probability` and `confidenceIndex`
- `client/src/data/mockData.ts`
- `client/src/components/cards/PredictionOverview.tsx`
- `client/src/components/cards/MarketComparison.tsx` inputs before chart conversion
- `client/src/components/CreateForecastContext.tsx`
- `server/src/services/sessions.service.ts`
- `server/src/repositories/session.repository.ts`
- `server/src/repositories/trending.repository.ts`
- `server/scripts/seed.ts`
- `server/scripts/test-session-result.ts`

## 4. Places Using 0-100 Values

Current `0-100` or percentage-oriented surfaces:

- `client/src/components/TrendingForecasts.tsx`
  - static `72`, `46`, `12`, `39`
  - appears legacy/unused
- `client/src/components/landing/UIShowcase.tsx`
  - hardcoded marketing percentages like `72.3%`
- `client/src/components/landing/Hero.tsx`
  - hardcoded `92%`
- `client/src/components/cards/MarketComparison.tsx`
  - chart layer converts `0-1` inputs into `0-100` display values
- `client/src/components/cards/PredictionOverview.tsx`
  - confidence is displayed as `/100`
- `server/scripts/seed.ts`
  - message and markdown text convert normalized values into `%` strings

## 5. Places Using `normalizePercent` or Guessing

`normalizePercent`:

- No live `normalizePercent` implementation or usage was found in `client/src`, `server/src`, `server/scripts`, or `server/tests`.

Guessing / heuristic interpretation:

- `client/src/App.tsx`
  - confidence `< 0.4` is treated as `volatile`
  - trending thresholds use `>= 0.6` and `<= 0.4`
- `client/src/components/cards/PredictionOverview.tsx`
  - probability label thresholds:
    - `>= 0.66` => `Likely`
    - `<= 0.34` => `Unlikely`
  - confidence label thresholds:
    - `>= 0.75` => `High confidence`
    - `>= 0.5` => `Moderate confidence`
    - `> 0` => `Low confidence`
- `client/src/components/cards/SentimentAnalysis.tsx`
  - appears to assume the incoming values can be shown directly with a `%` sign even though current data is `0-1`

## 6. Seed/Mock/Test Data That Needs Updates

Likely stale or risky data sources:

- `client/src/services/session.service.ts`
  - demo conversion logic still divides mock probabilities and sentiment by `100`
  - current `mockData.ts` is already normalized to `0-1`
- `client/src/services/trending.service.ts`
  - demo fallback divides `mockSessions[].probability` by `100`
  - current `mockSessions` already hold `0-1`
- `client/src/components/TrendingForecasts.tsx`
  - static percentage data uses `0-100` values and is inconsistent with the live app contract
- `client/src/types/index.ts`
  - sentiment comments say `-1 to 1` while current mock/backend sentiment is `0-1`

Seed/test data that is already aligned with `0-1`:

- `server/scripts/seed.ts`
- `server/scripts/test-session-result.ts`
- `client/src/data/mockData.ts`

## 7. UI Display Formatting Locations

Main UI display formatting locations:

- `client/src/lib/utils.ts`
  - `formatProbability(probability)` => `0-1` to `"74.3%"`
- `client/src/components/Sidebar.tsx`
  - session probability display via `formatProbability`
- `client/src/components/cards/PredictionOverview.tsx`
  - main result percentage display via `formatProbability`
  - confidence displayed as `Math.round(confidenceIndex * 100)/100`
- `client/src/components/cards/MarketComparison.tsx`
  - chart bars and labels use converted percentage values
- `client/src/components/CreateForecastContext.tsx`
  - trending probability shown as `(probability * 100).toFixed(0)%`
- `client/src/components/cards/SentimentAnalysis.tsx`
  - tooltip and footer append `%` directly to sentiment values
  - current values appear unscaled
- `server/scripts/seed.ts`
  - seeded assistant copy and markdown summary render `%` strings from normalized values

## 8. Risks Before Changing

- The active product path is mostly standardized on `0-1`, but there are still legacy `0-100` assumptions in demo and unused UI code.
- Fixing unit inconsistencies in one layer without updating demo fallbacks could make local/dev behavior diverge from production behavior.
- `client/src/components/cards/SentimentAnalysis.tsx` is a high-risk display surface because it appears to mix normalized `0-1` data with a `0-100` chart/display convention.
- `client/src/services/session.service.ts` and `client/src/services/trending.service.ts` contain stale `/ 100` conversions that can silently distort demo-mode output.
- `client/src/types/index.ts` sentiment comments do not match current data behavior, which increases the chance of future incorrect conversions.
- Marketing/demo components such as `TrendingForecasts.tsx`, `UIShowcase.tsx`, and `Hero.tsx` use percentage language and values but are not authoritative product data sources; they should not be used as a normalization reference.
