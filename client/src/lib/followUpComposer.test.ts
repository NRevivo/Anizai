import { describe, expect, it } from 'vitest';
import type { SessionStatus } from '../types';
import { shouldShowFollowUpComposer } from './followUpComposer';

describe('shouldShowFollowUpComposer', () => {
    it('shows the composer on a done session that has a result', () => {
        expect(shouldShowFollowUpComposer('done', true)).toBe(true);
    });

    it('hides the composer on a brand-new session with no result yet', () => {
        expect(shouldShowFollowUpComposer('queued', false)).toBe(false);
    });

    it.each<SessionStatus>(['queued', 'claimed', 'running'])(
        'hides the composer while the forecast is in progress (%s)',
        (status) => {
            expect(shouldShowFollowUpComposer(status, false)).toBe(false);
        }
    );

    it('hides the composer on a failed session', () => {
        expect(shouldShowFollowUpComposer('failed', false)).toBe(false);
    });

    it('hides the composer while awaiting clarification', () => {
        expect(shouldShowFollowUpComposer('awaiting_clarification', false)).toBe(false);
    });

    it('hides the composer when status is done but the result doc is missing', () => {
        // The BFF returns result: null whenever sessionResults/{id} is absent,
        // independent of session status — status alone is not sufficient.
        expect(shouldShowFollowUpComposer('done', false)).toBe(false);
    });

    it.each<SessionStatus>(['queued', 'claimed', 'running', 'awaiting_clarification', 'failed'])(
        'hides the composer when a stale result outlives a re-queued run (%s)',
        (status) => {
            // The clarification path re-queues an existing session, so a result
            // from a previous run can still be loaded — hasForecastResult alone
            // is not sufficient either.
            expect(shouldShowFollowUpComposer(status, true)).toBe(false);
        }
    );

    it('hides the composer when there is no active session at all', () => {
        expect(shouldShowFollowUpComposer(null, false)).toBe(false);
        expect(shouldShowFollowUpComposer(undefined, false)).toBe(false);
    });
});
