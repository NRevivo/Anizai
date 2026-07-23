import { useEffect, useState } from 'react';
import { fetchTrendingForecasts, type TrendingForecast } from '../../services/trending.service';

const DISPLAY_COUNT = 6;
// Modest over-fetch: /trending now returns event cards ranked by 24h volume and
// already excludes single-fixture noise server-side, so nearly every row is
// usable — we no longer need to sift a wide pool.
const FETCH_COUNT = 12;
// Show only binary questions whose market is genuinely in play — skip near-certain
// resolutions on either side of the dial.
//
// This filter used to be load-bearing: /markets returned individual candidate legs,
// so without it the section filled with sub-2% long shots ("Will LeBron win the 2028
// presidency"). Those legs no longer arrive — /events collapses them into their
// parent card (KG-C-13) — but the band still earns its place, because a market
// sitting at 99% is a resolved question and makes a poor advert for a forecasting
// tool.
const MIN_PROBABILITY = 0.05;
const MAX_PROBABILITY = 0.95;

type LoadState =
    | { kind: 'loading' }
    | { kind: 'ready'; rows: TrendingForecast[] }
    | { kind: 'empty' };

function formatProbability(probability: number | null | undefined): string | null {
    if (probability === undefined || probability === null || Number.isNaN(probability)) {
        return null;
    }
    // The BFF normalises to a 0–1 float. Guard against either scale just in case.
    const value = probability > 1 ? probability / 100 : probability;
    if (value < 0 || value > 1) {
        return null;
    }
    return `${Math.round(value * 100)}%`;
}

/**
 * The headline number for a row: the Yes price of a binary event, or the leading
 * outcome of a multi-outcome field ("Ballon d'Or Winner 2026" → its favourite).
 */
function headlineProbability(row: TrendingForecast): number | null {
    if (row.probability !== null) return row.probability;
    return row.outcomes[0]?.probability ?? null;
}

function isUsable(row: TrendingForecast): boolean {
    if (!row.title || !row.title.trim()) {
        return false;
    }
    const p = headlineProbability(row);
    if (p === null || formatProbability(p) === null) {
        return false;
    }
    const value = p > 1 ? p / 100 : p;
    return value >= MIN_PROBABILITY && value <= MAX_PROBABILITY;
}

export function QuestionsWeTrack() {
    const [state, setState] = useState<LoadState>({ kind: 'loading' });

    useEffect(() => {
        let cancelled = false;

        fetchTrendingForecasts(FETCH_COUNT)
            .then((items) => {
                if (cancelled) return;
                const rows = items.filter(isUsable).slice(0, DISPLAY_COUNT);
                if (rows.length === 0) {
                    setState({ kind: 'empty' });
                    return;
                }
                setState({ kind: 'ready', rows });
            })
            .catch(() => {
                if (cancelled) return;
                setState({ kind: 'empty' });
            });

        return () => {
            cancelled = true;
        };
    }, []);

    return (
        <section className="w-full px-6 py-24 lg:py-32 bg-white border-y border-slate-200/60 relative">
            <div className="max-w-4xl mx-auto">
                <div className="max-w-2xl mb-12 lg:mb-16">
                    <div className="mb-4 inline-flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" aria-hidden />
                        <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                            Questions we track
                        </span>
                    </div>
                    <h2 className="text-3xl sm:text-4xl lg:text-[2.75rem] font-light leading-tight tracking-[-0.02em] text-gray-900">
                        What people are betting on right now.
                    </h2>
                    <p className="mt-5 text-[15px] leading-[1.7] text-slate-600">
                        Live questions from Polymarket. Anizai generates a structured
                        forecast for any of them.
                    </p>
                </div>

                <div className="rounded-3xl bg-slate-50/60 ring-1 ring-slate-900/[0.06] shadow-[0_8px_30px_-12px_rgba(15,23,42,0.10),0_2px_6px_-2px_rgba(15,23,42,0.06)] overflow-hidden">
                    {state.kind === 'loading' && <SkeletonRows />}
                    {state.kind === 'empty' && <EmptyState />}
                    {state.kind === 'ready' && (
                        <ul className="divide-y divide-slate-200/70">
                            {state.rows.map((row) => (
                                <QuestionRow key={row.id} row={row} />
                            ))}
                        </ul>
                    )}
                </div>

                <p className="mt-4 text-[12px] text-slate-400">
                    Updated every 5 minutes.
                </p>
            </div>
        </section>
    );
}

function QuestionRow({ row }: { row: TrendingForecast }) {
    const text = row.title.trim();
    const pct = formatProbability(headlineProbability(row));
    // For a candidate field the headline number belongs to the front-runner, so
    // name them — an unattributed 38% under "Ballon d'Or Winner 2026" is meaningless.
    const leader = row.probability === null ? row.outcomes[0]?.label : null;

    return (
        <li className="px-6 sm:px-8 py-6 hover:bg-white/70 transition-colors">
            <div className="flex items-start gap-6">
                <div className="min-w-0 flex-1">
                    <p className="text-[15px] sm:text-base font-medium leading-snug line-clamp-2">
                        <a
                            href={row.url}
                            target="_blank"
                            rel="noopener noreferrer"
                            className="text-slate-800 transition-colors hover:text-anizai-purple-600 hover:underline"
                        >
                            {text}
                        </a>
                    </p>
                    <p className="mt-2 text-[12px] text-slate-500">
                        {leader ? `${leader} leading · Tracked on Polymarket` : 'Tracked on Polymarket'}
                    </p>
                </div>
                <div className="shrink-0 text-right pt-0.5">
                    <span className="text-2xl font-light tabular-nums text-gray-900">
                        {pct}
                    </span>
                </div>
            </div>
        </li>
    );
}

function SkeletonRows() {
    return (
        <ul className="divide-y divide-slate-200/70" aria-hidden>
            {Array.from({ length: 5 }).map((_, i) => (
                <li key={i} className="px-6 sm:px-8 py-6">
                    <div className="flex items-start gap-6">
                        <div className="min-w-0 flex-1 space-y-2.5">
                            <div className="h-4 w-11/12 rounded bg-slate-200/80 animate-pulse" />
                            <div className="h-3 w-32 rounded bg-slate-200/70 animate-pulse" />
                        </div>
                        <div className="h-7 w-14 rounded bg-slate-200/80 animate-pulse" />
                    </div>
                </li>
            ))}
        </ul>
    );
}

function EmptyState() {
    return (
        <div className="px-6 sm:px-8 py-14 text-center">
            <p className="text-[14px] text-slate-500">
                Questions unavailable right now.
            </p>
        </div>
    );
}
