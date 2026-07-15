import type { AgentEvent } from '../types';

/**
 * Rule B (Sprint 25 · T6): from the full agentEvents subcollection, return only
 * the events belonging to the session's live run — those whose `runId` matches
 * the session doc's `currentRunId` — ordered by `sequence` ascending.
 *
 * Until the hub sets `currentRunId` there is no live run, so nothing renders
 * (empty array). Events with a null/other `runId` are never included.
 *
 * The sequence sort is owned here rather than only inherited from the Firestore
 * query order, so the ordering guarantee is self-contained and unit-testable.
 */
export function selectCurrentRunEvents(
    events: AgentEvent[],
    currentRunId: string | null
): AgentEvent[] {
    if (!currentRunId) {
        return [];
    }

    return events
        .filter((event) => event.runId === currentRunId)
        .sort((a, b) => a.sequence - b.sequence);
}
