# Suggested Actions - Task 12

<!-- archive-banner -->
> 📁 **Historical change log.** Accurate for the change it describes;
> superseded as current documentation by [`frontend_ui.md §5.3`](../C_frontend/frontend_ui.md).
> Index: [`frontend_archive.md`](../C_frontend/frontend_archive.md) §3.

## 1. Summary

This task connects `SessionResult.suggestedActions` to the existing follow-up UI.

When a result includes suggested actions, the chat panel now renders up to three compact action buttons. Clicking a button sends that action's `prompt` through the existing follow-up message flow instead of creating any new endpoint or special-case messaging path.

## 2. Files Changed

- `client/src/components/ChatPanel.tsx`
  - Renders suggested-action buttons with a simple shared icon.
  - Passes the full action object on click.
- `client/src/pages/DashboardPage.tsx`
  - Uses `prediction.suggestedActions` as the source of truth.
  - Limits rendering to three actions.
  - Sends `suggestedAction.prompt` through the existing `onSendMessage` flow.

## 3. UI Behavior

- If `session.result.suggestedActions` exists and has items:
  - show up to 3 compact buttons
  - button label uses `suggestedAction.label`
  - buttons use one simple shared icon
- If `suggestedActions` is missing or empty:
  - hide the section entirely
  - no empty state is shown

## 4. Click / Send Behavior

- Clicking a suggested action sends:
  - `suggestedAction.prompt`
- It uses the existing:
  - `POST /sessions/:id/messages` flow
  - message refresh behavior
  - app-level follow-up error handling
- No new API endpoint was added.

## 5. Validation Results

Commands run:

- `git status --short`
- `cd client`
- `npx tsc -p tsconfig.app.json --noEmit --pretty false`
- `npm run lint`

Results:

- TypeScript: passed
- Lint: failed only because root `eslint.config.js` cannot resolve `@eslint/js`

## 6. Risks / Notes

- Old sessions remain safe because the buttons are hidden when result actions are absent.
- The current implementation assumes suggested actions include a `prompt`; actions without one are ignored safely.
- This task does not change message endpoint behavior, loading flow, or error contract behavior.
