import { describe, expect, it } from 'vitest';
import type { AgentEvent } from '../types';
import { selectCurrentRunEvents } from './agentEvents';

// Minimal AgentEvent factory — only the fields Rule B cares about matter here
// (runId, sequence); the rest are filled with inert defaults.
function evt(runId: string | null, sequence: number, eventId = `${runId}-${sequence}`): AgentEvent {
    return {
        eventId,
        sessionId: 'session-1',
        runId,
        sequence,
        timestamp: new Date(0),
        parentMessageId: null,
        type: 'vault_query',
        title: `event ${eventId}`,
        description: null,
        status: 'done',
        durationMs: null,
        payload: null,
    };
}

describe('selectCurrentRunEvents (Rule B)', () => {
    it('returns only the current run, ordered by sequence, from an interleaved out-of-order array', () => {
        // Two runs interleaved and deliberately unsorted.
        const events: AgentEvent[] = [
            evt('run-B', 1),
            evt('run-A', 2),
            evt('run-B', 0),
            evt('run-A', 0),
            evt('run-A', 1),
            evt('run-B', 2),
        ];

        const result = selectCurrentRunEvents(events, 'run-A');

        expect(result.map((e) => e.eventId)).toEqual(['run-A-0', 'run-A-1', 'run-A-2']);
        expect(result.map((e) => e.sequence)).toEqual([0, 1, 2]);
        // No cross-run leakage.
        expect(result.every((e) => e.runId === 'run-A')).toBe(true);
    });

    it('returns [] when currentRunId is null (hub has not started a run)', () => {
        const events: AgentEvent[] = [evt('run-A', 0), evt('run-A', 1)];
        expect(selectCurrentRunEvents(events, null)).toEqual([]);
    });

    it('excludes events whose runId is null or does not match', () => {
        const events: AgentEvent[] = [evt(null, 0), evt('run-A', 1), evt('run-Z', 2)];
        const result = selectCurrentRunEvents(events, 'run-A');
        expect(result.map((e) => e.eventId)).toEqual(['run-A-1']);
    });

    it('returns [] when no event matches the current run', () => {
        const events: AgentEvent[] = [evt('run-A', 0), evt('run-B', 1)];
        expect(selectCurrentRunEvents(events, 'run-C')).toEqual([]);
    });
});
