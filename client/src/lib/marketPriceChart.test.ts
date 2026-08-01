import { describe, expect, it } from 'vitest';
import {
    buildMarketPriceChart,
    buildTimeTicks,
    formatStamp,
    niceTicks,
    MIN_AXIS_SPAN_PCT,
} from './marketPriceChart';
import type { MarketPricePoint } from '../types';

const HOUR = 3_600_000;
const DAY = 86_400_000;
const T0 = Date.UTC(2026, 6, 1, 12, 0, 0);

function series(...probabilities: number[]): MarketPricePoint[] {
    return probabilities.map((probability, index) => ({
        t: T0 + index * HOUR, // hourly resolution, the real cadence
        probability,
    }));
}

describe('buildMarketPriceChart', () => {
    it('returns null for an empty series', () => {
        // Covers both "never written" (freeform) and "written but empty" — the
        // caller tells them apart by tier, not by this.
        expect(buildMarketPriceChart([], 0.5)).toBeNull();
    });

    it('converts 0-1 probabilities to percent', () => {
        // closeTo, not toEqual: 0.58 * 100 is 57.99999999999999 in binary float.
        // Both render sites round (`Math.round`, `toFixed(1)`), so the residue is
        // never visible — asserting exact equality would test the FPU, not us.
        const chart = buildMarketPriceChart(series(0.42, 0.58), 0.5);
        expect(chart?.data[0].pct).toBeCloseTo(42, 10);
        expect(chart?.data[1].pct).toBeCloseTo(58, 10);
        expect(chart?.anizaiPct).toBeCloseTo(50, 10);
    });

    it('reports first, last and span from the ends of the series', () => {
        const chart = buildMarketPriceChart(series(0.3, 0.4, 0.5), 0.5);
        expect(chart?.first.pct).toBe(30);
        expect(chart?.last.pct).toBe(50);
        expect(chart?.spanMs).toBe(2 * HOUR);
    });

    it('keeps a flat market from looking volatile', () => {
        // 54%-56% is a genuinely still market. An auto-fitted axis would spread
        // 2 points across the full height and read as a dramatic swing.
        const chart = buildMarketPriceChart(series(0.54, 0.55, 0.56), 0.55);
        const [min, max] = chart!.domain;
        expect(max - min).toBeGreaterThanOrEqual(MIN_AXIS_SPAN_PCT);
    });

    it('lets a genuinely large move fill the chart', () => {
        // A 70-point range must NOT be padded out to near-full scale; the
        // minimum span is a floor, not a target.
        const chart = buildMarketPriceChart(series(0.1, 0.8), 0.45);
        const [min, max] = chart!.domain;
        // Contains the data with a little air, and is not just reset to full
        // scale — the minimum span is a floor, not a target.
        expect(min).toBeLessThanOrEqual(10);
        expect(max).toBeGreaterThanOrEqual(80);
        expect(max - min).toBeLessThanOrEqual(90);
    });

    it('never leaves the 0-100 range', () => {
        // A market pinned at ~1% would otherwise pad to a negative axis.
        const low = buildMarketPriceChart(series(0.01, 0.02), 0.01);
        expect(low!.domain[0]).toBeGreaterThanOrEqual(0);

        const high = buildMarketPriceChart(series(0.99, 0.98), 0.99);
        expect(high!.domain[1]).toBeLessThanOrEqual(100);
    });

    it('includes the Anizai reference line inside the domain', () => {
        // The forecast sits far off the market's own range. If the domain were
        // fitted to the market alone the reference line would render outside the
        // plot area and vanish — losing the only comparison the card makes.
        const chart = buildMarketPriceChart(series(0.9, 0.92), 0.2);
        const [min, max] = chart!.domain;
        expect(min).toBeLessThanOrEqual(20);
        expect(max).toBeGreaterThanOrEqual(92);
    });

    it('handles a single point without collapsing the axis', () => {
        const chart = buildMarketPriceChart(series(0.5), 0.5);
        expect(chart?.data).toHaveLength(1);
        expect(chart?.spanMs).toBe(0);
        const [min, max] = chart!.domain;
        expect(max).toBeGreaterThan(min);
    });

    it('handles the expected production density', () => {
        // ~683 points at hourly resolution over a rolling ~30-day window — NOT
        // the market's lifetime. A market open for a year still shows one month.
        const probabilities = Array.from({ length: 683 }, (_, i) => 0.3 + (i / 683) * 0.4);
        const chart = buildMarketPriceChart(series(...probabilities), 0.5);
        expect(chart?.data).toHaveLength(683);
        expect(chart?.spanMs).toBe(682 * HOUR);
        // ~28.4 days — the window, not an inception-to-now span.
        expect(chart!.spanMs / DAY).toBeGreaterThan(27);
        expect(chart!.spanMs / DAY).toBeLessThan(31);
    });

    it('produces a legible axis at the real 30-day hourly shape', () => {
        // Re-checking both previously-fixed axis bugs at the corrected shape,
        // since both were found at 10-minute spacing over ~5 days.
        const probabilities = Array.from({ length: 683 }, (_, i) => 0.42 + Math.sin(i / 90) * 0.06);
        const chart = buildMarketPriceChart(series(...probabilities), 0.5)!;

        const labels = chart.xTicks.map((tick) => tick.label);
        expect(new Set(labels).size).toBe(labels.length);

        const gaps = chart.yTicks.slice(1).map((v, i) => v - chart.yTicks[i]);
        expect(new Set(gaps).size).toBe(1);
        expect(chart.yTicks.every((v) => Number.isInteger(v))).toBe(true);
    });

    it('keeps labels distinct for a market younger than the window', () => {
        // The window is a ceiling, not a guarantee: a market opened 3 days ago
        // yields ~72 points over 3 days. That is just past the 2-day clock
        // threshold, so several ticks share a date and the dedupe must engage.
        const probabilities = Array.from({ length: 72 }, (_, i) => 0.5 + i * 0.001);
        const chart = buildMarketPriceChart(series(...probabilities), 0.5)!;
        const labels = chart.xTicks.map((tick) => tick.label);
        expect(new Set(labels).size).toBe(labels.length);
    });
});

describe('niceTicks', () => {
    it('spaces ticks evenly on a round step', () => {
        // The bug this replaces: recharts fitting an arbitrary domain produced
        // 23 / 43 / 63 / 90 — three even gaps and one odd one.
        const ticks = niceTicks(20, 90);
        const gaps = ticks.slice(1).map((value, i) => value - ticks[i]);
        expect(new Set(gaps).size).toBe(1);
        expect(ticks.every((value) => Number.isInteger(value))).toBe(true);
    });

    it('stays inside the domain it is given', () => {
        const ticks = niceTicks(35, 75);
        expect(Math.min(...ticks)).toBeGreaterThanOrEqual(35);
        expect(Math.max(...ticks)).toBeLessThanOrEqual(75);
    });

    it('degrades to a single value on a zero-width domain', () => {
        expect(niceTicks(50, 50)).toEqual([50]);
    });
});

describe('buildTimeTicks', () => {
    it('never repeats a label across a multi-day span', () => {
        // The observed defect: a 2.1-day series rendered "Jun 15" five times,
        // which reads as broken rendering rather than as five distinct times.
        const ticks = buildTimeTicks(T0, T0 + 2.1 * DAY);
        const labels = ticks.map((tick) => tick.label);
        expect(new Set(labels).size).toBe(labels.length);
    });

    it('never repeats a label across a long span', () => {
        const ticks = buildTimeTicks(T0, T0 + 120 * DAY);
        const labels = ticks.map((tick) => tick.label);
        expect(new Set(labels).size).toBe(labels.length);
    });

    it('uses clock labels when the whole series fits in a day', () => {
        const ticks = buildTimeTicks(T0, T0 + 6 * HOUR);
        expect(ticks.every((tick) => /\d{1,2}:\d{2}/.test(tick.label))).toBe(true);
        expect(new Set(ticks.map((t) => t.label)).size).toBe(ticks.length);
    });

    it('spans exactly the data range', () => {
        const ticks = buildTimeTicks(T0, T0 + 30 * DAY);
        expect(ticks[0].value).toBe(T0);
        expect(ticks[ticks.length - 1].value).toBe(T0 + 30 * DAY);
    });

    it('degrades to one tick when start and end coincide', () => {
        expect(buildTimeTicks(T0, T0)).toHaveLength(1);
    });
});

describe('formatStamp', () => {
    it('shows a clock for a sub-two-day span', () => {
        // A market that lived six hours must not render six identical dates.
        expect(formatStamp(T0, 6 * HOUR)).toMatch(/\d{1,2}:\d{2}/);
    });

    it('shows a date for a multi-day span', () => {
        expect(formatStamp(T0, 30 * DAY)).not.toMatch(/:/);
    });
});
