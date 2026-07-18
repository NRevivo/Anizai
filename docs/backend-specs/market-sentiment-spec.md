# Market & Sentiment — Frontend Data Contract
> Domain: C — Frontend / BFF
> Type: Spec (cross-domain contract)
> Last updated: 2026-07-18 — shapes re-verified against `client/src/services/session.service.ts`; no changes required.
> TL;DR: What the agent must send to activate the Market Comparison and Sentiment Analysis cards. Open this before populating `marketProbability`, `marketComparison`, or `sentimentTimeSeries`.

> **Status: live, not archived.** This is a forward-looking cross-team contract, not
> history. Related: `../C_frontend/frontend_contracts.md` §3.4, §3.7 and KG-C-6 in
> `../C_frontend/frontend_sprints.md §4`.

**Audience:** pipeline owner (Ron)
**Purpose:** exact shapes the Anizai dashboard expects for the Market Comparison
and Sentiment Analysis cards, so the pipeline can populate them without
guesswork.
**Status:** the agent currently emits empty/null for all fields below; both
cards render deliberate empty states. This document specifies what to send so
they activate.

This spec is **reverse-engineered from the frontend code** — it describes what
the UI actually reads today, not what it ideally should. Field names and types
below are the **API shape** returned by `GET /sessions/:id` (the Express BFF
response), which is what the frontend consumes. The BFF mediates between
Firestore and the client; this document does not cover Firestore document
layout — only the contract at the API boundary.

Source of truth for these shapes in the frontend:
- `client/src/services/session.service.ts` — `SessionResult`, `SessionDetail`,
  `SentimentDataPoint` (API types).
- `client/src/App.tsx` — `toPrediction`, `toSentimentPoints` (mapping into
  display types).
- `client/src/components/cards/MarketComparison.tsx`,
  `client/src/components/cards/SentimentAnalysis.tsx` — the rendering.

All probability/sentiment values in the frontend are **0.0–1.0 floats**. The UI
multiplies by 100 for display. Do not send percentages.

---

## Part A — Market Comparison card

The Market Comparison card is driven by **one scalar field**: `marketProbability`.
Two further fields tune its presentation: `marketComparisonInsight` and `tier`.

### A.1 `marketProbability`

#### Schema

```
result.marketProbability: number | null
```

| Property      | Value                                                          |
|---------------|----------------------------------------------------------------|
| Type          | `number` or `null`                                             |
| Range         | `0.0` to `1.0`                                                 |
| Represents    | Market-implied probability for the forecast question          |
| `null` means  | No comparable / canonical market exists for this forecast      |

Lives on the `SessionResult` object (`result` field of the
`GET /sessions/:id` response).

#### What the UI renders

- When `marketProbability` is a number: the card renders a horizontal bar
  chart with two bars — **"Anizai Forecast"** (the model's own probability)
  and **"Market Consensus"** (`marketProbability`). Both bars are scaled to a
  fixed 0–100 axis. Each bar is labelled with its percentage to one decimal.
- The card title becomes a computed comparison sentence — e.g. *"Anizai is
  4.2 points above the market benchmark"* — based on the signed difference
  between the model's probability and `marketProbability`. (This computed
  title is overridden if `marketComparisonInsight` is supplied; see A.2.)
- When `marketProbability` is `null`: the chart is replaced by a compact
  empty state ("No market benchmark").

The other bar, "Anizai Forecast", comes from the model's own
`finalProbability` — not from anything you send here.

#### Edge cases the frontend handles

| Input                          | Behavior                                                                                  |
|--------------------------------|-------------------------------------------------------------------------------------------|
| `null`                         | Handled. Card shows the empty state. **This is the correct "no market" signal.** |
| `undefined` / field omitted    | Handled — treated identically to `null` (the check is `!= null`).                         |
| A valid `0.0`–`1.0` number     | Handled. Bar renders.                                                                     |
| `0.0` exactly                  | Handled — renders a zero-length bar labelled `0.0%`. Note: `0` is a *valid* value and is **not** treated as "no market". Use `null` for "no market". |

**Where you must be careful — the frontend does NOT sanitize these:**

| Input            | Behavior                                                                                   |
|------------------|--------------------------------------------------------------------------------------------|
| `NaN`            | Not guarded. `NaN != null` is true, so the card treats it as "has data", then renders a bar with a `NaN%` label and no visible length. Do not send `NaN`. |
| Negative number  | Not guarded. Renders a negative bar; the 0–100 axis clips it. Do not send negatives.       |
| Number `> 1.0`   | Not guarded. Scales past 100%, overflows the fixed 0–100 axis, bar is visually clipped. Keep within `0.0`–`1.0`. |

Summary: the frontend reliably handles **`null` / omitted**. It does **not**
validate the numeric range or `NaN` — send a clean `0.0`–`1.0` float or `null`,
nothing else.

#### Anti-spec — what NOT to send

- Do not send a percentage (`0`–`100`). The UI multiplies by 100 itself.
- Do not use `0`, `-1`, or a sentinel number to mean "no market". The only
  "no market" signal the UI understands is `null` (or omitting the field).
- Do not send `NaN` or out-of-range values expecting the UI to clamp them — it
  does not.

### A.2 `marketComparisonInsight` (optional — used when present)

#### Schema

```
result.marketComparisonInsight: string | null
```

| Property     | Value                                                       |
|--------------|-------------------------------------------------------------|
| Type         | `string` or `null`                                          |
| Represents   | A short headline describing the model-vs-market comparison  |
| `null` means | Frontend falls back to a computed comparison sentence       |

#### What the UI renders

- When a non-empty string: it is used **verbatim as the card's title**
  (whitespace is trimmed; an all-whitespace string is treated as empty).
- When `null` / empty: the frontend computes its own title — either the
  "Anizai is N points above/below the market benchmark" sentence (when
  `marketProbability` is present) or "No market benchmark available" (when it
  is not).

This field is purely a title override. It does not affect the chart. It is
safe to leave `null` — the computed fallback is always sensible.

### A.3 `tier` (optional — minor effect)

#### Schema

```
result.tier: 'tier_1' | 'tier_2' | null
```

#### What the UI renders

`tier` affects only the **wording of the empty state** when
`marketProbability` is `null`:
- `tier_2` → "No market benchmark available for this freeform forecast."
- `tier_1` / `null` → "Comparable market probability data is not available for
  this forecast."

It does not control whether the chart appears, and has no effect when
`marketProbability` is present. (`tier` is a general session field, not
market-specific — listed here only because this card reads it.)

### A.4 `marketComparison` — defined but NOT consumed

#### Schema (as currently typed)

```
result.marketComparison: { source: string; value: number }[]
```

#### Status: unconsumed by the UI

This array exists in the frontend's `SessionResult` type, but **no component
reads it**. `App.tsx::toPrediction` does not map it into the display model,
and `MarketComparison.tsx` has no prop for it. The Market Comparison card is
driven **entirely by the scalar `marketProbability`** (plus `anizaiProbability`,
`marketComparisonInsight`, `tier`).

**Implication for the pipeline:** populating `marketComparison` today produces
**zero visual change**. If the intent is to light up the Market card, send
`marketProbability`. Sending the `marketComparison` array is harmless but
inert — it would require new frontend work (a type mapping + component change)
before it renders anything. Treat it as a not-yet-wired field, not part of the
active contract.

---

## Part B — Sentiment Analysis card

The Sentiment Analysis card is driven by a **time series of points**:
`sentimentTimeSeries`.

### B.1 `sentimentTimeSeries`

#### Schema

`sentimentTimeSeries` is a top-level array on the `GET /sessions/:id` response
(a sibling of `result`, not nested inside it). Each element:

```
sentimentTimeSeries: SentimentDataPoint[]

SentimentDataPoint {
  id:              string             // not rendered by this card (see B.4)
  ts:              string             // ISO-8601 timestamp; used as date fallback
  date:            string             // pre-formatted X-axis label
  expertSentiment: number             // 0.0–1.0 — REQUIRED
  publicSentiment: number             // 0.0–1.0 — REQUIRED
  expertUpper:     number | null      // optional confidence band — see B.3
  expertLower:     number | null      // optional confidence band — see B.3
  createdAt:       string             // not rendered by this card (see B.4)
}
```

| Field             | Type             | Required | Notes                                                        |
|-------------------|------------------|----------|--------------------------------------------------------------|
| `expertSentiment` | `number` 0.0–1.0 | Yes      | Expert sentiment at this point.                              |
| `publicSentiment` | `number` 0.0–1.0 | Yes      | Public sentiment at this point.                              |
| `date`            | `string`         | Yes\*    | Pre-formatted X-axis label, e.g. `"May 18"`.                 |
| `ts`              | `string`         | Yes\*    | ISO-8601. Used to derive the label if `date` is absent.      |
| `expertUpper`     | `number` \| null | No       | Upper confidence bound. See B.3 — accepted but not rendered. |
| `expertLower`     | `number` \| null | No       | Lower confidence bound. See B.3 — accepted but not rendered. |
| `id`              | `string`         | No       | Carried in the type; not read by this card.                  |
| `createdAt`       | `string`         | No       | Carried in the type; not read by this card.                  |

\* **At least one of `date` or `ts` must be present and valid.** The frontend
uses `date` if truthy, otherwise formats `ts` into a label. If both are
missing/empty the X-axis label becomes the literal string `"Invalid Date"`
(no crash, but wrong).

`sentimentTimeSeries` **must always be an array** — `[]` is valid, `null` /
omitted is not (see B.2).

#### What the UI renders

- When the array is non-empty: an **area chart** with two series — **Expert
  Sentiment** (`expertSentiment`) and **Public Sentiment** (`publicSentiment`),
  each scaled to a fixed 0–100 Y-axis. The X-axis uses `date` (or the label
  derived from `ts`).
- A footer shows the **latest point's** values — i.e. the *last element of the
  array* — as two large percentages ("Expert" and "Public"). The array is
  consumed in the order received; the last element is treated as most recent.
  **Order your points oldest → newest.**
- When the array is empty: the chart is replaced by a compact empty state
  ("No sentiment data").

#### Edge cases the frontend handles

| Input                                   | Behavior                                                                                       |
|------------------------------------------|------------------------------------------------------------------------------------------------|
| `[]` (empty array)                       | Handled. Card shows the empty state.                                                           |
| Array with **1** point                   | Handled — renders, but an area chart of one point is a single dot with no line. Send **≥ 2** points for a meaningful chart. |
| Array with **2+** points                 | Handled. Proper area chart. This is the intended case.                                         |
| `expertSentiment` / `publicSentiment` out of range (`<0`, `>1`) | Not guarded. Scaled past the fixed 0–100 Y-axis and clipped. Keep within `0.0`–`1.0`. |
| `expertSentiment` / `publicSentiment` = `NaN` | Not guarded. That series point goes missing; the footer shows `NaN%`. Do not send `NaN`. |
| A point missing `expertSentiment` / `publicSentiment` | Not guarded. Same as `NaN` above — missing chart point, `NaN%` footer. Treat both as required. |
| `date` missing but `ts` valid             | Handled. Label derived from `ts`.                                                              |
| Both `date` and `ts` missing/invalid      | Not guarded. X-axis label becomes `"Invalid Date"`. Always send a valid `date` or `ts`.        |

**Critical:** `sentimentTimeSeries` itself must be an **array**. The frontend
mapper calls `.map()` on it directly. If the API returns `null`, `undefined`,
or omits the field while a session detail object exists, the dashboard
**throws and the view breaks**. An empty subcollection must surface as `[]`,
never as a missing field.

Summary of what the frontend reliably handles: **empty array**, **1 vs many
points**, **`date` absent when `ts` present**. What it does **not** validate:
numeric range, `NaN`, missing required per-point fields, or a non-array
top-level value.

### B.3 `expertUpper` / `expertLower` — accepted, plumbed, but NOT yet rendered

These optional fields were flagged in the Slice 2.5 audit as future confidence
bands. Current reality, precisely:

- They exist on the API `SentimentDataPoint` type.
- `App.tsx::toSentimentPoints` **does** read them and carries them into the
  display model (`null` is normalized to `undefined`).
- **`SentimentAnalysis.tsx` never reads them.** There is no band/range series
  in the area chart.

**Implication for the pipeline:** populating `expertUpper` / `expertLower`
today is **safe but inert** — the data flows as far as the display model and
then stops, because the chart has no band rendering. They will *not* "light up
automatically." Rendering a confidence band is a future frontend change. You
may populate them now (the contract accepts them: `number | null`, expected
range `0.0`–`1.0`, with `expertLower ≤ expertSentiment ≤ expertUpper`), but
expect no visual effect until the chart is updated.

### B.4 `id` / `createdAt` — carried but not used by this card

`SentimentDataPoint` includes `id` and `createdAt`. Neither is read by the
Sentiment Analysis card (`toSentimentPoints` ignores them). Populate them or
not as makes sense for the wider data model — they have no effect on this card.

### B.5 Anti-spec — what NOT to send

- Do not send sentiment as percentages (`0`–`100`). Send `0.0`–`1.0` floats;
  the UI scales to 100.
- Do not send `sentimentTimeSeries` as `null` or omit it — send `[]` for "no
  data". A missing array breaks the dashboard.
- Do not nest or group the points (e.g. by source, by series). The frontend
  expects a **flat array of time-ordered points**, each carrying *both*
  `expertSentiment` and `publicSentiment`. The two chart series are split out
  client-side from each point — do not send separate arrays per series.
- Do not rely on insertion/ID order for recency — order the array explicitly
  **oldest → newest**. The footer reads the **last element** as "latest".
- Do not send a single point and expect a line/area — send ≥ 2.

### B.6 `sentimentAnalysisInsight` (optional — used when present)

#### Schema

```
result.sentimentAnalysisInsight: string | null
```

A short headline string. When non-empty it replaces the card's default
sub-description ("Expert and public sentiment as supporting context"). When
`null`/empty the default is used. Lives on `SessionResult` (`result`), not on
the time-series points. Purely cosmetic; safe to leave `null`.

---

## Quick reference

| Field                          | Where        | Type                              | Drives                                   | Frontend-ready? |
|--------------------------------|--------------|-----------------------------------|------------------------------------------|-----------------|
| `marketProbability`            | `result`     | `number \| null` (0.0–1.0)        | Market card bar chart vs. empty state    | Yes             |
| `marketComparisonInsight`      | `result`     | `string \| null`                  | Market card title override               | Yes             |
| `tier`                         | `result`     | `'tier_1' \| 'tier_2' \| null`     | Market empty-state wording only          | Yes             |
| `marketComparison`             | `result`     | `{source,value}[]`                | Nothing — unconsumed                      | No (not wired)  |
| `sentimentTimeSeries`          | top-level    | `SentimentDataPoint[]`            | Sentiment card area chart vs. empty state | Yes             |
| `sentimentTimeSeries[].expertUpper/Lower` | per-point | `number \| null` (0.0–1.0) | Nothing yet — accepted, not rendered      | No (not wired)  |
| `sentimentAnalysisInsight`     | `result`     | `string \| null`                  | Sentiment card sub-description override   | Yes             |

**To activate the Market card:** send `marketProbability` as a `0.0`–`1.0`
float (or `null` for genuinely no market).
**To activate the Sentiment card:** send `sentimentTimeSeries` as a flat,
oldest→newest array of ≥ 2 points, each with `expertSentiment`,
`publicSentiment`, and a valid `date` or `ts`.
