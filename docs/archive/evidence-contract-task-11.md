# Evidence Contract - Task 11

<!-- archive-banner -->
> ⚠️ **SUPERSEDED — contains inaccuracies.** Historical record only; do not
> cite as current. Corrected content: [`frontend_contracts.md`](../C_frontend/frontend_contracts.md) §3.5, §5.2.
> Why this doc is wrong: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §2.

## 1. Summary

This task extends the evidence data contract with new hub fields while keeping the current evidence UI compact and backward-compatible.

The raw evidence shape now carries richer optional metadata, and the timeline view maps that into the existing evidence feed without breaking old items, current filters, or empty states.

## 2. Files Changed

- `client/src/App.tsx`
  - Maps richer evidence data into the timeline view model.
  - Normalizes `sourceType` into existing UI categories.
- `client/src/components/cards/EvidenceTimeline.tsx`
  - Renders optional source domain, snippet, relevance, credibility, and justification when present.
- `client/src/services/session.service.ts`
  - Extends the client evidence contract and adds minimal demo metadata.
- `client/src/types/index.ts`
  - Extends the timeline evidence view model with optional metadata fields.
- `server/src/repositories/session.repository.ts`
  - Maps the new evidence fields from Firestore documents into API responses.
- `server/src/services/sessions.service.ts`
  - Extends the backend evidence contract types.

## 3. New Evidence Fields

Added optional/backward-compatible fields:

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

Existing fields preserved:

- `type`
- `impact`
- `impactLabel`
- `isKeyEvidence`
- `title`
- `source`
- `timestamp`
- `url`

## 4. SourceType Mapping

The UI normalizes specific hub `sourceType` values into the existing evidence categories:

- `vault_news` / `online_news` -> `news`
- `vault_telegram` / `online_blog` -> `social`
- `vault_hackernews` -> `social`
- `vault_arxiv` -> `expert`
- `vault_market` / `vault_fred` -> `market`

Fallback behavior:

- if `sourceType` is missing, the UI falls back to the existing `type`

## 5. UI Rendering Behavior

The evidence UI remains compact.

When present, each evidence item can now show:

- `sourceDomain`
- fallback `source`
- `snippet` instead of the older generic description when available
- `relevanceScore`
- `credibilityTier`
- `justification`

Display rules:

- optional fields render only when present
- old evidence items still render with the existing title/date/impact structure
- filters and empty states remain intact

## 6. Backward Compatibility Notes

- Old evidence items do not crash because all new fields are optional in the UI layer.
- Server mapping falls back to `null` for optional evidence metadata.
- Existing evidence `type` remains preserved.
- Probability handling is unchanged.

## 7. Validation Results

Commands run:

- `git status --short`
- `cd client`
- `npx tsc -p tsconfig.app.json --noEmit --pretty false`
- `npm run lint`

Results:

- TypeScript: passed
- Lint: failed only because root `eslint.config.js` cannot resolve `@eslint/js`

## 8. Risks / Notes

- The evidence feed now understands a richer `sourceType` vocabulary from the hub while still rendering through the existing compact categories.
- Optional metadata is intentionally secondary so the timeline does not become overcrowded.
- Market evidence now remains distinguishable when that source type is present.
