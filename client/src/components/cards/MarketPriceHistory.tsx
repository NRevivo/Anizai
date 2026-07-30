import { useMemo } from 'react';
import {
    Area,
    AreaChart,
    CartesianGrid,
    ReferenceLine,
    ResponsiveContainer,
    Tooltip,
    XAxis,
    YAxis,
} from 'recharts';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '../ui/card';
import { StateMessage } from '../ui/StateMessage';
import { buildMarketPriceChart, formatStamp } from '../../lib/marketPriceChart';
import type { MarketPricePoint } from '../../types';

interface MarketPriceHistoryProps {
    points: MarketPricePoint[];
    /** The Anizai forecast, 0–1. Drawn as a reference line so the user can read
     *  our answer against where the market has actually traded. */
    anizaiProbability: number;
    /** Distinguishes "freeform, no series was ever written" from "market forecast
     *  whose series came back empty". Both arrive as `points: []`. */
    tier?: 'tier_1' | 'tier_2' | null;
}

/** Below this, a line has too few vertices to read as a line — show the points. */
const DOT_THRESHOLD = 3;

function formatPercent(probability: number): string {
    return `${Math.round(probability * 100)}%`;
}

/**
 * The market's own recent price history.
 *
 * This is the benchmark's history, not a forecast history: every point is
 * Polymarket's YES price at an instant, written by the pipeline on the Tier-1
 * path only. It answers the question `MarketComparison` raises but cannot settle —
 * whether our number disagrees with a stable market or with one that has been
 * moving underneath it.
 *
 * ⚠ It is a **rolling ~30-day window at hourly resolution (~683 points), not the
 * market's lifetime.** Nothing here may imply "full history" or "since
 * inception" — a market open for a year shows only its last month. The footer
 * states the actual range present rather than naming a window, so the card stays
 * truthful if the pipeline retunes the window or the market is younger than it.
 */
export function MarketPriceHistory({
    points,
    anizaiProbability,
    tier = null,
}: MarketPriceHistoryProps) {
    const chart = useMemo(
        () => buildMarketPriceChart(points, anizaiProbability),
        [points, anizaiProbability]
    );

    if (!chart) {
        return (
            <Card className="h-full max-w-full overflow-hidden border-gray-200 bg-white shadow-sm">
                <CardHeader className="p-4 sm:p-5 pb-2">
                    <CardTitle className="text-base font-semibold text-gray-900">
                        Market price history
                    </CardTitle>
                    <CardDescription className="text-xs text-gray-500">
                        How the market's own price moved over time
                    </CardDescription>
                </CardHeader>
                <CardContent className="p-4 sm:p-5 pt-2">
                    <StateMessage
                        compact
                        title="No price history"
                        description={
                            tier === 'tier_2'
                                ? 'This is a freeform forecast, so it resolves to no market and there is no price history to show.'
                                : 'No price history was recorded for this market. The forecast above is unaffected.'
                        }
                    />
                </CardContent>
            </Card>
        );
    }

    const { data, first, last, spanMs, domain, yTicks, xTicks, anizaiPct } = chart;
    const xTickLabels = new Map(xTicks.map((tick) => [tick.value, tick.label]));
    const changePct = last.pct - first.pct;
    const isUp = changePct > 0;
    const gapPct = anizaiPct - last.pct;

    const headline =
        Math.abs(changePct) < 0.5
            ? `Market held near ${Math.round(last.pct)}% across the period`
            : `Market moved ${isUp ? 'up' : 'down'} ${Math.abs(changePct).toFixed(1)} points, ` +
              `${Math.round(first.pct)}% → ${Math.round(last.pct)}%`;

    const CustomTooltip = ({ active, payload }: any) => {
        if (!active || !payload?.length) return null;
        const point = payload[0].payload;
        return (
            <div className="rounded-lg border border-gray-200 bg-white p-3 shadow-sm">
                <p className="mb-1 text-xs text-gray-500">
                    {new Date(point.t).toLocaleString('en-US', {
                        month: 'short',
                        day: 'numeric',
                        hour: 'numeric',
                        minute: '2-digit',
                    })}
                </p>
                <p className="text-sm font-semibold text-gray-900">
                    Market {point.pct.toFixed(1)}%
                </p>
            </div>
        );
    };

    return (
        <Card className="h-full max-w-full overflow-hidden border-gray-200 bg-white shadow-sm">
            <CardHeader className="p-4 sm:p-5 pb-2">
                <CardTitle className="text-base font-semibold leading-tight text-gray-900 break-words">
                    {headline}
                </CardTitle>
                <CardDescription className="text-xs text-gray-500">
                    The market's own price over time, against the Anizai forecast
                </CardDescription>
            </CardHeader>
            <CardContent className="p-4 sm:p-5 pt-2">
                <div className="h-[200px] w-full overflow-hidden sm:h-[240px]">
                    <ResponsiveContainer width="100%" height="100%">
                        <AreaChart data={data} margin={{ top: 14, right: 12, left: 0, bottom: 5 }}>
                            <defs>
                                <linearGradient id="marketPriceGradient" x1="0" y1="0" x2="0" y2="1">
                                    <stop offset="5%" stopColor="#14b8a6" stopOpacity={0.22} />
                                    <stop offset="95%" stopColor="#14b8a6" stopOpacity={0} />
                                </linearGradient>
                            </defs>
                            <CartesianGrid strokeDasharray="3 3" stroke="#f0f0f0" vertical={false} />
                            <XAxis
                                dataKey="t"
                                type="number"
                                scale="time"
                                domain={['dataMin', 'dataMax']}
                                ticks={xTicks.map((tick) => tick.value)}
                                tickFormatter={(value: number) => xTickLabels.get(value) ?? ''}
                                tick={{ fontSize: 10, fill: '#9ca3af' }}
                                axisLine={false}
                                tickLine={false}
                                dy={10}
                                minTickGap={20}
                            />
                            <YAxis
                                domain={domain}
                                ticks={yTicks}
                                tickFormatter={(value: number) => `${value}%`}
                                tick={{ fontSize: 10, fill: '#9ca3af' }}
                                axisLine={false}
                                tickLine={false}
                                dx={-6}
                                width={44}
                            />
                            <Tooltip
                                content={<CustomTooltip />}
                                cursor={{ stroke: '#cbd5e1', strokeWidth: 1, strokeDasharray: '4 4' }}
                            />
                            {/* Unlabelled on purpose: an in-plot label sits on
                                top of the data whenever the forecast lands near
                                the market's own range, which is the common case.
                                The legend below carries the value instead. */}
                            <ReferenceLine
                                y={anizaiPct}
                                stroke="#9333ea"
                                strokeWidth={1.5}
                                strokeDasharray="5 4"
                            />
                            <Area
                                type="monotone"
                                dataKey="pct"
                                stroke="#0d9488"
                                strokeWidth={2}
                                fill="url(#marketPriceGradient)"
                                name="Market price"
                                // ~683 points is the expected density. Per-point dots
                                // would add that many DOM nodes for no signal, and the
                                // entry animation re-tweens every vertex on each render.
                                dot={data.length < DOT_THRESHOLD}
                                activeDot={{ r: 4, fill: '#0d9488' }}
                                isAnimationActive={false}
                            />
                        </AreaChart>
                    </ResponsiveContainer>
                </div>

                <div className="mt-3 flex flex-wrap items-center gap-x-4 gap-y-1.5 text-[11px] text-gray-500">
                    <span className="inline-flex items-center gap-1.5">
                        <span className="h-0.5 w-4 shrink-0 rounded-full bg-anizai-teal-600" aria-hidden />
                        Market price
                    </span>
                    <span className="inline-flex items-center gap-1.5">
                        <span
                            className="h-0 w-4 shrink-0 border-t-2 border-dashed border-anizai-purple-600"
                            aria-hidden
                        />
                        Anizai forecast {Math.round(anizaiPct)}%
                    </span>
                </div>

                <div className="mt-3 flex flex-col gap-3 border-t border-gray-50 pt-3 sm:flex-row sm:items-center sm:justify-between">
                    <p className="text-xs leading-relaxed text-gray-500 break-words">
                        {data.length.toLocaleString()} price
                        {data.length === 1 ? ' point' : ' points'} from{' '}
                        {formatStamp(first.t, spanMs)} to {formatStamp(last.t, spanMs)}.
                    </p>
                    <div className="flex shrink-0 gap-4">
                        <div className="text-left sm:text-right">
                            <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                                Latest
                            </p>
                            <span className="text-lg font-bold text-anizai-teal-600">
                                {formatPercent(last.pct / 100)}
                            </span>
                        </div>
                        <div className="text-left sm:text-right">
                            <p className="text-[10px] font-semibold uppercase tracking-wider text-gray-400">
                                vs Anizai
                            </p>
                            <span className="text-lg font-bold text-anizai-purple-600">
                                {gapPct >= 0 ? '+' : '−'}
                                {Math.abs(gapPct).toFixed(1)}
                            </span>
                        </div>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
