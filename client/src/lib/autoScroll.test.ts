import { afterEach, describe, expect, it } from 'vitest';
import { isScrollableOverflow, prefersReducedMotion } from './autoScroll';

describe('isScrollableOverflow', () => {
    it('treats auto as scrollable', () => {
        expect(isScrollableOverflow('auto')).toBe(true);
    });

    it('treats scroll as scrollable', () => {
        expect(isScrollableOverflow('scroll')).toBe(true);
    });

    it('treats the legacy overlay value as scrollable', () => {
        expect(isScrollableOverflow('overlay')).toBe(true);
    });

    it('treats visible as not scrollable', () => {
        expect(isScrollableOverflow('visible')).toBe(false);
    });

    it('treats hidden as not scrollable', () => {
        // The dashboard's centre column is overflow-hidden; walking up for a
        // scroll container must skip it rather than stop there.
        expect(isScrollableOverflow('hidden')).toBe(false);
    });

    it('treats clip as not scrollable, since it forbids programmatic scrolling too', () => {
        expect(isScrollableOverflow('clip')).toBe(false);
    });

    it('rejects an unrecognised value rather than guessing', () => {
        expect(isScrollableOverflow('')).toBe(false);
    });
});

// The suite runs in the `node` environment (vitest.config.ts), so there is no
// window unless a test provides one. Assigning onto globalThis is what makes
// the bare `window` reference inside the module resolve.
// Cast through `unknown`: the DOM lib types `globalThis.window` as a full,
// non-optional `Window`, which a partial stub can neither satisfy nor delete.
type WindowStub = { matchMedia?: (query: string) => { matches: boolean } };
const globalWithWindow = globalThis as unknown as { window?: WindowStub };

describe('prefersReducedMotion', () => {
    afterEach(() => {
        delete globalWithWindow.window;
    });

    it('is true when the media query matches', () => {
        globalWithWindow.window = { matchMedia: () => ({ matches: true }) };
        expect(prefersReducedMotion()).toBe(true);
    });

    it('is false when the media query does not match', () => {
        globalWithWindow.window = { matchMedia: () => ({ matches: false }) };
        expect(prefersReducedMotion()).toBe(false);
    });

    it('defaults to false when matchMedia is unavailable', () => {
        // Older browsers: motion is the safe default — the caller only
        // downgrades to instant on an explicit preference.
        globalWithWindow.window = {};
        expect(prefersReducedMotion()).toBe(false);
    });

    it('defaults to false when there is no window at all', () => {
        expect(prefersReducedMotion()).toBe(false);
    });
});
