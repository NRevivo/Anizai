import { useEffect, useState } from 'react';
import { MarketPicker } from './MarketPicker';
import { FreeformQuestionModal } from './FreeformQuestionModal';
import { randomUUID } from '../lib/utils';
import {
    searchTrendingForecasts,
    type TrendingForecast,
    type TrendingMarket,
} from '../services/trending.service';

/** One trending event, as the dashboard maps it. Mirrors `TrendingQuestionView`. */
interface TrendingEvent {
    id: string;
    /** The event TITLE — a display label. Never submitted; a market question is. */
    question: string;
    probability: number | null;
    outcomes: { label: string; probability: number }[];
    /** Inline for binary events only; `[]` for multi-outcome (fetched on demand). */
    markets: TrendingMarket[];
    volume24h: number;
    marketCount: number;
    mutuallyExclusive: boolean;
    url: string;
}

interface CreateForecastViewProps {
    /** `conditionId` is null for freeform questions, which resolve to no market. */
    onSubmit: (
        question: string,
        idempotencyKey: string,
        conditionId?: string | null
    ) => Promise<void>;
    onOpenSubscription?: () => void;
    userPlan?: 'free' | 'premium';
    monthlyForecastsUsed?: number;
    trendingEvents?: TrendingEvent[];
    isLoading?: boolean;
}

/**
 * Wire shape → the view's shape. The only difference is the name of the title
 * field: `App.toTrendingView` calls it `question`, so search results are renamed
 * here to render through the identical card.
 */
function toTrendingEvent(item: TrendingForecast): TrendingEvent {
    return {
        id: item.id,
        question: item.title || 'Untitled forecast',
        probability: item.probability,
        outcomes: item.outcomes,
        markets: item.markets,
        volume24h: item.volume24h,
        marketCount: item.marketCount,
        mutuallyExclusive: item.mutuallyExclusive,
        url: item.url,
    };
}

/** Matches MIN_QUERY_LENGTH in the BFF — below this the server returns []. */
const MIN_QUERY_LENGTH = 2;
const SEARCH_DEBOUNCE_MS = 300;

type SearchState =
    | { kind: 'idle' }
    | { kind: 'searching' }
    | { kind: 'error' }
    | { kind: 'results'; events: TrendingEvent[] };

/** Compact USD volume, matching how Polymarket labels its own cards ("$2M Vol."). */
function formatVolume(value: number): string {
    if (!Number.isFinite(value) || value <= 0) return '—';
    if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `$${Math.round(value / 1_000)}K`;
    return `$${Math.round(value)}`;
}

function formatPercent(probability: number): string {
    return `${Math.round(probability * 100)}%`;
}

/**
 * The new-forecast screen.
 *
 * Markets lead, free text follows. Previously this screen was a single empty text
 * box with the live markets demoted to a side rail (desktop) or pushed below the
 * fold (mobile) — which put the weakest path first: a self-written question
 * resolves to no market, so it can never carry a benchmark, while every card here
 * does. Picking a market also submits its exact question text, which is what the
 * pipeline stores and matches on.
 *
 * Both entry points now open the same kind of modal, so neither is the "buried"
 * one: a card opens `MarketPicker` to choose the outcome, and the header button
 * opens `FreeformQuestionModal`.
 */
export function CreateForecastView({
    onSubmit,
    onOpenSubscription,
    userPlan = 'free',
    monthlyForecastsUsed = 0,
    trendingEvents = [],
    isLoading = false,
}: CreateForecastViewProps) {
    const [isFreeformOpen, setIsFreeformOpen] = useState(false);
    const [pickerFor, setPickerFor] = useState<TrendingEvent | null>(null);
    const [query, setQuery] = useState('');
    const [search, setSearch] = useState<SearchState>({ kind: 'idle' });

    const trimmedQuery = query.trim();
    const isSearchActive = trimmedQuery.length >= MIN_QUERY_LENGTH;

    // Debounced so a typed word costs one upstream call, not one per keystroke.
    // `cancelled` guards against a slow earlier query overwriting a newer result.
    useEffect(() => {
        if (!isSearchActive) {
            setSearch({ kind: 'idle' });
            return;
        }

        let cancelled = false;
        setSearch({ kind: 'searching' });
        const timer = setTimeout(() => {
            searchTrendingForecasts(trimmedQuery)
                .then((events) => {
                    if (!cancelled) {
                        setSearch({ kind: 'results', events: events.map(toTrendingEvent) });
                    }
                })
                .catch(() => {
                    // searchTrendingForecasts rethrows by design: a failed request
                    // must not render as "no markets found".
                    if (!cancelled) setSearch({ kind: 'error' });
                });
        }, SEARCH_DEBOUNCE_MS);

        return () => {
            cancelled = true;
            clearTimeout(timer);
        };
    }, [trimmedQuery, isSearchActive]);

    const submit = (question: string, conditionId: string | null) => {
        void onSubmit(question, randomUUID(), conditionId).catch(() => undefined);
    };

    /**
     * A card click resolves to a market, never to the event title.
     *
     * Binary events submit straight from their inline market — that path has no
     * picker and must not pay a round-trip. Keyed on `markets[0]` existing rather
     * than `marketCount === 1`, because a binary event whose only leg is inactive
     * reports `marketCount: 1` with `markets: []`, and trusting the count there
     * would submit `undefined`.
     */
    const handleEventSelect = (event: TrendingEvent) => {
        const inlineMarket = event.markets[0];
        if (event.marketCount === 1 && inlineMarket) {
            submit(inlineMarket.question, inlineMarket.conditionId);
            return;
        }
        setPickerFor(event);
    };

    return (
        <div className="h-full w-full max-w-full overflow-y-auto overflow-x-hidden font-sans">
            <div className="mx-auto w-full max-w-4xl px-4 py-8 sm:px-6 sm:py-10 lg:px-8">
                <header className="animate-fadeIn">
                    <p className="text-[11px] font-semibold uppercase tracking-[0.14em] text-slate-400">
                        New forecast
                    </p>
                    <h1 className="mt-3 text-3xl font-light leading-tight tracking-[-0.02em] text-gray-900 sm:text-4xl">
                        What do you want to forecast?
                    </h1>
                    <p className="mt-3 max-w-xl text-[15px] leading-[1.7] text-slate-600">
                        Pick a live market below and Anizai forecasts that exact question —
                        probability, confidence, and the evidence behind it, measured against
                        the market's own price.
                    </p>
                </header>

                {/* Free text is a peer entry point, not the default one. */}
                <div className="mt-6 animate-fadeIn" style={{ animationDelay: '80ms' }}>
                    <button
                        type="button"
                        onClick={() => setIsFreeformOpen(true)}
                        className="group flex w-full cursor-pointer items-center gap-4 rounded-2xl bg-white p-4 text-left ring-1 ring-slate-900/[0.06] transition-all hover:-translate-y-px hover:ring-anizai-teal-300 hover:shadow-[0_12px_30px_-14px_rgba(15,23,42,0.18)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500 sm:p-5"
                    >
                        <span className="inline-flex h-11 w-11 shrink-0 items-center justify-center rounded-xl bg-gradient-to-br from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 text-white shadow-sm">
                            <svg className="h-5 w-5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.8} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                                <path d="M12 20h9" />
                                <path d="M16.5 3.5a2.121 2.121 0 0 1 3 3L7 19l-4 1 1-4Z" />
                            </svg>
                        </span>
                        <span className="min-w-0 flex-1">
                            <span className="block text-[15px] font-semibold text-gray-900">
                                Ask your own question
                            </span>
                            <span className="mt-0.5 block text-[13px] leading-relaxed text-slate-500">
                                Anything future-facing. No market benchmark for these.
                            </span>
                        </span>
                        <svg className="h-4 w-4 shrink-0 text-slate-300 transition-colors group-hover:text-anizai-teal-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                            <path d="m9 18 6-6-6-6" />
                        </svg>
                    </button>
                </div>

                <div className="mt-8 animate-fadeIn" style={{ animationDelay: '120ms' }}>
                    <label htmlFor="market-search" className="sr-only">
                        Search Polymarket markets
                    </label>
                    <div className="relative">
                        <svg
                            className="pointer-events-none absolute left-4 top-1/2 h-4 w-4 -translate-y-1/2 text-slate-400"
                            viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2}
                            strokeLinecap="round" strokeLinejoin="round" aria-hidden="true"
                        >
                            <circle cx="11" cy="11" r="7" />
                            <path d="m20 20-3.5-3.5" />
                        </svg>
                        <input
                            id="market-search"
                            type="search"
                            value={query}
                            onChange={(event) => setQuery(event.target.value)}
                            placeholder="Search all Polymarket markets — try “fed”, “iran”, “bitcoin”"
                            className="h-12 w-full rounded-2xl bg-white pl-11 pr-11 text-[14px] text-gray-900 ring-1 ring-slate-900/[0.06] transition-shadow placeholder:text-slate-400 focus:outline-none focus:ring-2 focus:ring-anizai-teal-400 [&::-webkit-search-cancel-button]:hidden"
                        />
                        {query ? (
                            <button
                                type="button"
                                onClick={() => setQuery('')}
                                aria-label="Clear search"
                                className="absolute right-3 top-1/2 inline-flex h-7 w-7 -translate-y-1/2 cursor-pointer items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500"
                            >
                                <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                                    <path d="M18 6 6 18M6 6l12 12" />
                                </svg>
                            </button>
                        ) : null}
                    </div>
                </div>

                <div className="mt-8 flex items-baseline justify-between gap-4 animate-fadeIn" style={{ animationDelay: '140ms' }}>
                    {/* Wraps rather than truncates: at 375px "Trending on Polymarket"
                        clipped to "TRENDING ON POLYM…", which reads as a bug. */}
                    <h2 className="min-w-0 break-words text-[13px] font-semibold uppercase tracking-[0.12em] text-slate-500">
                        {isSearchActive ? `Results for “${trimmedQuery}”` : 'Trending on Polymarket'}
                    </h2>
                    <span className="shrink-0 text-[12px] text-slate-400">
                        {isSearchActive
                            ? search.kind === 'results'
                                ? `${search.events.length} ${search.events.length === 1 ? 'market' : 'markets'}`
                                : ''
                            : 'Updated every 5 minutes'}
                    </span>
                </div>

                <div className="mt-4 animate-fadeIn" style={{ animationDelay: '180ms' }} aria-live="polite">
                    {isSearchActive ? (
                        <SearchResults
                            state={search}
                            query={trimmedQuery}
                            onSelect={handleEventSelect}
                            onClear={() => setQuery('')}
                        />
                    ) : isLoading ? (
                        <SkeletonGrid />
                    ) : trendingEvents.length === 0 ? (
                        <div className="rounded-2xl bg-white p-8 text-center ring-1 ring-slate-900/[0.06]">
                            <p className="text-[15px] font-medium text-gray-900">
                                No live markets right now
                            </p>
                            <p className="mx-auto mt-1.5 max-w-sm text-[13px] leading-relaxed text-slate-500">
                                The trending feed is unavailable. You can still ask your own
                                question above.
                            </p>
                        </div>
                    ) : (
                        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
                            {trendingEvents.map((event) => (
                                <li key={event.id}>
                                    <EventCard event={event} onSelect={handleEventSelect} />
                                </li>
                            ))}
                        </ul>
                    )}
                </div>
            </div>

            {pickerFor ? (
                <MarketPicker
                    eventId={pickerFor.id}
                    eventTitle={pickerFor.question}
                    mutuallyExclusive={pickerFor.mutuallyExclusive}
                    onSelect={(question, conditionId) => {
                        setPickerFor(null);
                        submit(question, conditionId);
                    }}
                    onClose={() => setPickerFor(null)}
                />
            ) : null}

            {isFreeformOpen ? (
                <FreeformQuestionModal
                    onSubmit={onSubmit}
                    onClose={() => setIsFreeformOpen(false)}
                    onOpenSubscription={onOpenSubscription}
                    userPlan={userPlan}
                    monthlyForecastsUsed={monthlyForecastsUsed}
                />
            ) : null}
        </div>
    );
}

/**
 * Search results, with the three states kept distinct.
 *
 * "No matches" and "the request failed" must not look alike — the first is a real
 * answer about the catalogue, the second is a retryable fault. The empty case also
 * says *why* an on-topic-only search can come back empty, so a user searching for a
 * football market is not left thinking the feature is broken.
 */
function SearchResults({
    state,
    query,
    onSelect,
    onClear,
}: {
    state: SearchState;
    query: string;
    onSelect: (event: TrendingEvent) => void;
    onClear: () => void;
}) {
    if (state.kind === 'searching' || state.kind === 'idle') {
        return <SkeletonGrid count={4} />;
    }

    if (state.kind === 'error') {
        return (
            <div className="rounded-2xl bg-white p-8 text-center ring-1 ring-red-200">
                <p className="text-[15px] font-medium text-red-800">Search failed</p>
                <p className="mx-auto mt-1.5 max-w-sm text-[13px] leading-relaxed text-red-700">
                    The search request didn't complete. This is a loading failure, not an
                    empty result — try again.
                </p>
            </div>
        );
    }

    if (state.events.length === 0) {
        return (
            <div className="rounded-2xl bg-white p-8 text-center ring-1 ring-slate-900/[0.06]">
                <p className="text-[15px] font-medium text-gray-900">
                    No markets match “{query}”
                </p>
                <p className="mx-auto mt-1.5 max-w-md text-[13px] leading-relaxed text-slate-500">
                    Search covers the topics Anizai gathers evidence for — politics, economics,
                    geopolitics, crypto, energy, tech and science. Sport and entertainment
                    markets are excluded.
                </p>
                <button
                    type="button"
                    onClick={onClear}
                    className="mt-4 inline-flex h-9 cursor-pointer items-center rounded-lg px-3 text-[13px] font-medium text-anizai-teal-700 transition-colors hover:bg-anizai-teal-50 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500"
                >
                    Back to trending
                </button>
            </div>
        );
    }

    return (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2">
            {state.events.map((event) => (
                <li key={event.id}>
                    <EventCard event={event} onSelect={onSelect} />
                </li>
            ))}
        </ul>
    );
}

function EventCard({
    event,
    onSelect,
}: {
    event: TrendingEvent;
    onSelect: (event: TrendingEvent) => void;
}) {
    const isBinary = event.probability !== null;
    // Prefer the real selectable count: `marketCount` still includes inactive
    // placeholder legs, so a 33-market field can offer only 8 real choices.
    const selectableCount = event.markets.length || event.marketCount;

    return (
        <button
            type="button"
            onClick={() => onSelect(event)}
            className="group flex h-full w-full cursor-pointer flex-col rounded-2xl bg-white p-5 text-left ring-1 ring-slate-900/[0.06] transition-all hover:-translate-y-px hover:ring-anizai-purple-200 hover:shadow-[0_14px_34px_-16px_rgba(15,23,42,0.22)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500"
        >
            <div className="flex items-start justify-between gap-3">
                <h3 className="min-w-0 break-words text-[15px] font-medium leading-snug text-gray-900 transition-colors group-hover:text-anizai-purple-700">
                    {event.question}
                </h3>
                {isBinary ? (
                    <span className="shrink-0 text-2xl font-light tabular-nums leading-none text-gray-900">
                        {formatPercent(event.probability as number)}
                    </span>
                ) : null}
            </div>

            {!isBinary && event.outcomes.length > 0 ? (
                <ul className="mt-4 space-y-1.5">
                    {event.outcomes.map((outcome) => (
                        <li key={outcome.label} className="flex items-center gap-3 text-[13px]">
                            <span className="min-w-0 flex-1 truncate text-slate-600">
                                {outcome.label}
                            </span>
                            <span className="shrink-0 tabular-nums font-medium text-slate-700">
                                {formatPercent(outcome.probability)}
                            </span>
                        </li>
                    ))}
                </ul>
            ) : null}

            <div className="mt-auto flex items-center gap-2 pt-4 text-[12px] text-slate-400">
                <span className="tabular-nums">{formatVolume(event.volume24h)} 24h vol</span>
                {!isBinary ? (
                    <>
                        <span aria-hidden="true">·</span>
                        <span className="tabular-nums">{selectableCount} outcomes</span>
                    </>
                ) : null}
                <span className="ml-auto inline-flex items-center gap-1 font-medium text-slate-300 transition-colors group-hover:text-anizai-teal-600">
                    {isBinary ? 'Forecast' : 'Choose'}
                    <svg className="h-3.5 w-3.5" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
                        <path d="m9 18 6-6-6-6" />
                    </svg>
                </span>
            </div>
        </button>
    );
}

function SkeletonGrid({ count = 6 }: { count?: number }) {
    return (
        <ul className="grid grid-cols-1 gap-3 md:grid-cols-2" aria-hidden="true">
            {Array.from({ length: count }).map((_, index) => (
                <li
                    key={index}
                    className="animate-pulse rounded-2xl bg-white p-5 ring-1 ring-slate-900/[0.06]"
                >
                    <div className="h-4 w-4/5 rounded bg-slate-100" />
                    <div className="mt-2 h-4 w-2/5 rounded bg-slate-100" />
                    <div className="mt-5 space-y-2">
                        <div className="h-3 w-full rounded bg-slate-50" />
                        <div className="h-3 w-3/4 rounded bg-slate-50" />
                    </div>
                    <div className="mt-6 h-3 w-1/3 rounded bg-slate-50" />
                </li>
            ))}
        </ul>
    );
}
