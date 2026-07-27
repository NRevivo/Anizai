import type { ChatMessage } from '../types';

/**
 * How long a follow-up may sit unanswered before the UI stops showing a live
 * "thinking" animation and says so plainly instead.
 *
 * This exists because nothing guarantees an answer ever arrives. The hub flips
 * the triggering user message `sent -> answered` in the same batch as the reply,
 * and marks it `failed` when it gives up — both of those clear the pending state
 * on their own. But if the hub never responds at all the message stays at
 * `sent` in Firestore indefinitely (the documented deployed-agent-image gap), and
 * an animation with no timeout would spin forever, including across a reload.
 */
export const FOLLOW_UP_STALL_MS = 90_000;

/**
 * The trailing user message still waiting on an answer, or null if nothing is
 * pending.
 *
 * Scans backwards for the most recent user message: the hub answers one
 * follow-up at a time and flips that message's status in the same batch as the
 * reply, so its status is the whole story. `pending` is the client's optimistic
 * pre-POST state; `sent` is persisted and unanswered. `answered` and `failed`
 * both mean the hub is done with it.
 */
export function findPendingFollowUp(messages: ChatMessage[]): ChatMessage | null {
    for (let i = messages.length - 1; i >= 0; i--) {
        const message = messages[i];
        if (message.role === 'user') {
            return message.status === 'pending' || message.status === 'sent' ? message : null;
        }
    }

    return null;
}

export type FollowUpPendingState = 'idle' | 'thinking' | 'stalled';

/**
 * Map a pending follow-up's age onto what the chat panel should render.
 *
 * `idle` — nothing pending; render no indicator at all.
 * `thinking` — animated dots.
 * `stalled` — static notice, no animation. Reached when the answer has not
 *   arrived within `stallAfterMs`.
 *
 * Age is measured from the message timestamp rather than from when the
 * component mounted, so reopening a session whose follow-up was abandoned long
 * ago reports `stalled` immediately instead of animating for another full
 * grace period.
 */
export function resolveFollowUpPendingState(
    pendingSinceMs: number | null,
    nowMs: number,
    stallAfterMs: number = FOLLOW_UP_STALL_MS
): FollowUpPendingState {
    if (pendingSinceMs === null) {
        return 'idle';
    }

    // A clock skew that puts the message in the future must not read as stalled.
    return nowMs - pendingSinceMs >= stallAfterMs ? 'stalled' : 'thinking';
}
