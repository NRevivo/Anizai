import type { MarketPricePoint } from '../types';

/**
 * Pure geometry for the market price-history chart.
 *
 * Split out of `cards/MarketPriceHistory.tsx` because the axis rule below is the
 * one part of that card that can be wrong without looking wrong — a mis-scaled
 * axis still renders a plausible line. Everything here is unit-tested.
 */

/**
 * The y-axis never spans less than this many percentage points.
 *
 * A market that traded between 54% and 56% is genuinely flat, and an auto-fitted
 * axis would magnify that 2-point drift into a dramatic-looking swing — the same
 * class of error as showing probability without confidence. A fixed 0–100 axis
 * fails the other way, flattening every real move into a horizontal line.
 * Clamping the *minimum* span keeps small moves honest and still lets a large
 * move fill the chart.
 */
export const MIN_AXIS_SPAN_PCT = 20;

const MS_PER_DAY = 86_400_000;

/** Roughly how many ticks to aim for on each axis. */
const TARGET_TICKS = 5;

/** Step sizes an axis is allowed to use, so labels land on readable numbers. */
const NICE_STEPS = [1, 2, 5, 10, 20, 25, 50];

export interface AxisTick {
    value: number;
    label: string;
}

export interface MarketPriceChart {
    /** Points in percent, ascending by time. */
    data: { t: number; pct: number }[];
    first: { t: number; pct: number };
    last: { t: number; pct: number };
    /** Milliseconds between the first and last sample. */
    spanMs: number;
    /** `[min, max]` for the y-axis, in percent, always within 0–100. */
    domain: [number, number];
    /** Evenly spaced y-axis values. Recharts fits its own otherwise, and against
     *  an arbitrary domain that yields runs like 23 / 43 / 63 / 90. */
    yTicks: number[];
    /** X-axis ticks with labels precomputed — see `buildTimeTicks`. */
    xTicks: AxisTick[];
    /** The Anizai forecast in percent — drawn as a reference line. */
    anizaiPct: number;
}

/** Evenly spaced values covering `[min, max]`, on a step from NICE_STEPS. */
export function niceTicks(min: number, max: number, target = TARGET_TICKS): number[] {
    if (!(max > min)) return [min];
    const rawStep = (max - min) / target;
    const step = NICE_STEPS.find((candidate) => candidate >= rawStep) ?? NICE_STEPS[NICE_STEPS.length - 1];

    const ticks: number[] = [];
    for (let value = Math.ceil(min / step) * step; value <= max; value += step) {
        ticks.push(value);
    }
    return ticks.length > 0 ? ticks : [min, max];
}

/**
 * X-axis ticks whose labels are guaranteed distinct.
 *
 * A date-only label repeats as soon as two ticks land on the same day — a
 * 2.1-day series rendered "Jun 15, Jun 15, Jun 15, Jun 16, Jun 16", which reads
 * as a rendering fault rather than as five distinct times. Labels are therefore
 * built together, not per-tick in isolation: the day is dropped once it repeats
 * and the clock takes over, so every tick says something the one before it did not.
 */
export function buildTimeTicks(startMs: number, endMs: number, target = TARGET_TICKS): AxisTick[] {
    if (!(endMs > startMs)) {
        return [{ value: startMs, label: formatStamp(startMs, 0) }];
    }

    const spanMs = endMs - startMs;
    const step = spanMs / target;
    const values = Array.from({ length: target + 1 }, (_, i) => Math.round(startMs + i * step));

    const dateLabel = (ms: number) =>
        new Date(ms).toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
    const timeLabel = (ms: number) =>
        new Date(ms).toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });

    // Under two days a clock is the only thing that distinguishes ticks at all.
    if (spanMs < 2 * MS_PER_DAY) {
        return values.map((value) => ({ value, label: timeLabel(value) }));
    }

    let previousDate = '';
    return values.map((value) => {
        const date = dateLabel(value);
        const label = date === previousDate ? timeLabel(value) : date;
        previousDate = date;
        return { value, label };
    });
}

/**
 * Build everything the chart needs, or `null` when there is nothing to draw.
 *
 * `null` covers both "the subcollection was never written" (freeform) and
 * "it exists but is empty" — the caller distinguishes those by `tier`, since the
 * array alone cannot.
 */
export function buildMarketPriceChart(
    points: MarketPricePoint[],
    anizaiProbability: number
): MarketPriceChart | null {
    if (points.length === 0) return null;

    const data = points.map((point) => ({ t: point.t, pct: point.probability * 100 }));
    const first = data[0];
    const last = data[data.length - 1];

    const values = data.map((d) => d.pct);
    const anizaiPct = anizaiProbability * 100;
    // The reference line joins the extent, or it can land outside the plotted
    // area and silently disappear — the one comparison the card exists to make.
    const low = Math.min(...values, anizaiPct);
    const high = Math.max(...values, anizaiPct);
    const extent = high - low;

    const padding = Math.max((MIN_AXIS_SPAN_PCT - extent) / 2, extent * 0.12, 1);

    // Snapped outwards to multiples of 5 so the axis labels are round numbers.
    // Snapping outwards only — never inwards, which would clip a real data point.
    const domain: [number, number] = [
        Math.max(0, Math.floor((low - padding) / 5) * 5),
        Math.min(100, Math.ceil((high + padding) / 5) * 5),
    ];

    const spanMs = last.t - first.t;

    return {
        data,
        first,
        last,
        spanMs,
        domain,
        yTicks: niceTicks(domain[0], domain[1]),
        xTicks: buildTimeTicks(first.t, last.t),
        anizaiPct,
    };
}

/**
 * Axis/footer stamp. A series spanning hours needs a clock; one spanning weeks
 * needs a date, where a clock would just repeat "12:00 AM" across every tick.
 */
export function formatStamp(epochMs: number, spanMs: number): string {
    const date = new Date(epochMs);
    if (spanMs < 2 * MS_PER_DAY) {
        return date.toLocaleTimeString('en-US', { hour: 'numeric', minute: '2-digit' });
    }
    return date.toLocaleDateString('en-US', { month: 'short', day: 'numeric' });
}
