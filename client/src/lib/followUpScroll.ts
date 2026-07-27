/**
 * Auto-scroll policy for the follow-up thread.
 *
 * The decision is separated from the DOM so it can be unit-tested — the
 * dashboard sits behind auth, so this is the part of the behaviour provable
 * without a signed-in session (same reasoning as [[followUpComposer]] and
 * [[followUpPending]]).
 */

/**
 * How far from the bottom the thread may be scrolled and still count as
 * "following along".
 *
 * Sized to absorb sub-pixel rounding and a partially-visible last line without
 * swallowing a deliberate scroll up: one chat bubble is ~60px, so a user who
 * has scrolled back even a single message is past this and is left alone.
 */
export const AUTO_SCROLL_THRESHOLD_PX = 64;

/** The scroll geometry this module needs — a structural subset of Element. */
export type ScrollGeometry = {
    scrollHeight: number;
    scrollTop: number;
    clientHeight: number;
};

/**
 * Pixels between the current viewport bottom and the end of the content.
 *
 * Clamped at 0: browsers report a slightly over-scrolled `scrollTop` during
 * momentum/elastic scrolling, which would otherwise produce a negative
 * distance and read as "far from the bottom".
 */
export function getDistanceFromBottom(geometry: ScrollGeometry): number {
    const distance = geometry.scrollHeight - geometry.scrollTop - geometry.clientHeight;

    return distance > 0 ? distance : 0;
}

/** Whether the thread is close enough to the bottom to keep following it. */
export function isNearBottom(
    distanceFromBottom: number,
    threshold: number = AUTO_SCROLL_THRESHOLD_PX
): boolean {
    return distanceFromBottom <= threshold;
}

export type AutoScrollReason =
    /** The user just sent this message — always follow it, wherever they are. */
    | 'own-message'
    /** They were already at the bottom, so keep them there. */
    | 'pinned'
    /** They have scrolled up to read history — do not yank them down. */
    | 'suppressed';

export type AutoScrollDecision = {
    scroll: boolean;
    reason: AutoScrollReason;
};

/**
 * Decide whether an update to the thread should scroll it to the bottom.
 *
 * `isPinnedToBottom` must reflect where the user was **before** this update
 * rendered. Measuring after the fact always reports "far from the bottom" —
 * the new content is precisely what pushed the bottom away — which would
 * suppress every scroll after the first.
 *
 * A user's own message overrides the suppression: they just acted, so following
 * their message is what they expect even if they had scrolled up to re-read
 * something before sending.
 */
export function decideAutoScroll(params: {
    isPinnedToBottom: boolean;
    isOwnNewMessage: boolean;
}): AutoScrollDecision {
    if (params.isOwnNewMessage) {
        return { scroll: true, reason: 'own-message' };
    }

    return params.isPinnedToBottom
        ? { scroll: true, reason: 'pinned' }
        : { scroll: false, reason: 'suppressed' };
}

/**
 * Pick the scroll behaviour for a jump.
 *
 * The first paint of a session is always instant: animating from the top is
 * the "scroll-from-top flash" this feature exists to remove. After that,
 * motion is smooth unless the user asked for less of it.
 */
export function resolveScrollBehavior(params: {
    isInitial: boolean;
    prefersReducedMotion: boolean;
}): ScrollBehavior {
    if (params.isInitial || params.prefersReducedMotion) {
        return 'auto';
    }

    return 'smooth';
}
