/**
 * DOM-facing helpers shared by the auto-scrolling surfaces — the follow-up
 * thread (`ChatPanel`) and the agent timeline (`AgentEventsTimeline`).
 *
 * The scroll *policy* (threshold, pinned-ness, behaviour) lives in
 * [[followUpScroll]] and is shared by both. What differs between the two is how
 * the scroll container is found, which is what this module covers.
 */

/**
 * Whether a computed `overflow-y` value makes an element a scroll container.
 *
 * `overlay` is a legacy WebKit value that still behaves as a scroll container.
 * `clip` deliberately does not: it forbids scrolling entirely, programmatic
 * included.
 *
 * This matters more than it looks. The dashboard's centre panel is written as
 * `overflow-x-hidden` with no `overflow-y` utility at all, but CSS forces the
 * other axis away from `visible` when one axis is not `visible` — so it
 * computes to `overflow-y: auto` and silently *is* the scroll container.
 * Verified in a browser against the real class chain.
 */
export function isScrollableOverflow(overflowY: string): boolean {
    return overflowY === 'auto' || overflowY === 'scroll' || overflowY === 'overlay';
}

/**
 * The nearest ancestor that actually scrolls, or `null` if there is none.
 *
 * Returns `null` rather than falling back to the document or window: nothing in
 * the dashboard should ever scroll the page. The shell is `h-screen
 * overflow-hidden`, so the document has no scroll range anyway, and quietly
 * defaulting to it would be exactly the "moves the whole page" failure that
 * `scrollIntoView` causes.
 *
 * Stops at `<body>` for the same reason.
 */
export function findScrollableAncestor(element: HTMLElement | null): HTMLElement | null {
    let candidate = element?.parentElement ?? null;

    while (candidate && candidate !== document.body) {
        if (isScrollableOverflow(window.getComputedStyle(candidate).overflowY)) {
            return candidate;
        }

        candidate = candidate.parentElement;
    }

    return null;
}

/**
 * Whether the user has asked for reduced motion.
 *
 * Read at call time rather than cached in state, so the value is always current
 * without a change listener to clean up. Guarded for non-browser environments
 * so it is callable from tests.
 */
export function prefersReducedMotion(): boolean {
    if (typeof window === 'undefined') {
        return false;
    }

    return window.matchMedia?.('(prefers-reduced-motion: reduce)').matches === true;
}
