# Hub Handoff Summary - Task 16

<!-- archive-banner -->
> ⚠️ **SUPERSEDED — contains inaccuracies.** Historical record only; do not
> cite as current. Corrected content: [`frontend_contracts.md`](../C_frontend/frontend_contracts.md) §3–§4.
> Why this doc is wrong: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §2.

## 1. New Session Statuses
- Supported session statuses across client and server:
  - `queued`
  - `claimed`
  - `running`
  - `done`
  - `failed`
  - `awaiting_clarification`

## 2. POST /sessions Idempotency Requirement
- `POST /sessions` requires `idempotencyKey`
- Backend validates it as a non-empty UUID
- Duplicate submits within the recent lookup window return the existing session instead of creating a second one

## 3. POST /sessions/:id/clarify Endpoint
- Endpoint exists: `POST /sessions/:id/clarify`
- Body:
  - `{ "chosenCandidateId": string | null }`
- Server verifies session ownership and requires session status `awaiting_clarification`
- Valid selections re-queue the session as `queued`

## 4. Expected Firestore Fields
- Session documents are expected to support:
  - `status`
  - `errorMessage`
  - `clarificationCandidates`
  - `canonicalKey`
  - `latestProbability`
  - `latestConfidence`
  - `createdAt`
  - `updatedAt`
  - `lastActivityAt`
- Session message documents are expected to support:
  - `userId`
  - `role`
  - `content`
  - `createdAt`
  - `status`
  - `meta`
- Agent event documents are expected under `sessions/{sessionId}/agentEvents`

## 5. forecastQueries Behavior
- New session creation writes one `forecastQueries` document for the new request
- Clarification submit writes a new `forecastQueries` document when the session is re-queued
- Idempotent duplicate submit should not create a second `forecastQueries` document for the same request attempt

## 6. SessionResult Schema Supported
- Existing fields preserved:
  - `finalProbability`
  - `confidence`
  - `confidenceLabel`
  - `consensusStrength`
  - `evidenceVolumeLabel`
  - `bottomLineAnswer`
  - `detailedExplanation`
  - `summaryMarkdown`
  - `marketProbability`
  - `marketComparisonInsight`
  - `sentimentAnalysisInsight`
  - `evidenceFeedSummary`
  - `marketComparison`
- New supported fields:
  - `keyFactors`
  - `whatIDidntFind`
  - `reasoningChain`
  - `suggestedActions`
  - `generatedAt`
  - `agentVersion`
  - `tier`

## 7. Evidence Schema Supported
- Existing fields preserved:
  - `type`
  - `impact`
  - `impactLabel`
  - `isKeyEvidence`
  - `title`
  - `source`
  - `timestamp`
  - `url`
- New supported fields:
  - `evidenceId`
  - `sourceType`
  - `origin`
  - `sourceDomain`
  - `snippet`
  - `fetchedAt`
  - `relevanceScore`
  - `credibilityTier`
  - `recencyWeight`
  - `usedInAnswer`
  - `impactOnForecast`
  - `justification`
  - `rank`

## 8. agentEvents Schema Supported
- Client supports `AgentEvent` with:
  - `eventId`
  - `sessionId`
  - `sequence`
  - `timestamp`
  - `parentMessageId`
  - `type`
  - `title`
  - `description`
  - `status`
  - `durationMs`
  - `payload`

## 9. Tier 1 / Tier 2 Behavior
- `tier_1`:
  - normal market comparison behavior
  - market benchmark expected when available
- `tier_2`:
  - safe even when `marketProbability` is `null`
  - UI shows a compact no-benchmark state instead of misleading comparison math
- Missing `tier` on old sessions is handled safely

## 10. Plan Limit Error Format
- Backend/frontend support structured plan-limit errors in the shape:
  - `error.code = "PLAN_LIMIT_EXCEEDED"`
  - `error.message`
  - `error.details.used`
  - `error.details.limit`
  - `error.details.planTier`
  - `error.details.resetAt`

## 11. Follow-up Message Behavior
- Follow-ups use existing `POST /sessions/:id/messages`
- User messages are written into the session messages subcollection
- Frontend listens to session messages live
- User messages render immediately
- Assistant responses written by the hub render when they appear in Firestore

## 12. Suggested Actions Behavior
- `SessionResult.suggestedActions` renders up to 3 action buttons
- Clicking a suggested action sends its `prompt` through the existing follow-up message flow
- No separate endpoint was added

## 13. Remaining Limitations / Known Issues
- Client lint remains blocked by root ESLint dependency resolution for `@eslint/js`
- No client test runner is currently wired in `client/package.json`
- Clarification picker is implemented as a minimal V1 flow, not a deeper guided workflow
- Retry for failed sessions creates a fresh session request rather than mutating the failed one
