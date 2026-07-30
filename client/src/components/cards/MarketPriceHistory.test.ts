import { describe, expect, it } from 'vitest';
import { emptyStateFor } from './MarketPriceHistory';

/**
 * An empty `predictionSeries` is three different outcomes wearing one shape.
 * Getting the copy wrong here is not cosmetic: telling a user "no market
 * benchmark" when the market matched fine and only the history fetch degraded
 * misattributes a transient failure to their question.
 */
describe('emptyStateFor', () => {
    it('tier_2 says there is no market at all', () => {
        const state = emptyStateFor('tier_2');
        expect(state.title).toBe('No market benchmark');
        expect(state.description).toMatch(/freeform/i);
    });

    it('tier_1 says the market matched and only the history is missing', () => {
        // The case that previously rendered as "no price history was recorded",
        // which reads as "this market has none" rather than "we could not fetch
        // it" — and which would otherwise render an empty chart frame.
        const state = emptyStateFor('tier_1');
        expect(state.title).toBe('Market matched, history unavailable');
        expect(state.description).toMatch(/matched to a market/i);
        // Must reassure that the forecast itself still stands.
        expect(state.description).toMatch(/unaffected/i);
    });

    it('stays neutral when the tier is unknown', () => {
        // Claiming "market matched" without a tier would assert something
        // unverified.
        const state = emptyStateFor(null);
        expect(state.title).toBe('No price history');
        expect(state.description).not.toMatch(/matched/i);
        expect(state.description).not.toMatch(/freeform/i);
    });

    it('gives all three outcomes distinct copy', () => {
        const titles = (['tier_1', 'tier_2', null] as const).map((t) => emptyStateFor(t).title);
        expect(new Set(titles).size).toBe(3);
    });
});
