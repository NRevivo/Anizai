import { describe, expect, it } from 'vitest';
import {
    AUTO_SCROLL_THRESHOLD_PX,
    decideAutoScroll,
    getDistanceFromBottom,
    isNearBottom,
    resolveScrollBehavior,
} from './followUpScroll';

describe('getDistanceFromBottom', () => {
    it('is zero when scrolled fully to the bottom', () => {
        expect(getDistanceFromBottom({ scrollHeight: 1000, scrollTop: 800, clientHeight: 200 })).toBe(0);
    });

    it('measures the gap when scrolled up', () => {
        expect(getDistanceFromBottom({ scrollHeight: 1000, scrollTop: 300, clientHeight: 200 })).toBe(500);
    });

    it('is zero when the content is shorter than the viewport', () => {
        expect(getDistanceFromBottom({ scrollHeight: 120, scrollTop: 0, clientHeight: 400 })).toBe(0);
    });

    it('clamps elastic over-scroll to zero rather than reporting a negative gap', () => {
        // Momentum scrolling on macOS/iOS reports scrollTop past the maximum.
        expect(getDistanceFromBottom({ scrollHeight: 1000, scrollTop: 850, clientHeight: 200 })).toBe(0);
    });

    it('reports zero for a hidden container, where all metrics are zero', () => {
        // ChatPanel is mounted three times; the off-breakpoint copies are
        // display:none and must not throw or produce nonsense.
        expect(getDistanceFromBottom({ scrollHeight: 0, scrollTop: 0, clientHeight: 0 })).toBe(0);
    });
});

describe('isNearBottom', () => {
    it('treats the exact bottom as near', () => {
        expect(isNearBottom(0)).toBe(true);
    });

    it('treats the threshold itself as still near (inclusive)', () => {
        expect(isNearBottom(AUTO_SCROLL_THRESHOLD_PX)).toBe(true);
    });

    it('treats one pixel past the threshold as scrolled away', () => {
        expect(isNearBottom(AUTO_SCROLL_THRESHOLD_PX + 1)).toBe(false);
    });

    it('treats a full message scrolled back as scrolled away', () => {
        expect(isNearBottom(400)).toBe(false);
    });

    it('honours a custom threshold', () => {
        expect(isNearBottom(100, 200)).toBe(true);
        expect(isNearBottom(300, 200)).toBe(false);
    });
});

describe('decideAutoScroll', () => {
    it('scrolls when the user is already at the bottom', () => {
        expect(decideAutoScroll({ isPinnedToBottom: true, isOwnNewMessage: false })).toEqual({
            scroll: true,
            reason: 'pinned',
        });
    });

    it('suppresses when the user has scrolled up to read history', () => {
        expect(decideAutoScroll({ isPinnedToBottom: false, isOwnNewMessage: false })).toEqual({
            scroll: false,
            reason: 'suppressed',
        });
    });

    it("always follows the user's own message, even when they had scrolled up", () => {
        expect(decideAutoScroll({ isPinnedToBottom: false, isOwnNewMessage: true })).toEqual({
            scroll: true,
            reason: 'own-message',
        });
    });

    it('reports own-message as the reason even when they were also pinned', () => {
        expect(decideAutoScroll({ isPinnedToBottom: true, isOwnNewMessage: true })).toEqual({
            scroll: true,
            reason: 'own-message',
        });
    });

    it('suppresses an incoming assistant reply while the user reads history', () => {
        // The thinking indicator and the answer that replaces it both arrive as
        // non-own updates, so both are suppressed by the same rule.
        expect(decideAutoScroll({ isPinnedToBottom: false, isOwnNewMessage: false }).scroll).toBe(false);
    });
});

describe('resolveScrollBehavior', () => {
    it('is instant on the first paint of a session, to avoid a scroll-from-top flash', () => {
        expect(resolveScrollBehavior({ isInitial: true, prefersReducedMotion: false })).toBe('auto');
    });

    it('is smooth for later updates', () => {
        expect(resolveScrollBehavior({ isInitial: false, prefersReducedMotion: false })).toBe('smooth');
    });

    it('is instant for later updates when the user prefers reduced motion', () => {
        expect(resolveScrollBehavior({ isInitial: false, prefersReducedMotion: true })).toBe('auto');
    });

    it('stays instant on the initial jump regardless of the motion preference', () => {
        expect(resolveScrollBehavior({ isInitial: true, prefersReducedMotion: true })).toBe('auto');
    });
});
