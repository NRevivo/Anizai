# Slice 2.5 — Backend Payload Audit & Display Verification

<!-- archive-banner -->
> ⚠️ **SUPERSEDED — contains inaccuracies.** Historical record only; do not
> cite as current. Corrected content: [`frontend_contracts.md`](../C_frontend/frontend_contracts.md) §3–§6.
> Why this doc is wrong: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §2.

Audit of every field reachable from `GET /sessions/:id` (`status === 'done'`)
and where it lands in the frontend. Read-only; no code changed.

Trace path: `session.repository.ts` → `sessions.service.ts` (`SessionDetail`)
→ `client/src/services/session.service.ts` (`SessionDetail` mirror) →
`App.tsx` mappers (`toPrediction`, `toTimelineEvents`, `toSentimentPoints`,
`toChatMessages`, `toSidebarSession`) → components.

`agentEvents` is **not** in the REST payload — the frontend reads it from a
direct Firestore listener (`subscribeToAgentEvents`). Included below because
the brief asks for everything on a session-detail response.

---

## A. Inventory — every backend field

### `SessionDetail.session` (always present)

| Field path | Type | Always present? | Where it ends up in UI |
|---|---|---|---|
| `session.id` | string | yes | `toPrediction`→`Prediction.id`; `toSidebarSession`→`PredictionSession.id` |
| `session.userId` | string | yes | Not mapped — dropped |
| `session.question` | string | yes | `Prediction.question` → Dashboard header `<h1>`, `extractDeadline`, thesis fallback |
| `session.title` | string\|null | yes | `toSidebarSession` (`title ?? question`) → Sidebar item label |
| `session.status` | enum | yes | `Prediction.status`, `toActiveSessionState`, `toSidebarSession` → Sidebar status dot |
| `session.latestProbability` | number\|null | yes | `toSidebarSession`; `toPrediction` fallback when no `result` |
| `session.latestConfidence` | number\|null | yes | `toPrediction` confidence fallback |
| `session.followEnabled` | boolean | yes | Not mapped — dropped |
| `session.isFollowing` | boolean | yes | Not mapped — dropped |
| `session.canonicalKey` | string\|null | yes | Not mapped — dropped |
| `session.errorCode` | string\|null | yes | Not mapped — dropped (only `errorMessage` is used) |
| `session.errorMessage` | string\|null | yes | `Prediction.errorMessage`, `toActiveSessionState` → failed-state UI |
| `session.clarificationCandidates` | `ClarificationCandidate[]`\|null | yes | `Prediction` + `toActiveSessionState` → clarification flow |
| `session.createdAt` | string | yes | `Prediction.createdAt` (Date) |
| `session.updatedAt` | string | yes | `Prediction.updatedAt` (Date) |
| `session.lastActivityAt` | string | yes | `toSidebarSession.lastUpdated` → Sidebar relative time |

### `SessionDetail.result` (`SessionResult`, null until `status === 'done'`)

| Field path | Type | Always present? | Where it ends up in UI |
|---|---|---|---|
| `result.sessionId` | string | when result exists | Not mapped — dropped |
| `result.userId` | string | when result exists | Not mapped — dropped |
| `result.finalProbability` | number (0–1) | yes | `Prediction.probability` → `ProbabilityRing`, `MarketComparison`, `deriveVerdict` |
| `result.confidence` | number (0–1) | yes | `Prediction.confidenceIndex` → `MetricsRow` confidence tile, `deriveVerdict` |
| `result.marketComparison` | `{source,value}[]` | yes (defaults `[]`) | **Not mapped — dropped** (see Gaps) |
| `result.summaryMarkdown` | string | yes | `Prediction.summaryMarkdown` → `MarkdownSummary` in `index.tsx`; thesis fallback |
| `result.createdAt` | string | yes | Not mapped — dropped |
| `result.updatedAt` | string | yes | Not mapped — dropped |
| `result.confidenceLabel` | enum\|null | yes | `Prediction.confidenceLabel` → `MetricsRow` confidence tile |
| `result.consensusStrength` | enum\|null | yes | `Prediction.consensusStrength` → `MetricsRow` consensus tile |
| `result.evidenceVolumeLabel` | `High\|Medium\|Low`\|null | yes | **Not mapped — dropped** (see Gaps) |
| `result.bottomLineAnswer` | string\|null | yes | `Prediction.bottomLineAnswer` → `VerdictBanner` thesis |
| `result.detailedExplanation` | string\|null | yes | `Prediction.detailedExplanation` → thesis fallback only; also feeds deprecated `explanation` |
| `result.marketProbability` | number\|null | yes | `Prediction.marketProbability` → `MarketComparison` |
| `result.marketComparisonInsight` | string\|null | yes | **Not mapped — dropped** (see Gaps) |
| `result.sentimentAnalysisInsight` | string\|null | yes | **Not mapped — dropped** (see Gaps) |
| `result.evidenceFeedSummary` | string\|null | yes | **Not mapped — dropped** (see Gaps) |
| `result.keyFactors` | `KeyFactor[]` | yes (defaults `[]`) | `Prediction.keyFactors` → `DriversAndHeadwinds` |
| `result.keyFactors[].label` | string | — | Factor row title |
| `result.keyFactors[].description` | string | — | Factor row description |
| `result.keyFactors[].direction` | `increases\|decreases` | — | Splits factors into Drivers / Headwinds columns |
| `result.keyFactors[].weight` | number (0–1) | — | Factor weight bar; column sort |
| `result.keyFactors[].evidence_ids` | string[] | — | Only `.length` used (source-count text); IDs never linked |
| `result.whatIDidntFind` | string[] | yes (defaults `[]`) | `Prediction.whatIDidntFind` → `GapsNotice` |
| `result.reasoningChain` | `ReasoningStep[]` | yes (defaults `[]`) | `Prediction.reasoningChain` → `ReasoningChain` |
| `result.reasoningChain[].step` | number | — | Step number / sort |
| `result.reasoningChain[].title` | string | — | Step title |
| `result.reasoningChain[].description` | string | — | Step description |
| `result.suggestedActions` | `SuggestedAction[]` | yes (defaults `[]`) | `Prediction.suggestedActions` → `ChatPanel` chips (backend always `[]` — see Phantoms) |
| `result.suggestedActions[].id/label/prompt` | string | — | Chip id / label / prompt |
| `result.generatedAt` | string\|null | yes | `Prediction.generatedAt` (Date) — **mapped, never rendered** (see Gaps) |
| `result.agentVersion` | string\|null | yes | `Prediction.agentVersion` — **mapped, never rendered** (see Gaps) |
| `result.tier` | `tier_1\|tier_2`\|null | yes | `Prediction.tier` → `MarketComparison` (freeform-tier empty copy) |

### `SessionDetail.evidence` (`Evidence[]`, subcollection)

| Field path | Type | Always present? | Where it ends up in UI |
|---|---|---|---|
| `evidence[].id` | string | yes | `TimelineEvent.id` → React key in `EvidenceTimeline` |
| `evidence[].type` | `news\|social\|expert\|market` | yes | Used in `mapEvidenceSourceType` fallback only |
| `evidence[].evidenceId` | string\|null | yes | `TimelineEvent.evidenceId` — **mapped, never rendered** |
| `evidence[].sourceType` | string\|null | yes | Drives `mapEvidenceSourceType` → `TimelineEvent.sourceType` (filter tabs, source chip) |
| `evidence[].origin` | string\|null | yes | `TimelineEvent.origin` — **mapped, never rendered** |
| `evidence[].title` | string | yes | Evidence row `<h4>` |
| `evidence[].snippet` | string | yes | Evidence row snippet (+ `description`) |
| `evidence[].url` | string\|null | yes | `TimelineEvent.url` — **mapped, never rendered** (title has `cursor-pointer` but no link) |
| `evidence[].source` | string\|null | yes | Evidence row source label |
| `evidence[].sourceDomain` | string\|null | yes | Evidence row domain label; key-evidence domain |
| `evidence[].publishedAt` | string\|null | yes | `date` + `timestamp` on `TimelineEvent` → row date |
| `evidence[].fetchedAt` | string\|null | yes | `TimelineEvent.fetchedAt` — **mapped, never rendered** |
| `evidence[].sourceId` | string\|null | yes | Fallback for `source` only |
| `evidence[].score` | number | yes | **Not mapped — dropped** |
| `evidence[].relevanceScore` | number\|null | yes | Evidence row "Relevance n%" chip |
| `evidence[].credibilityTier` | string\|null | yes | Evidence row "Credibility …" chip |
| `evidence[].recencyWeight` | number\|null | yes | `TimelineEvent.recencyWeight` — **mapped, never rendered** |
| `evidence[].usedInAnswer` | boolean\|null | yes | `TimelineEvent.usedInAnswer` — **mapped, never rendered** |
| `evidence[].impactOnForecast` | string\|null | yes | `TimelineEvent.impactOnForecast` — **mapped, never rendered** (see Drift) |
| `evidence[].justification` | string\|null | yes | Evidence row justification text |
| `evidence[].rank` | number\|null | yes | `TimelineEvent.rank` — **mapped, never rendered** |
| `evidence[].createdAt` | string | yes | Date fallback when `publishedAt` null |
| `evidence[].impact` | `positive\|negative\|neutral`\|null | yes | Evidence row dot color + impact label |
| `evidence[].impactLabel` | string\|null | yes | Evidence row impact label; key-evidence label |
| `evidence[].isKeyEvidence` | boolean | yes | "Key evidence" section filter |

### `SessionDetail.predictionSeries` (`PredictionPoint[]`, subcollection)

| Field path | Type | Always present? | Where it ends up in UI |
|---|---|---|---|
| `predictionSeries` | `PredictionPoint[]` | yes (often `[]`) | **Entire array never read by any mapper — dropped** |
| `predictionSeries[].id/ts/probability/confidence/reasonType/evidenceIds` | mixed | — | Dropped |

### `SessionDetail.sentimentTimeSeries` (`SentimentDataPoint[]`, subcollection)

| Field path | Type | Always present? | Where it ends up in UI |
|---|---|---|---|
| `sentimentTimeSeries[].id` | string | yes | Not mapped — dropped |
| `sentimentTimeSeries[].ts` | string | yes | Fallback to derive `date` |
| `sentimentTimeSeries[].date` | string | yes | `SentimentDataPoint.date` → `SentimentAnalysis` X-axis |
| `sentimentTimeSeries[].expertSentiment` | number | yes | `SentimentAnalysis` expert area + footer stat |
| `sentimentTimeSeries[].expertUpper` | number\|null | yes | `SentimentDataPoint.expertUpper` — **mapped, never rendered** (no confidence band drawn) |
| `sentimentTimeSeries[].expertLower` | number\|null | yes | `SentimentDataPoint.expertLower` — **mapped, never rendered** |
| `sentimentTimeSeries[].publicSentiment` | number | yes | `SentimentAnalysis` public area + footer stat |
| `sentimentTimeSeries[].createdAt` | string | yes | Not mapped — dropped |

### `agentEvents` (Firestore listener, not in REST payload)

| Field path | Type | Always present? | Where it ends up in UI |
|---|---|---|---|
| `agentEvents[].eventId` | string | yes | React key |
| `agentEvents[].sessionId` | string | yes | Not rendered |
| `agentEvents[].sequence` | number | yes | Firestore `orderBy` only — not rendered |
| `agentEvents[].timestamp` | Date | yes | Agent row time |
| `agentEvents[].parentMessageId` | string\|null | yes | "Follow-up" badge + row tint |
| `agentEvents[].type` | enum | yes | Agent row type label |
| `agentEvents[].title` | string | yes | Agent row title |
| `agentEvents[].description` | string\|null | yes | Agent row description |
| `agentEvents[].status` | enum | yes | Agent row status dot + badge |
| `agentEvents[].durationMs` | number\|null | yes | Agent row duration |
| `agentEvents[].payload` | object\|null | yes | **Mapped, never rendered** |

### `messages` (`SessionMessage[]`, REST + Firestore listener)

| Field path | Type | Always present? | Where it ends up in UI |
|---|---|---|---|
| `messages[].id` | string | yes | `ChatMessage.id` |
| `messages[].role` | `user\|assistant\|system` | yes | `ChatMessage.role` (`system`→`assistant`) |
| `messages[].content` | string | yes | Chat bubble text |
| `messages[].createdAt` | string | yes | `ChatMessage.timestamp` |
| `messages[].status` | `sent\|failed`\|null | yes | `ChatMessage.status` |
| `messages[].userId` | string\|null | optional | Not mapped — dropped |
| `messages[].meta` | `{model,tokensIn,tokensOut,runId}`\|null | optional | **Not mapped — dropped** |

---

## B. Gaps — backend fields not shown

| Field | What the backend intends | Should it be shown? |
|---|---|---|
| `result.marketComparisonInsight` | Short headline for the Market card (CLAUDE.md). | Yes — `MarketComparison` currently templates its own title; this is the agent's intended copy. |
| `result.sentimentAnalysisInsight` | Short headline for the Sentiment card. | Yes — `SentimentAnalysis` shows a generic static line instead. |
| `result.evidenceFeedSummary` | Short headline for the Evidence section. | Yes — `EvidenceTimeline` shows a hardcoded "Sources and signals…" line instead. |
| `result.evidenceVolumeLabel` | `High/Medium/Low` qualitative evidence-volume rating. | Maybe — pairs naturally with confidence/consensus; designer's call. |
| `result.marketComparison` (`{source,value}[]`) | Per-source market probabilities for comparison. | Maybe — richer than the single `marketProbability`; only relevant once market data is wired. |
| `result.generatedAt` | When the forecast was produced. | Maybe — a "generated 2h ago" timestamp; mapped to `Prediction` but no component reads it. |
| `result.agentVersion` | Which agent build produced the result. | Probably not (debug/provenance) — but it is silently mapped then dropped. |
| `evidence[].usedInAnswer` | Whether synthesis actually used this item (CLAUDE.md "forecast-specific" tier). | Yes — distinguishes evidence that shaped the answer from evidence merely retrieved. |
| `evidence[].impactOnForecast` | Agent's positive/negative/neutral impact rating. | Yes — see Drift; the UI uses a different field for this. |
| `evidence[].recencyWeight` | Objective recency score (CLAUDE.md "objective" tier). | Maybe — completes the relevance/credibility/recency triad already partly shown. |
| `evidence[].rank` | Agent's ordering rank for evidence. | Maybe — could drive sort/emphasis. |
| `evidence[].score` | Raw evidence score. | Probably not — superseded by `relevanceScore`; dropped at the mapper entirely. |
| `evidence[].evidenceId` | Stable evidence identifier. | Yes (indirectly) — it is the link target for `keyFactors[].evidence_ids`; needed to wire factor→evidence. |
| `evidence[].url` | Source link. | Yes — evidence titles have `cursor-pointer` styling but no link (CLAUDE.md known problem #12). |
| `sentimentTimeSeries[].expertUpper/expertLower` | Expert-sentiment confidence band. | Maybe — `SentimentAnalysis` draws only the center line, not the band. |
| `agentEvents[].payload` | Per-event structured detail. | Maybe — could power an event detail expansion. |
| `messages[].meta` | Model + token usage per message. | Probably not (telemetry) — but silently dropped. |

---

## C. Phantoms — UI surfaces with no backend

| Field | Situation | Note |
|---|---|---|
| `Prediction.consensusScore` | `toPrediction` hardcodes `consensusScore: null`; no numeric consensus field exists on `SessionResult`. `deriveVerdict` accepts `consensus_score` but its decision table never reads it. | UI plumbing exists for a numeric consensus that the backend does not emit. Only `consensusStrength` (the label) is real. |
| `Prediction.suggestedActions` → `ChatPanel` chips | Backend `SessionResult.suggestedActions` always returns `[]` (Sprint 25 per CLAUDE.md). `ChatPanel` renders chips only when non-empty. | UI renderer is live; backend never populates it. Harmless (renders nothing) but a dead surface today. |
| `SessionDetail.predictionSeries` | Backend type + repository fetch this subcollection, but the agent writes empty arrays (CLAUDE.md). No frontend mapper reads it anyway. | Double-dead: empty from the agent AND dropped at the mapper. No `predictionSeries` field on `Prediction`. |
| `SentimentAnalysis` / `MarketComparison` cards | Render full charts, but `sentimentTimeSeries` and `marketProbability` are typically empty/null (CLAUDE.md: no live market/sentiment data). | Cards fall back to `StateMessage` empty states — working as designed, but they are mostly-empty surfaces today. |

---

## D. Drift — name / shape mismatches

1. **`evidence.impact` vs `evidence.impactOnForecast`** — the backend `Evidence`
   interface carries **both** (`sessions.service.ts:83` `impactOnForecast`,
   `:89` `impact`). CLAUDE.md's documented evidence schema lists only
   `impactOnForecast: 'positive'|'negative'|'neutral'` as the synthesis output —
   there is no `impact` in the documented schema. `App.tsx:231-232`
   (`toTimelineEvents`) maps **both**: `impact: evidence.impact ?? 'neutral'`
   and `impactOnForecast: evidence.impactOnForecast`. `EvidenceTimeline` then
   renders **only `impact`** (dot color at line ~92, label at ~107) and never
   reads `impactOnForecast`. If the agent populates `impact_on_forecast` (the
   documented field) and leaves `impact` empty, every evidence row silently
   defaults to `'neutral'`. This is the likely root of CLAUDE.md known problem
   #13 ("negative evidence mislabeled") — the UI is reading the non-canonical
   field.

2. **`deriveVerdict` parameter `consensus_score` (snake_case)** —
   `deriveVerdict.ts:22` declares `consensus_score: number`, fed by
   `index.tsx:66` `consensus_score: prediction.consensusScore ?? 0`. The
   frontend type is camelCase `consensusScore`; the backend emits neither —
   it only emits `consensusStrength`. Not a rename of a real field, but a
   snake_case parameter modeled on a backend field that does not exist.

3. **`Prediction.explanation` (deprecated)** — `App.tsx:114-118` synthesizes
   `explanation` from `detailedExplanation ?? bottomLineAnswer ??
   summaryMarkdown ?? 'Forecast is still being prepared.'`. It is no longer a
   backend field at all; it is a derived compatibility shim consumed by
   `DashboardPage.tsx` (`currentAnswer={prediction?.explanation}`, lines 504 /
   602 / 648) to seed chat context. Flagged as drift only because the name
   implies a backend field; it is purely client-derived.

4. **Shape note — `summaryMarkdown` nullability** — backend
   `SessionResult.summaryMarkdown` is typed `string` (non-null) and the
   repository reads `data.summaryMarkdown` with no `?? null` guard
   (`session.repository.ts:179`), so a missing Firestore field yields
   `undefined`. The frontend tolerates it (`?? null` in `toPrediction`), so no
   runtime break — but the backend type over-promises.

---

## Open questions

1. **Is `evidence.impact` deprecated or in-flight?** Both `impact` and
   `impactLabel` are tagged "New: Impact classification" in
   `sessions.service.ts`, yet CLAUDE.md's canonical evidence schema documents
   `impactOnForecast` instead. Need the agent owner to confirm which field
   the pipeline actually writes — this determines whether Drift #1 is a real
   bug or a harmless duplicate. (Resolvable at runtime: inspect an evidence
   doc, or `console.log` a sample in `toTimelineEvents`.)

2. **`marketComparison` array vs `marketProbability` scalar** — both exist on
   `SessionResult`. Is `marketComparison` the intended future shape (multiple
   markets) and `marketProbability` a temporary scalar, or vice versa? Affects
   whether the dropped array is worth wiring.

3. **`generatedAt` / `agentVersion`** — mapped onto `Prediction` but read by
   nothing post-Slice-2 (the pre-redesign `PredictionOverview.tsx` rendered a
   footer with them). Intentionally retired, or an accidental loss in the
   redesign? Designer's call.

4. **`predictionSeries`** — fetched all the way through the backend but the
   agent writes `[]`. Is the time-series feature still planned (CLAUDE.md lists
   it out of scope "right now"), so the plumbing is intentional, or is it dead?
