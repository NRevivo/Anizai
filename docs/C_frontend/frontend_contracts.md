# frontend_contracts.md
> Domain: C — Frontend / BFF
> Type: Spec
> Last updated: 2026-07-18
> TL;DR: Every data shape crossing a boundary in Domain C — the session lifecycle, the REST wire types returned by `GET /sessions/:id`, the Firestore documents the client reads directly, and the mapping layer that turns both into view models. Open this for any question about what a field is called, what it holds, or who writes it.

## Navigation
- §1 — Overview — three contract surfaces, and the duplication between them
- §2 — Session Lifecycle — the six statuses, the two status vocabularies, who writes each
- §3 — REST Wire Types — §3.1–§3.9, the `GET /sessions/:id` aggregate
- §4 — Firestore Direct-Read Contracts — what the client listeners read
- §5 — View-Model Mapping — wire types → `Prediction` / `TimelineEvent` / `ChatMessage`
- §6 — Known Constraints

---

## §1 — Overview

Three contract surfaces exist in Domain C, and they are **not** the same shape:

1. **REST wire types** — what `GET /sessions/:id` returns. Declared twice: once in
   `server/src/services/sessions.service.ts`, once in
   `client/src/services/session.service.ts`.
2. **Firestore document shapes** — what the client's three listeners read directly,
   bypassing the BFF. Declared in `client/src/services/session.service.ts`
   (`SessionDocData`) and `client/src/types/index.ts` (`AgentEvent`).
3. **View models** — what components actually render. Declared in
   `client/src/types/index.ts` (`Prediction`, `TimelineEvent`, `ChatMessage`,
   `PredictionSession`, `SentimentDataPoint`).

Surface 1 is **hand-mirrored** between server and client with no shared package and no
generation step. The two declarations are currently identical field-for-field (verified
2026-07-18), but nothing enforces that — a change to one side does not break the other
at compile time. Tracked KG-C-1.

Surface 2 exists because the BFF is not in the update path during a run. See
`frontend_overview.md §2`.

> **Naming is not uniform across the boundary.** Firestore documents written by the
> agent use **snake_case**; the REST wire types are **camelCase**; two nested types
> (`KeyFactor.evidence_ids`, and the evidence read path) keep snake_case deliberately.
> The BFF repository is where normalization happens — see §3.5.

---

## §2 — Session Lifecycle

### §2.1 The six statuses

`Session.status` (`server/src/services/sessions.service.ts:28`, mirrored at
`client/src/services/session.service.ts:14`):

| Status | Written by | Meaning |
|---|---|---|
| `queued` | BFF | Session created (or re-queued after clarification); waiting for the agent to claim it |
| `claimed` | Agent | Queue doc claimed; run starting |
| `running` | Agent | Graph executing |
| `done` | Agent | `sessionResults/{id}` written; forecast readable |
| `failed` | Agent | Run aborted; `errorCode` / `errorMessage` populated |
| `awaiting_clarification` | Agent | Question ambiguous; `clarificationCandidates` populated, waiting on the user |

The BFF only ever writes `queued`. Every other transition is the agent's.

### §2.2 Two status vocabularies

`sessions/{id}` and `forecastQueries/{id}` are different documents with different
status fields. They are not interchangeable:

| Document | Field | Initial value | Written by |
|---|---|---|---|
| `sessions/{id}` | `status` | `'queued'` | BFF (`session.repository.ts:417`) |
| `forecastQueries/{id}` | `status` | `'pending'` | BFF (`session.repository.ts:436`) |

`forecastQueries` documents also carry `queryId` (a fresh `randomUUID()`), `sessionId`,
`userId`, `question`, `createdAt`, `claimedAt: null`, `claimedBy: null`. The agent
claims by setting `claimedAt` / `claimedBy`.

### §2.3 Queue-document creation paths

Two code paths create a `forecastQueries` document, and **they use different document
IDs**:

| Path | Queue doc ID | Code |
|---|---|---|
| `POST /sessions` | `sessionRef.id` — same id as the session | `session.repository.ts:443` — `collectionRef('forecastQueries').doc(sessionRef.id)` |
| `POST /sessions/:id/clarify` | Firestore auto-id — **unrelated to the session id** | `session.repository.ts:462` — `collectionRef('forecastQueries').doc()` |

This asymmetry has a cleanup consequence — see §6, KG-C-8.

### §2.4 Idempotency

`POST /sessions` requires a UUID `idempotencyKey` (zod: `.trim().min(1).uuid()`,
`server/src/routes/sessions.ts:17`). Before charging usage, the service looks for an
existing session with the same `userId` + `idempotencyKey` created within a
**60-second window** (`windowMs = 60_000`, `session.repository.ts:105`) and returns it
instead of creating a second one. The lookup runs **twice** — once before
`incrementUsage`, once after — so a race that slips through the first check still
avoids a double charge (`sessions.service.ts:277-298`).

`idempotencyKey` is persisted on the session document but is **not** exposed on the
`Session` wire type.

### §2.5 Retry

`POST /sessions/:id/retry` is valid only when `status === 'failed'`
(`sessions.service.ts:358`). It hard-deletes the failed session and creates a fresh one
with a server-generated `randomUUID()` idempotency key. Delete runs first and its
failure is caught and logged rather than thrown, so a partial delete still yields a
usable retry session (`sessions.service.ts:367-374`).

---

## §3 — REST Wire Types

Everything in this section is the shape of `GET /sessions/:id` → `{ data: SessionDetail }`.

### §3.1 Envelope

```ts
// server/src/types/api.ts
{ data: T,     meta?: { requestId?: string, timestamp?: string } }   // success
{ error: { message: string, code?: string, details?: unknown },
  meta?: { requestId?: string, timestamp?: string } }                // error
```

The client unwraps `.data` and discards `meta` entirely (`client/src/lib/api.ts`,
`apiRequest` returns `(parsedBody as ApiSuccessResponse<T>).data`). Non-2xx responses
become `ApiError`, or `ApiAuthError` for 401/403.

### §3.2 `SessionDetail`

```ts
{
  session:            Session;
  messages:           SessionMessage[];
  predictionSeries:   PredictionPoint[];   // agent writes none today — §6
  evidence:           Evidence[];          // capped at 50, §3.5
  result:             SessionResult | null;  // null until status === 'done'
  sentimentTimeSeries: SentimentDataPoint[]; // agent writes none today — §6
}
```

### §3.3 `Session`

```ts
{
  id:                      string;
  userId:                  string;
  question:                string;
  title:                   string | null;
  status:                  'queued'|'claimed'|'running'|'done'|'failed'|'awaiting_clarification';
  latestProbability:       number | null;   // 0–1 float; BFF-derived, see below
  latestConfidence:        number | null;   // 0–1 float
  followEnabled:           boolean;         // always false today; no feature reads it
  isFollowing:             boolean;         // always false today
  canonicalKey:            string | null;   // set from the chosen clarification candidate
  errorCode:               string | null;
  errorMessage:            string | null;
  clarificationCandidates: ClarificationCandidate[] | null;
  createdAt:               string;          // ISO8601
  updatedAt:               string;          // ISO8601
  lastActivityAt:          string;          // ISO8601
}
```

> **`latestProbability` is derived by the BFF, not read straight off the document.**
> The agent writes `finalProbability` into `sessionResults/{id}` but never copies it
> onto the session doc, so `sessions/{id}.latestProbability` stays `null`. For sessions
> with `status === 'done'` and a null value, `enrichLatestProbability()` fetches the
> result doc and substitutes `finalProbability`
> (`server/src/repositories/session.repository.ts:190-217`). A directly-written value
> always wins, so this becomes a no-op if the agent ever starts writing the field. It
> runs on both `listSessions` and `getSession`, costing one extra read per affected
> session.

`ClarificationCandidate`:

```ts
{ id: string;              // canonical market id
  label: string;
  source: 'polymarket' | 'kalshi';
  description: string;     // resolution criteria / longer context
  matchConfidence: number; // 0–1
}
```

### §3.4 `SessionResult`

Null until `status === 'done'`.

> ### The 0–1 convention
>
> **Every probability, confidence, and sentiment value in Domain C is a 0–1 float.
> Conversion to a percentage happens only at render time, never in storage, transport,
> mapping, or component props.**
>
> This holds across all four layers: Firestore documents, the REST wire types in this
> section, the view models in `client/src/types/index.ts`, and component props.
> `client/src/lib/utils.ts` `formatProbability()` is the shared formatter and the only
> place a `× 100` belongs; chart components convert internally for their axes
> (`SentimentAnalysis` renders 0–100 for recharts while accepting 0–1 props).
>
> It applies to `finalProbability`, `confidence`, `marketProbability`,
> `latestProbability`, `latestConfidence`, `keyFactors[].weight`,
> `evidence[].relevanceScore`, `evidence[].recencyWeight`,
> `sentimentTimeSeries[].expertSentiment` / `publicSentiment`, and
> `ClarificationCandidate.matchConfidence`.
>
> **This is the most breakable cross-cutting convention in the codebase.** It has been
> violated before — a stale `/ 100` in the trending demo fallback produced 0–0.01
> values on that path, and one card rendered a raw 0–1 value with a `%` sign. Both
> were fixed in the Task-2 standardization pass. There is no runtime guard and no type
> that distinguishes a ratio from a percentage, so the only protection is this
> convention. A backend that starts sending `62` instead of `0.62` will render as
> `6200%` with nothing failing loudly.

All probability/confidence values below are therefore 0–1.

```ts
{
  sessionId:               string;
  userId:                  string;
  finalProbability:        number;          // 0–1 — how likely the event is
  confidence:              number;          // 0–1 — how sure the model is of that number
  marketComparison:        { source: string; value: number }[];  // defaults []
  summaryMarkdown:         string | null;   // render via react-markdown
  createdAt:               string;
  updatedAt:               string;
  confidenceLabel:         'High Confidence' | 'Medium Confidence' | 'Low Confidence' | null;
  consensusStrength:       'Strong' | 'Moderate' | 'Weak' | null;
  evidenceVolumeLabel:     'High' | 'Medium' | 'Low' | null;
  bottomLineAnswer:        string | null;   // the headline thesis
  detailedExplanation:     string | null;
  marketProbability:       number | null;   // 0–1
  marketComparisonInsight: string | null;
  sentimentAnalysisInsight: string | null;
  evidenceFeedSummary:     string | null;
  keyFactors:              KeyFactor[];     // defaults []
  whatIDidntFind:          string[];        // defaults []
  reasoningChain:          ReasoningStep[]; // defaults []
  suggestedActions:        SuggestedAction[]; // defaults []
  generatedAt:             string | null;
  agentVersion:            string | null;
  tier:                    'tier_1' | 'tier_2' | null;
}
```

Every optional field is defaulted in the repository mapper
(`session.repository.ts:210-236`) — arrays to `[]`, scalars to `null`. A missing
Firestore field never surfaces as `undefined`.

**There is no numeric consensus score.** `consensusStrength` (the label) is the only
consensus signal the backend emits.

#### Nested types — these are where the old task-logs are wrong

```ts
// server/src/services/sessions.service.ts:135 · client/src/types/index.ts
// snake_case is DELIBERATE: the agent writes these key names into Firestore and the
// BFF passes the object through unchanged. There is no rename layer.
KeyFactor {
  label:        string;
  description:  string;
  direction:    'increases' | 'decreases';   // binary — no neutral
  weight:       number;                      // 0–1
  evidence_ids: string[];
}

// server/src/services/sessions.service.ts:143
ReasoningStep { step: number; title: string; description: string; }

// server/src/services/sessions.service.ts:149
SuggestedAction { id: string; label: string; prompt: string; }
```

> `docs/session-result-contract-task-10.md §3` documents `KeyFactor` as
> `{ rank, title, explanation, direction, weight, supportingEvidenceIds }` and
> `ReasoningStep` as `{ sequence, description, outcome }`. **Neither matches the code.**
> That doc is superseded by this section.

### §3.5 `Evidence`

Read from the `sessions/{id}/evidence` subcollection, capped at **50 items**
(`getEvidence(sessionId, limit = 50)`).

```ts
{
  id:               string;   // Firestore doc id
  type:             'news' | 'social' | 'expert' | 'market';
  evidenceId:       string | null;
  sourceType:       string | null;   // raw hub vocabulary, e.g. 'vault_news' — mapped in §5.2
  origin:           string | null;
  title:            string;
  snippet:          string;
  url:              string | null;
  source:           string | null;
  sourceDomain:     string | null;
  publishedAt:      string | null;   // ISO8601
  fetchedAt:        string | null;   // ISO8601
  sourceId:         string | null;
  score:            number;
  // Objective tier — item-intrinsic, set by the agent's rating step
  relevanceScore:   number | null;   // 0–1
  credibilityTier:  string | null;   // 'tier_1' | 'tier_2' | 'tier_3'
  recencyWeight:    number | null;   // 0–1
  // Forecast-specific tier — how THIS answer used the item, set by synthesis
  usedInAnswer:     boolean | null;
  impactOnForecast: string | null;   // 'positive' | 'negative' | 'neutral'
  justification:    string | null;
  rank:             number | null;
  createdAt:        string;
  impact:           'positive' | 'negative' | 'neutral' | null;  // legacy — §6
  impactLabel:      string | null;
  isKeyEvidence:    boolean;
}
```

#### §3.5.1 Case normalization — the evidence read path

**Evidence documents are written to Firestore in snake_case.** The repository mapper
dual-reads both cases and emits camelCase (`session.repository.ts:319-342`):

```ts
evidenceId:       data.evidence_id       ?? data.evidenceId       ?? null,
sourceType:       data.source_type       ?? data.sourceType       ?? null,
sourceDomain:     data.source_domain     ?? data.sourceDomain     ?? null,
publishedAt:      toISOString(data.published_at ?? data.publishedAt),
fetchedAt:        toISOString(data.fetched_at   ?? data.fetchedAt),
sourceId:         data.source_id         ?? data.sourceId         ?? null,
relevanceScore:   data.relevance_score   ?? data.relevanceScore   ?? null,
credibilityTier:  data.credibility_tier  ?? data.credibilityTier  ?? null,
recencyWeight:    data.recency_weight    ?? data.recencyWeight    ?? null,
usedInAnswer:     data.used_in_answer    ?? data.usedInAnswer     ?? null,
impactOnForecast: data.impact_on_forecast ?? data.impactOnForecast ?? null,
createdAt:        toISOString(data.created_at ?? data.createdAt) ?? '',
impactLabel:      data.impact_label      ?? data.impactLabel      ?? null,
isKeyEvidence:    data.is_key_evidence   ?? data.isKeyEvidence    ?? false,
```

Snake_case wins where both exist. The camelCase branch exists for older fixtures.
The in-code comment names the pipeline schema (`data-pipeline/agent/schemas.py`) as
the reason. **This normalization is the single most important undocumented fact about
the evidence contract** — a reader who assumes camelCase on disk will write a query
that silently returns nothing.

Four fields are read **without** a snake_case alternative and **without** a null
default: `type`, `title`, `snippet`, `score`. If the agent does not write those exact
camelCase keys, they arrive as `undefined` despite non-optional types. Not verified
against a live document — flagged in §6.

#### §3.5.2 Evidence ordering

Evidence is **not** ordered server-side. The pipeline's `write_evidence_batch` does not
write a `createdAt` field, so a Firestore `orderBy('createdAt')` would silently exclude
every pipeline-written row. The repository fetches the whole subcollection, sorts in
memory by `publishedAt ?? createdAt` descending (NaN dates sink to the bottom), then
slices to the limit (`session.repository.ts:346-364`). Per-session evidence is ~30–50
docs, so this is cheap.

### §3.6 `SessionMessage`

```ts
{
  id:                string;
  role:              'user' | 'assistant' | 'system';
  content:           string;
  createdAt:         string;   // ISO8601
  status:            'sent' | 'failed' | 'answered' | null;
  userId?:           string | null;
  replyToMessageId?: string | null;   // top-level on the doc, NOT nested in meta
  meta:              { model?: string; tokensIn?: number;
                       tokensOut?: number; runId?: string } | null;
}
```

- `'answered'` is written by the **hub**, which flips the triggering user message
  `sent → answered` in the same write batch as the assistant reply.
- `replyToMessageId` is written by the hub on assistant messages only. The BFF's
  `addMessage` never writes it (`session.repository.ts:497-503` writes `userId`, `role`,
  `content`, `createdAt`, `status: 'sent'`, `meta` — nothing else).

### §3.7 `SentimentDataPoint`

```ts
{ id: string; ts: string; date: string;
  expertSentiment: number;        // 0–1
  expertUpper: number | null; expertLower: number | null;
  publicSentiment: number;        // 0–1
  createdAt: string; }
```

Capped at 100 points. Empty in practice — §6.

### §3.8 `PredictionPoint`

```ts
{ id: string; ts: string; probability: number; confidence: number;
  reasonType: 'news' | 'market' | 'model_update'; evidenceIds: string[]; }
```

Fetched end-to-end but empty in practice, and read by no client mapper — §6.

### §3.9 Composite-index fallbacks

`listSessions`, `getMessages`, `getPredictionSeries`, and `getSentimentTimeSeries` each
wrap their ordered query in a `try/catch` on Firestore's `failed-precondition` error
(code `9`). On that error they refetch unordered and sort in memory
(`isFailedPrecondition`, `session.repository.ts:9-16`). This keeps local/dev working
before composite indexes are deployed; it is not a correctness path in production.

---

## §4 — Firestore Direct-Read Contracts

The client reads three surfaces directly through the Firebase Web SDK, gated by
`server/firebase/firestore.rules`. These shapes never pass through the BFF.

### §4.1 `SessionDocData` — `subscribeToSession`

Listener on `sessions/{sessionId}` (`client/src/services/session.service.ts:276`).
A **narrower projection** than the REST `Session`, not the same type:

```ts
{
  id:                      string;
  status:                  SessionStatus;
  latestProbability:       number | null;
  latestConfidence:        number | null;
  currentRunId:            string | null;   // hub-written; identifies the live run
  errorCode:               string | null;
  errorMessage:            string | null;
  clarificationCandidates: ClarificationCandidate[] | null;
}
```

Every field is read defensively (`typeof x === 'string' ? x : null`), so a malformed
document degrades to nulls rather than throwing. Non-existent snapshots are ignored.

> **`currentRunId` has no REST equivalent.** It is read off the live document
> specifically because the REST aggregate only refreshes on an explicit refetch.

### §4.2 `AgentEvent` — `subscribeToAgentEvents`

Listener on `sessions/{sessionId}/agentEvents`, ordered by `sequence` ascending
(`session.service.ts:310`). Shape from `client/src/types/index.ts`:

```ts
{
  eventId:         string;
  sessionId:       string;
  runId:           string | null;   // groups events by forecast run
  sequence:        number;
  timestamp:       Date;
  parentMessageId: string | null;
  type:            AgentEventType;   // 15-value union, below
  title:           string;
  description:     string | null;
  status:          'pending' | 'running' | 'done' | 'failed';
  durationMs:      number | null;
  payload:         Record<string, unknown> | null;   // telemetry; not rendered
}
```

`AgentEventType` — 15 values: `vault_query`, `vault_query_result`,
`sufficiency_check`, `reactive_search`, `reactive_search_result`, `evidence_rated`,
`synthesis_started`, `synthesis_complete`, `clarification_needed`, `followup_started`,
`context_loaded`, `followup_search`, `followup_response_complete`, `error`.

**Rule B — run scoping.** `selectCurrentRunEvents(events, currentRunId)`
(`client/src/lib/agentEvents.ts`) filters to events whose `runId` matches the session
doc's `currentRunId`, then sorts by `sequence`. **If `currentRunId` is null, it returns
an empty array** — no run is live, so nothing renders. Events with a null or
non-matching `runId` are never included. The sort is duplicated here rather than
inherited from the Firestore query order so the guarantee is self-contained and
unit-testable (`client/src/lib/agentEvents.test.ts`).

`mapAgentEventDoc` falls back to the Firestore doc id when `eventId` is absent, and to
`'Untitled event'` when `title` is absent (`session.service.ts:185-204`).

### §4.3 Message documents — `subscribeToSessionMessages`

Listener on `sessions/{sessionId}/messages`, ordered by `createdAt` ascending. Maps to
the same `SessionMessage` shape as §3.6. `status` is narrowed explicitly — only
`'failed'`, `'answered'`, `'sent'` pass through; anything else becomes `null`
(`session.service.ts:214-221`).

### §4.4 Collection map and access rules

Server-side collection constants (`server/src/services/firebase.service.ts`):
`users`, `sessions`, `sessionResults`, `trendingForecasts`, `forecastQueries`.

From `server/firebase/firestore.rules`:

| Path | Client access |
|---|---|
| `users/{uid}` | Read own; create/delete denied; update restricted — `email`, `plan`, `planExpiresAt`, `usageMonth`, `monthlyForecastsUsed` must be unchanged |
| `users/{uid}/follows/{sessionId}` | Read/write own |
| `sessions/{sessionId}` | Read if owner; **all writes denied** (Admin SDK only) |
| `sessions/*/messages/{id}` | Read if session owner; create restricted to `role == "user"` |
| `sessions/*/evidence/{id}` | Read if session owner |
| `sessions/*/predictionSeries/{id}` | Read if session owner |
| `sessions/*/agentEvents/{id}` | Read if session owner |
| `sessionResults/{sessionId}` | Read gated on parent session ownership |
| `forecastQueries/{queryId}` | **Server-only** — the agent uses its own service account |
| `trendingForecasts`, `canonicalForecasts`, `sources`, `payments` | see rules file |

Ownership is resolved with a `get()` on the parent session doc (`isSessionOwner`),
which bills an extra document read per subcollection rule evaluation.

### §4.5 Deletion cleanup

`deleteSession` (`session.repository.ts:531-551`) drains five subcollections in
200-doc chunks — `messages`, `predictionSeries`, `evidence`, `sentimentTimeSeries`,
`agentEvents` — then batch-deletes `sessions/{id}`, `sessionResults/{id}`, and
`forecastQueries/{id}`. See §6 KG-C-8 for the case this misses.

---

## §5 — View-Model Mapping

All mapping lives in `client/src/App.tsx` as module-level pure functions. Components
never see wire types.

| Mapper | Input | Output | Line |
|---|---|---|---|
| `toSidebarSession` | `SessionListItem` | `PredictionSession` | `App.tsx:92` |
| `toPrediction` | `SessionDetail \| null` | `Prediction \| null` | `App.tsx:105` |
| `toActiveSessionState` | `SessionDetail \| null` | `ActiveSessionState \| null` | `App.tsx:158` |
| `toSentimentPoints` | `SessionDetail \| null` | `SentimentDataPoint[]` | `App.tsx:173` |
| `toTimelineEvents` | `SessionDetail \| null` | `TimelineEvent[]` | `App.tsx:188` |
| `toChatMessages` / `toChatMessage` | `SessionDetail` / `SessionMessage` | `ChatMessage[]` | `App.tsx:241` |
| `toTrendingView` | `TrendingForecast[]` | `TrendingQuestionView[]` | `App.tsx:266` |

### §5.1 `toPrediction` — result-then-session fallback

`Prediction` flattens `SessionDetail.session` and `SessionDetail.result` into one
object, preferring the result and falling back to the session:

```ts
probability = result?.finalProbability ?? session.latestProbability ?? 0
confidence  = result?.confidence      ?? session.latestConfidence  ?? 0
```

So an in-flight session renders a `Prediction` with zeros rather than null.

`Prediction.explanation` is a **client-derived compatibility shim**, marked
`@deprecated` in `client/src/types/index.ts`. It is not a backend field:

```ts
explanation = result?.detailedExplanation
           ?? result?.bottomLineAnswer
           ?? result?.summaryMarkdown
           ?? 'Forecast is still being prepared.'
```

### §5.2 `toTimelineEvents` — sourceType normalization

The hub's `sourceType` vocabulary is collapsed into the four UI categories
(`App.tsx:193-210`):

| Hub `sourceType` | UI `sourceType` |
|---|---|
| `vault_news`, `online_news` | `news` |
| `vault_telegram`, `online_blog`, `vault_hackernews` | `social` |
| `vault_arxiv` | `expert` |
| `vault_market`, `vault_fred` | `market` |
| *(anything else / null)* | falls back to `evidence.type` |

Other behavior in this mapper:

- `date` is a **display string** (`toLocaleDateString('en-US', {month:'short', day:'numeric'})`,
  e.g. `Jan 5`), while `timestamp` keeps the real `Date`. Sort on `timestamp`, never `date`.
- `description` is set to `evidence.snippet` — the same value as `snippet`.
- `impactOnForecast` is passed through; the legacy `impact` field is **not** mapped.

### §5.3 `toChatMessage`

`role: 'system'` collapses to `'assistant'`. `status` preserves `'failed'` and
`'answered'` distinctly; everything else reads as `'sent'` (`App.tsx:249-265`).

### §5.4 Dead mapper

`mapSessionStatus(status, confidence) → 'stable' | 'volatile'` still exists at
`App.tsx:74`, immediately followed by `void mapSessionStatus;` at `App.tsx:89` to
suppress the unused warning. **It is dead code.** `PredictionSession.status` and
`Prediction.status` both carry the full `SessionStatus` union now. Documentation
claiming the UI collapses backend statuses into `stable`/`volatile` is describing a
retired behavior.

---

## §6 — Known Constraints

| Constraint | Detail |
|---|---|
| Contract types duplicated, unenforced | The REST wire types are declared independently in `server/src/services/sessions.service.ts` and `client/src/services/session.service.ts`. No shared package, no codegen, no cross-package type test. Identical as of 2026-07-18; nothing prevents divergence. Tracked KG-C-1. |
| Evidence is snake_case on disk | The agent writes snake_case keys; the BFF dual-reads and emits camelCase (§3.5.1). Any new consumer reading `sessions/*/evidence` **directly** from Firestore must handle snake_case — the camelCase names exist only downstream of the repository mapper. |
| Four evidence fields have no fallback | `type`, `title`, `snippet`, `score` are read raw with no snake_case alternative and no null default, but are typed non-optional. If the agent writes `source_type`-style keys for these, they arrive `undefined`. **Not verified against a live evidence document** — needs a Firestore inspection to confirm which keys the agent actually writes. |
| `impact` is a dead duplicate of `impactOnForecast` | Both exist on the wire `Evidence` type (`sessions.service.ts:90` and `:95`, the latter tagged `// New: Impact classification`). The agent only populates `impactOnForecast`. `TimelineEvent` dropped `impact` entirely and the UI reads `impactOnForecast` exclusively. The wire-type field is retained but unused. |
| `latestProbability` is BFF-derived | The agent never writes it to the session doc; the BFF back-fills from `sessionResults` for `done` sessions at the cost of one extra read each (§3.3). Removable once the agent writes the field directly. |
| `predictionSeries` is dead plumbing | Typed, fetched, index-fallback-handled, and returned on every `SessionDetail` — but the agent writes no points, and **no client mapper reads the array**. There is no `predictionSeries` field on `Prediction`. Dead on both ends. |
| `sentimentTimeSeries` / `marketComparison` / `marketProbability` empty | The agent emits nothing for these; the Sentiment and Market cards render deliberate empty states. Expected shapes are specified in `../backend-specs/market-sentiment-spec.md`. Tracked KG-C-6. |
| `followEnabled` / `isFollowing` are inert | Written `false` at creation, never updated, read by no UI. Reserved for a tracking/follow feature that does not exist. |
| Mapped-but-never-rendered fields | `generatedAt`, `agentVersion` reach `Prediction` and stop; `evidenceId`, `origin`, `url`, `fetchedAt`, `recencyWeight`, `usedInAnswer`, `rank` reach `TimelineEvent` and stop; `AgentEvent.payload` and `SessionMessage.meta` are mapped and unused. Not defects — but the plumbing implies a consumer that isn't there. |
| Orphaned `forecastQueries` doc after clarify-then-delete | `POST /sessions/:id/clarify` creates its queue doc under a Firestore auto-id (§2.3), but `deleteSession` only deletes `forecastQueries/{sessionId}`. A session that went through clarification and was later deleted leaves its clarify-path queue document behind. Reasoned from the code paths; **not reproduced against a live Firestore**. Tracked KG-C-8. |
| Sequential reads in `getSessionDetail` | The five subcollection reads run in `Promise.all`, but `getSessionResult` re-runs the ownership `getSession()` inside that parallel block — so the session document is read twice per detail request (`sessions.service.ts:245-261`). Correctness is fine; it is one redundant read. |

> `frontend_sprints.md §4` is the authoritative `KG-C-*` register. `KG-C-8` is raised
> here and must be recorded there.
