# Anizai — Frontend Refactor Handoff (Hub Integration Prep)

Hey 👋 This is the consolidated reference doc for the frontend/BFF work needed to prepare Anizai for the agentic hub integration.

It pulls together everything we've agreed on across three planning rounds. Bring this into a fresh conversation with your AI coding assistant — along with the existing project files — and use it as the single source of truth for the refactor.

**This doc replaces the three earlier docs** (`anizai_frontend_suggestions.md`, `anizai_data_contracts.md`, `anizai_ux_and_behavior.md`). You don't need to reference those separately anymore — everything they contained that we agreed on is here.

---

## Table of contents

1. [Context — what's being built and why](#1-context)
2. [Architectural foundation (already agreed)](#2-architectural-foundation)
3. [Frontend/BFF changes — must do before hub integration](#3-frontend-changes-must-do)
4. [Frontend/BFF changes — nice to have](#4-frontend-changes-nice-to-have)
5. [Data contracts (Firestore schemas)](#5-data-contracts)
6. [V1 UX features](#6-v1-ux-features)
7. [Explicitly deferred (not in V1)](#7-explicitly-deferred)
8. [Suggested implementation order](#8-suggested-implementation-order)
9. [Coordination points with the hub team](#9-coordination-points)

---

## 1. Context

### What's being built

The **agentic hub** is a new Python service (LangGraph-based) that produces actual forecasts. When a user submits a question, the hub:

1. Searches our internal knowledge vault (Postgres) for relevant signals
2. If the vault doesn't have enough info, searches the live web for fresh articles
3. Reasons over all the evidence using LangGraph
4. Produces a probability forecast + chain-of-thought explanation
5. Writes the result back to Firestore so the frontend can display it

### Why your work matters

The hub will be Firebase-native — it writes results to the same Firestore collections your frontend already reads from. That means **your existing UI keeps working** as the data layer is upgraded from seed/mock data to real AI-generated forecasts. Most of this refactor is preparation for the hub's writes; very little of it changes existing user-facing behavior.

---

## 2. Architectural foundation

These four decisions are locked in. They underpin everything else in this doc.

| Decision | Choice |
|---|---|
| **Storage** | Hub writes to Firestore directly (same store the frontend reads). Postgres stays for vault data only. |
| **Trigger mechanism** | Worker pattern — hub watches `forecastQueries` collection via Firestore listener. When a `pending` doc appears, hub claims it and processes. |
| **Real-time updates** | Frontend uses Firestore real-time listeners on session subcollections (no WebSocket). Updates appear as the hub writes. |
| **Auth between hub and Firestore** | Firebase Admin SDK with service account credentials (same model as Express). |

### What this means in practice

User submits forecast →
`POST /sessions` creates a Firestore session (status `queued`) and a `forecastQueries` doc (status `pending`) →
Hub worker sees the new doc via Firestore listener, claims it, runs the agent →
Hub writes evidence/predictions/sentiment/events to Firestore subcollections **as it reasons** (not all at the end) →
Frontend's Firestore listeners see each write and update the BI cards in real time →
Hub finishes, sets session status to `done`, frontend shows the final summary.

No WebSockets, no queue infrastructure, no extra services. Firestore is the message bus.

---

## 3. Frontend/BFF changes — must do

Five small foundational changes. Total effort: ~2.5 hours. All defensive — they prevent specific bugs we know we'd hit otherwise.

### 3.1 — Pick ONE probability unit convention (0-1 floats everywhere)

**The problem:** Three conventions in flight (`0-1` floats from Polymarket, `0-100` from test data, `normalizePercent` heuristic in `App.tsx`). Edge cases will silently mis-display.

**The fix:**
- Standardize on **0-1 floats everywhere** in storage, APIs, and component props (this is the standard ML convention — Polymarket, Manifold, Metaculus all use 0-1 internally)
- Convert to `0-100` only inside the BI card render layers, right at the `<span>` displaying the percentage
- Delete `normalizePercent` from `App.tsx`
- Update `seed.ts`, `test-session-result.ts` and any other test fixtures to use 0-1

### 3.2 — Fix the `/api` prefix inconsistency

**The problem:** `.env.example` says `VITE_API_BASE_URL=/api`, `client/src/lib/api.ts:3` hardcodes `http://localhost:3000` (which overrides the env var), and `vite.config.ts:7-15` proxies `/api/*` → `http://localhost:3000`. Three different conventions; production deploy will break.

**The fix:**
- Frontend always uses relative paths (`/api/sessions`, `/api/me`, etc.)
- `api.ts` reads from `import.meta.env.VITE_API_BASE_URL` (no hardcoded fallback)
- Vite proxy stays as-is for dev
- Production reverse proxy / hosting platform routing handles `/api` → Express in prod
- Settle Express side: either accept both `/sessions` and `/api/sessions`, or pick one convention and update the proxy accordingly

### 3.3 — Add real session status ownership

**The problem:** `Session.status` enum is `'draft' | 'running' | 'done'` but nothing transitions sessions between states. They stay `draft` forever.

**The fix:**
- Update enum to: `'queued' | 'claimed' | 'running' | 'done' | 'failed' | 'awaiting_clarification'`

  > **Note on `'claimed'`:** Worker has atomically claimed the query from `forecastQueries`; no reasoning has started yet. Frontend should show a "starting analysis" state (between the queued spinner and the live chain-of-thought stream).
- `POST /sessions` creates session with `status: 'queued'` (instead of `'draft'`)
- Add `errorMessage: string | null` field on the session for the `failed` case
- Add `clarificationCandidates: ClarificationCandidate[] | null` field (used by Topic 6.3 — clarification flow)
- The hub owns transitions: `queued → claimed → running → done | failed | awaiting_clarification`
- Frontend renders different UI states based on status

### 3.4 — Add idempotency keys to `POST /sessions`

**The problem:** Double-clicking "Analyze Forecast" creates two sessions, two `forecastQueries` rows, two agent runs = double the OpenAI/search bills. The agent takes ~15-20s per run, so the window for accidental double-submission is wide.

**The fix:**
- Frontend generates a UUID v4 per submit attempt (fresh on form mount, regenerated on successful submission)
- `POST /sessions` accepts `idempotencyKey: string` in the request body
- Express checks: any session with this `idempotencyKey` created in the last 60 seconds? If yes, return the existing session. If no, create new.
- Add a Firestore index on `idempotencyKey` for the lookup

### 3.5 — Remove or harden the demo routes

**The problem:** `routes/demo.ts` bypasses auth using a hardcoded `DEMO_USER_ID = 'demo-user-001'`. Only protection is `if (isDev)`. The README says "REMOVE IN PRODUCTION." When the hub goes live with Firestore write access, an auth bypass becomes a real risk.

**The fix:** Either delete `routes/demo.ts` entirely (recommended), or gate behind an additional explicit env var:

```ts
if (process.env.ALLOW_DEMO_ROUTES === 'true' && isDev) {
  app.use('/demo', demoRouter);
}
```

And never set that env var in any deployed environment.

---

## 4. Frontend/BFF changes — nice to have

These are bigger and optional. Both pay off when streaming chain-of-thought UX (Topic 5.2) goes live, but neither is strictly blocking for the first hub integration.

### 4.1 — Add React Router for shareable forecast URLs

Currently `appState` enum drives rendering; URLs never change. Without routing, you can't share a forecast link or bookmark a specific session. The chain-of-thought UX is exactly the kind of thing users will want to share.

**Effort:** ~3-4 hours. Add `react-router-dom`, replace `appState` with route-based rendering, `activeSessionId` becomes a URL param.

If skipped: works fine for v1; gets harder to retrofit as more pages/features are added.

### 4.2 — Move server state to TanStack Query

Currently `App.tsx` holds all server state (`userProfile`, `sessions`, `trending`, `activeSessionDetail`) and re-fetches imperatively. No caching, no automatic refetching, no real-time integration.

When chain-of-thought streaming goes live, this pattern will start to strain. TanStack Query handles real-time subscriptions cleanly, gives caching and automatic refetching for free.

**Effort:** ~4-6 hours.

If skipped: works fine, just gets messier as streaming UI is added.

---

## 5. Data contracts

These are the Firestore schemas the hub will write to. Most fields you already have stay exactly the same; we're mostly adding new fields.

### 5.1 — `SessionResult` shape

**Existing fields (kept exactly as-is):**

```ts
{
  finalProbability,           // 0-1 float
  confidence,                 // 0-1 float
  confidenceLabel,            // "Low" | "Moderate" | "High"
  consensusStrength,          // "Weak" | "Mixed" | "Strong"
  evidenceVolumeLabel,        // "Low" | "Moderate" | "High"
  bottomLineAnswer,           // 1-2 sentence executive summary
  detailedExplanation,        // paragraph
  summaryMarkdown,            // full markdown summary for chat
  marketProbability,          // 0-1 or null (null for Tier 2 / no canonical market)
  marketComparisonInsight,    // narrative for MarketComparison card
  sentimentAnalysisInsight,   // narrative for SentimentAnalysis card
  evidenceFeedSummary,        // narrative above EvidenceTimeline
  marketComparison: MarketDataPoint[]
}
```

**New fields the hub will add:**

```ts
{
  // ... all existing fields ...

  // Agent reasoning artifacts
  keyFactors: KeyFactor[]              // top 3-5 drivers behind the forecast
  whatIDidntFind: string[]             // explicit gaps the agent flagged
  reasoningChain: ReasoningStep[]      // ordered persistent summary

  // Suggested follow-up actions (V1 simpler dynamic)
  suggestedActions: SuggestedAction[]  // 3 dynamic suggestions per forecast

  // Metadata
  generatedAt: Timestamp
  agentVersion: string                 // for debugging across hub deployments
  tier: "tier_1" | "tier_2"            // see section 6.3
}

type KeyFactor = {
  rank: number                         // 1 = most influential
  title: string                        // short headline
  explanation: string                  // 1-2 sentences
  direction: "supports" | "opposes" | "uncertain"
  weight: number                       // 0-1
  supportingEvidenceIds: string[]      // links to evidence items
}

type ReasoningStep = {
  sequence: number
  description: string
  outcome: string
}

type SuggestedAction = {
  id: string                           // uuid
  label: string                        // 3-7 words, clear over clever
  prompt: string                       // sent to follow-up endpoint on click
}
```

**Confidence-label thresholds (used to derive labels from numeric values):**
- `confidence >= 0.8` → `"High"`
- `0.5 <= confidence < 0.8` → `"Moderate"`
- `confidence < 0.5` → `"Low"`

Same pattern for `evidenceVolumeLabel` and `consensusStrength`.

### 5.2 — `evidence` subcollection (extending existing)

**Existing fields (kept):** `type`, `impact`, `impactLabel`, `isKeyEvidence`, title, source, timestamp, url.

**New fields the hub will add:**

```ts
{
  // ... existing fields ...

  // Source identity
  evidenceId: string                   // uuid (Firestore doc ID is fine)
  sourceType: 
    | "vault_news"      | "vault_telegram" | "vault_arxiv"
    | "vault_market"    | "vault_fred"     | "vault_hackernews"
    | "online_news"     | "online_blog"
  origin: "knowledge_vault" | "reactive_search"
  sourceDomain: string                 // e.g., "reuters.com"

  // Content
  snippet: string                      // first ~200 chars
  fetchedAt: Timestamp                 // when agent retrieved (vs published)

  // Agent ratings
  relevanceScore: number               // 0-1
  credibilityTier: "tier_1" | "tier_2" | "tier_3"
  recencyWeight: number                // 0-1

  // Influence
  usedInAnswer: boolean
  impactOnForecast: "increases" | "decreases" | "neutral" | "context_only"

  // Transparency
  justification: string                // one-sentence "why I used this"
  rank: number                         // 1 = most influential
}
```

**Mapping rule for the existing `type` field** (so existing filter tabs keep working):
- `vault_news` or `online_news` → `type: 'news'`
- `vault_telegram` or `online_blog` → `type: 'social'`
- `vault_arxiv` → `type: 'expert'`
- `vault_market` or `vault_fred` → `type: 'market'`
- `vault_hackernews` → `type: 'social'`

The existing filter tabs (All / News / Expert / Social) keep working without any frontend changes.

### 5.3 — `agentEvents` subcollection (NEW)

For the chain-of-thought streaming UX. Hub writes events in real time as it reasons; frontend listens and renders them in a thinking panel.

```ts
type AgentEvent = {
  eventId: string                      // uuid (Firestore doc ID)
  sessionId: string
  sequence: number                     // 1, 2, 3... for ordering
  timestamp: Timestamp
  parentMessageId: string | null       // null for main forecast, set for follow-ups

  type: 
    | "vault_query"
    | "vault_query_result"
    | "sufficiency_check"
    | "reactive_search"
    | "reactive_search_result"
    | "evidence_rated"
    | "synthesis_started"
    | "synthesis_complete"
    | "clarification_needed"
    | "followup_started"
    | "context_loaded"
    | "followup_search"
    | "followup_response_complete"
    | "error"

  title: string                        // e.g., "Searching knowledge vault for Iran sanctions"
  description: string | null
  status: "in_progress" | "complete" | "failed"
  durationMs: number | null            // null while in_progress

  payload: object | null               // event-type specific extras
}
```

**Suggested UI:** vertical timeline in the chat/reasoning panel. Each event = one row with icon + title + spinner/checkmark/X based on status + duration once complete. Renders in `sequence` order. Failed events get a red indicator with the error reason.

### 5.4 — Tier 1 vs Tier 2 sessions

Both tiers persist with full results. The `tier` field on `SessionResult` lets the frontend render slight differences:

- **Tier 1**: `tier: "tier_1"`, `canonicalKey` populated with market ID. `marketProbability` has a value. `MarketComparison` card shows real comparison data.
- **Tier 2**: `tier: "tier_2"`, `canonicalKey: null`, `marketProbability: null`. `MarketComparison` card either hides or shows a "no canonical market available" state.

---

## 6. V1 UX features

Four features confirmed for V1.

### 6.1 — Follow-up messages

User types a follow-up in the chat panel → agent responds with context from the original forecast.

**Flow:**
1. User sends follow-up via existing `POST /sessions/:id/messages` endpoint
2. Hub worker watches for new user messages on completed sessions
3. Agent loads parent session context (`SessionResult`, top `keyFactors`, top evidence, message history)
4. Agent runs a lightweight subgraph (no full vault re-search by default)
5. If follow-up clearly needs fresh evidence ("what about latest news?"), agent escalates to vault query / reactive search
6. Agent writes assistant message back to messages subcollection

**Budget:** ~5-7 seconds total (vs. 15-20s for main forecasts). Conversational, not exhaustive.

**Streaming:** Complete message for V1 (token-by-token streaming deferred — see section 7).

**Progress events:** Follow-ups also write to `agentEvents` subcollection but with `parentMessageId` set, so the existing reasoning panel works for follow-ups too.

### 6.2 — Plan limit handling

You already implemented this — just needs to be pushed to git.

**Required structured error response:**

```json
{
  "error": {
    "code": "PLAN_LIMIT_EXCEEDED",
    "message": "You've used your free forecasts this month",
    "details": {
      "used": 3,
      "limit": 3,
      "planTier": "free",
      "resetAt": "2026-12-01T00:00:00Z"
    }
  }
}
```

**Frontend handling:** catch `code === "PLAN_LIMIT_EXCEEDED"` → render modal with "You've used 3/3 free forecasts this month. Upgrade to Premium for unlimited." + CTA button to plan selection page.

If your existing implementation already returns this format, you're done. If it returns a string message, update to the structured format above.

### 6.3 — Clarification flow

When the agent finds 2-5 candidate canonical markets and isn't confident which the user meant, it pauses and asks instead of guessing.

**Flow:**

1. Agent's matching step finds multiple candidates with similar match scores → writes `session.status = "awaiting_clarification"` and populates `clarificationCandidates`:

```ts
type ClarificationCandidate = {
  id: string                       // canonical market id
  label: string                    // human-readable
  source: "polymarket" | "kalshi"
  description: string              // longer context (e.g., resolution criteria)
  matchConfidence: number          // 0-1
}
```

2. Agent writes a `clarification_needed` event to `agentEvents` and stops processing. No `SessionResult` yet.

3. Frontend Firestore listener sees `status === "awaiting_clarification"` → renders picker UI in the dashboard:

> **We found a few possible markets — which did you mean?**
>
> ◯ [candidate.label]  
>     *[candidate.source] — [candidate.description]*
>
> ◯ None of these — analyze as freeform

Candidates sorted by `matchConfidence` desc.

4. User picks → frontend calls **new endpoint** `POST /sessions/:id/clarify` with body `{ chosenCandidateId: string | null }` (null = freeform / Tier 2).

5. Express updates session: `status: 'queued'`, `canonicalKey` set (or null), writes new `forecastQueries` doc to re-trigger the agent.

6. Agent picks up re-queued session, sees `canonicalKey` is locked in, skips the matching step, proceeds with the full forecast pipeline.

**New endpoint to build:** `POST /sessions/:id/clarify`

**Frontend work:** small picker component (radio buttons + Analyze button) + handle new status in session-status routing. ~2-3 hours.

### 6.4 — Suggested Actions (simpler dynamic)

The hub generates 3 contextual follow-up suggestions per forecast. Frontend renders them as buttons in the chat panel.

**Schema** (already in section 5.1's `SessionResult.suggestedActions[]`):

```ts
type SuggestedAction = {
  id: string
  label: string                    // 3-7 words, clear over clever
  prompt: string                   // sent to follow-up endpoint on click
}
```

**Frontend rendering:** 3 buttons with one default icon for all (e.g., `→` or `💬`). On click, send the `prompt` field through the existing follow-up message endpoint (section 6.1).

**Note on "simpler dynamic":** The agent generates contextual suggestions but with a lighter prompt — clear labels over polished phrasing, no per-button icon variety yet. We'll iterate on quality post-V1 once we see how users actually interact with them.

---

## 7. Explicitly deferred (NOT in V1)

These are documented so neither side forgets them. They are not blocking.

| Feature | Why deferred | When to revisit |
|---|---|---|
| **Sentiment confidence bands rendering** | Hub writes `expertUpper`/`expertLower` from day 1; rendering is pure frontend Recharts work that can come later | When you have time / when users ask about uncertainty visualization |
| **Trending sidebar from Gold layer** | Currently bypasses pipeline (calls Polymarket directly). Refactoring it is purely cleanup with no user-visible benefit until edge cases bite | When agent's view of "trending" diverges meaningfully from sidebar |
| **Token-by-token streaming for follow-ups** | Complete message arrives all at once for V1. Streaming requires different infra than Firestore | When you want polish; existing UX is fine without it |
| **React Router** | Section 4.1 — works without it for V1, gets harder to retrofit later | Before you build any feature requiring shareable URLs |
| **TanStack Query refactor** | Section 4.2 — current pattern works, will strain when streaming chain-of-thought lands | When `App.tsx` state management starts feeling painful |
| **Agent-generated icons for Suggested Actions** | "Simpler dynamic" V1 uses one default icon for all | When polishing the UX |
| **Suggested Actions richness (full version)** | "Simpler dynamic" V1 is bare-bones | After data on user click-through tells us if/where to invest |

---

## 8. Suggested implementation order

A reasonable sequence to minimize integration friction:

### Phase A — Foundation (~3 hours)
Do these first; they're cheap and they unblock everything else.

1. Section 3.1 — Probability units standardization
2. Section 3.2 — `/api` prefix fix
3. Section 3.5 — Demo routes removal/hardening
4. Section 3.4 — Idempotency keys
5. Section 3.3 — Session status ownership (includes adding `clarificationCandidates` field)

### Phase B — V1 features (~4-6 hours)
6. Section 6.2 — Plan limit handling (push existing work + verify response format)
7. Section 6.3 — Clarification flow (new `POST /sessions/:id/clarify` endpoint + picker UI)
8. Frontend Firestore listeners on `agentEvents` subcollection (chain-of-thought rendering)

### Phase C — Once Phase A+B is in
At this point your work is done for V1. The hub team picks up — implementing the agent, writing schemas in 5.1-5.4, generating `suggestedActions[]`, handling follow-up messages, building clarification candidate matching, etc.

### Phase D — Optional polish (when you want)
- Section 4.1 — React Router
- Section 4.2 — TanStack Query
- Sentiment confidence band rendering
- Trending from Gold layer

---

## 9. Coordination points

Things that need ongoing sync between your work and the hub team. None of these are blocking right now; they're just things to keep in mind.

- **Confidence label thresholds** — section 5.1 lists proposed cutoffs (0.5, 0.8). If you have opinions, voice them now; otherwise we use these.
- **Follow-up budget enforcement** — hub enforces ~5-7s budget. If a follow-up consistently runs over, the user sees a partial response with degradation note. Frontend should handle the case where assistant message arrives with `status: "complete"` but content includes a "I had to stop early" caveat.
- **Clarification candidate count** — agent presents 2-5 candidates. If we ever want to expand to "show top N regardless of confidence," that's a future tweak.
- **Tier 2 UI treatment** — `MarketComparison.tsx` needs a small change to handle `marketProbability === null`. Either hide the card, or show an empty state ("No canonical market available — freeform analysis"). Your call on which.
- **Error state UI** — when session status flips to `failed`, the frontend shows the `errorMessage` and a "Retry forecast" button that calls `POST /sessions` with the original question + a fresh UUID v4 idempotency key. This creates a brand-new session; the failed one is preserved as an audit trail. See spec §8.7.6 for the full lifecycle. **Sprint 26 hardening:** retry has no per-error eligibility yet — permanent failures keep failing on retry until the error taxonomy ships.

---

## TL;DR for your AI assistant

> The Anizai project is integrating with a new AI reasoning service ("the agentic hub"). The hub writes results directly to Firestore using Firebase Admin SDK. The frontend reads via Firestore real-time listeners. No WebSocket needed. 
>
> Five small foundational changes are needed first (sections 3.1-3.5), totaling ~2.5 hours. Then four V1 features (section 6) that build on those foundations, totaling ~4-6 hours. The data contracts in section 5 are what the hub will write to — most fields already exist, the rest are additive.
>
> Two optional refactors (sections 4.1, 4.2) are nice-to-have and can wait. Section 7 lists explicitly deferred features so we don't accidentally build them in V1.

Implementation order in section 8. Coordination points in section 9.

Good luck — and ping back if anything's unclear 🚀
