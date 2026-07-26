import { describe, expect, it } from 'vitest';
import type { ChatMessage } from '../types';
import {
    FOLLOW_UP_STALL_MS,
    findPendingFollowUp,
    resolveFollowUpPendingState,
} from './followUpPending';

function msg(
    role: ChatMessage['role'],
    status: ChatMessage['status'],
    id = `${role}-${status}`
): ChatMessage {
    return { id, role, content: `${role} says something`, timestamp: new Date(1_000), status };
}

describe('findPendingFollowUp', () => {
    it('returns the trailing user message while it is optimistically pending', () => {
        const messages = [msg('user', 'answered', 'u1'), msg('assistant', 'sent', 'a1'), msg('user', 'pending', 'u2')];
        expect(findPendingFollowUp(messages)?.id).toBe('u2');
    });

    it('returns the trailing user message while it is persisted but unanswered', () => {
        expect(findPendingFollowUp([msg('user', 'sent', 'u1')])?.id).toBe('u1');
    });

    it('clears on the success path — the hub flips the user message to answered', () => {
        // The hub writes the assistant reply and flips sent -> answered in one
        // batch, so both land in the same listener push.
        const messages = [msg('user', 'answered', 'u1'), msg('assistant', undefined, 'a1')];
        expect(findPendingFollowUp(messages)).toBeNull();
    });

    it('clears on the error path — the hub marks the user message failed', () => {
        expect(findPendingFollowUp([msg('user', 'failed', 'u1')])).toBeNull();
    });

    it('ignores assistant messages when locating the pending follow-up', () => {
        // Only the latest *user* message decides; a trailing assistant message
        // must not mask an unanswered question before it.
        const messages = [msg('user', 'sent', 'u1'), msg('assistant', undefined, 'a1')];
        expect(findPendingFollowUp(messages)?.id).toBe('u1');
    });

    it('returns null for an empty thread', () => {
        expect(findPendingFollowUp([])).toBeNull();
    });

    it('returns null for a thread with no user messages at all', () => {
        expect(findPendingFollowUp([msg('assistant', undefined, 'a1')])).toBeNull();
    });

    it('considers only the most recent user message, not earlier ones', () => {
        const messages = [msg('user', 'sent', 'u1'), msg('user', 'answered', 'u2')];
        expect(findPendingFollowUp(messages)).toBeNull();
    });
});

describe('resolveFollowUpPendingState', () => {
    it('is idle when nothing is pending', () => {
        expect(resolveFollowUpPendingState(null, 10_000)).toBe('idle');
    });

    it('animates while the answer is still within the grace period', () => {
        expect(resolveFollowUpPendingState(0, FOLLOW_UP_STALL_MS - 1)).toBe('thinking');
    });

    it('animates immediately after sending', () => {
        expect(resolveFollowUpPendingState(5_000, 5_000)).toBe('thinking');
    });

    it('stalls once the grace period elapses, so the animation cannot spin forever', () => {
        expect(resolveFollowUpPendingState(0, FOLLOW_UP_STALL_MS)).toBe('stalled');
        expect(resolveFollowUpPendingState(0, FOLLOW_UP_STALL_MS * 100)).toBe('stalled');
    });

    it('stalls immediately when reopening a session whose follow-up was abandoned long ago', () => {
        // Age is measured from the message timestamp, not from mount, so a
        // months-old unanswered message does not animate for another 90s.
        const sentAt = Date.UTC(2026, 0, 1);
        const now = Date.UTC(2026, 5, 1);
        expect(resolveFollowUpPendingState(sentAt, now)).toBe('stalled');
    });

    it('does not read a future timestamp as stalled under clock skew', () => {
        expect(resolveFollowUpPendingState(10_000, 0)).toBe('thinking');
    });

    it('honours a custom grace period', () => {
        expect(resolveFollowUpPendingState(0, 500, 1_000)).toBe('thinking');
        expect(resolveFollowUpPendingState(0, 1_000, 1_000)).toBe('stalled');
    });
});
