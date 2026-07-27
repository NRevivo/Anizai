import { useCallback, useEffect, useRef, useState } from 'react';
import { fetchTrendingMarkets, type TrendingMarket } from '../services/trending.service';
import { StateMessage } from './ui/StateMessage';
import { Button } from './ui/button';

/**
 * Above this many options the list becomes a scan-and-give-up wall (candidate
 * fields run to 128 legs), so we show the leading options and offer a free-text
 * escape hatch instead of paginating a list nobody reads.
 */
const SHORTLIST_THRESHOLD = 8;

type LoadState =
    | { kind: 'loading' }
    | { kind: 'error' }
    | { kind: 'ready'; markets: TrendingMarket[] };

interface MarketPickerProps {
    eventId: string;
    eventTitle: string;
    /** See `TrendingForecast.mutuallyExclusive` — wording only, not behaviour. */
    mutuallyExclusive: boolean;
    /** Receives the market's FULL question, and its conditionId (null = freeform). */
    onSelect: (question: string, conditionId: string | null) => void;
    onClose: () => void;
}

function formatPercent(probability: number): string {
    return `${Math.round(probability * 100)}%`;
}

/**
 * Choose which market of a trending event to forecast.
 *
 * Exists because the event title is a display label, not a proposition: submitting
 * "Fed Decision in July?" gives the pipeline nothing to match, while
 * "Will there be no change in Fed interest rates after the July 2026 meeting?" is a
 * real market with a price behind it. The user picks a leg; we submit its question.
 *
 * Binary events never open this — they carry their single market inline and submit
 * on click (see TrendingContext).
 */
export function MarketPicker({
    eventId,
    eventTitle,
    mutuallyExclusive,
    onSelect,
    onClose,
}: MarketPickerProps) {
    const [state, setState] = useState<LoadState>({ kind: 'loading' });
    const [freeText, setFreeText] = useState('');
    const dialogRef = useRef<HTMLDivElement>(null);

    const load = useCallback(() => {
        let cancelled = false;
        setState({ kind: 'loading' });
        fetchTrendingMarkets(eventId)
            .then((markets) => {
                if (!cancelled) setState({ kind: 'ready', markets });
            })
            .catch(() => {
                // fetchTrendingMarkets rethrows by design: a failed fetch must
                // render as a retryable error, never as an empty picker that
                // reads "this event has no markets".
                if (!cancelled) setState({ kind: 'error' });
            });
        return () => {
            cancelled = true;
        };
    }, [eventId]);

    useEffect(() => load(), [load]);

    useEffect(() => {
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape') onClose();
        };
        document.addEventListener('keydown', onKeyDown);
        dialogRef.current?.focus();
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [onClose]);

    const markets = state.kind === 'ready' ? state.markets : [];
    const isShortlisted = markets.length > SHORTLIST_THRESHOLD;
    const visibleMarkets = isShortlisted ? markets.slice(0, SHORTLIST_THRESHOLD) : markets;

    const submitFreeText = (event: React.FormEvent) => {
        event.preventDefault();
        const question = freeText.trim();
        // No conditionId: a self-written question matches no market, so there is
        // no benchmark to compare against. Said plainly in the hint below.
        if (question) onSelect(question, null);
    };

    return (
        <>
            <div
                className="fixed inset-0 z-50 bg-black bg-opacity-50 transition-opacity"
                onClick={onClose}
                aria-hidden="true"
            />
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                <div
                    ref={dialogRef}
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="market-picker-title"
                    tabIndex={-1}
                    className="relative z-50 flex max-h-[85vh] w-full max-w-lg flex-col rounded-lg bg-white shadow-xl focus:outline-none"
                >
                    <div className="flex items-start justify-between gap-3 border-b border-gray-200 p-4 sm:p-5">
                        <div className="min-w-0">
                            <h3
                                id="market-picker-title"
                                className="break-words text-base font-semibold leading-snug text-gray-900"
                            >
                                {eventTitle}
                            </h3>
                            <p className="mt-1 text-xs text-gray-500">
                                {mutuallyExclusive
                                    ? 'These outcomes are mutually exclusive — pick the one you want forecast.'
                                    : 'These are separate, overlapping outcomes — pick the one you mean.'}
                            </p>
                        </div>
                        <button
                            type="button"
                            onClick={onClose}
                            aria-label="Close"
                            className="-mr-1 -mt-1 inline-flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-md text-gray-400 transition-colors hover:bg-gray-100 hover:text-gray-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500"
                        >
                            <svg
                                className="h-4 w-4"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth={2}
                                strokeLinecap="round"
                                aria-hidden="true"
                            >
                                <path d="M18 6 6 18M6 6l12 12" />
                            </svg>
                        </button>
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto p-4 sm:p-5" aria-live="polite">
                        {state.kind === 'loading' ? (
                            <StateMessage
                                compact
                                variant="loading"
                                title="Loading outcomes"
                                description="Fetching the markets in this event."
                            />
                        ) : null}

                        {state.kind === 'error' ? (
                            <StateMessage
                                compact
                                variant="error"
                                title="Couldn't load outcomes"
                                description="The market list didn't load. This is a loading failure, not an empty event."
                                action={
                                    <Button variant="outline" size="sm" onClick={load}>
                                        Try again
                                    </Button>
                                }
                            />
                        ) : null}

                        {state.kind === 'ready' && markets.length === 0 ? (
                            <StateMessage
                                compact
                                title="No open outcomes"
                                description="Every market in this event has closed, so there is nothing here to forecast."
                            />
                        ) : null}

                        {state.kind === 'ready' && markets.length > 0 ? (
                            <>
                                <ul className="space-y-2">
                                    {visibleMarkets.map((market) => {
                                        // The server falls groupItemTitle back to question, so
                                        // showing both would duplicate on binary-shaped legs.
                                        const showQuestion =
                                            market.groupItemTitle.trim() !== market.question.trim();
                                        return (
                                            <li key={market.conditionId}>
                                                <button
                                                    type="button"
                                                    onClick={() =>
                                                        onSelect(market.question, market.conditionId)
                                                    }
                                                    className="flex w-full min-h-11 cursor-pointer items-center justify-between gap-3 rounded-md border border-gray-200 bg-white px-3 py-2.5 text-left transition-colors hover:border-anizai-teal-300 hover:bg-anizai-teal-50/40 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500"
                                                >
                                                    <span className="min-w-0">
                                                        <span className="block break-words text-sm font-medium text-gray-900">
                                                            {market.groupItemTitle}
                                                        </span>
                                                        {showQuestion ? (
                                                            <span className="mt-0.5 block break-words text-xs text-gray-500">
                                                                {market.question}
                                                            </span>
                                                        ) : null}
                                                    </span>
                                                    <span className="shrink-0 font-mono text-sm font-semibold text-gray-700">
                                                        {formatPercent(market.probability)}
                                                    </span>
                                                </button>
                                            </li>
                                        );
                                    })}
                                </ul>

                                {isShortlisted ? (
                                    <p className="mt-3 text-xs text-gray-400">
                                        Showing the {SHORTLIST_THRESHOLD} most likely of{' '}
                                        {markets.length} outcomes.
                                    </p>
                                ) : null}
                            </>
                        ) : null}
                    </div>

                    {/* The free-text escape hatch only earns its space on a field too
                        large to scan; below that, every outcome is already listed. */}
                    {state.kind === 'ready' && isShortlisted ? (
                        <div className="border-t border-gray-200 p-4 sm:p-5">
                            <label
                                htmlFor="market-picker-freetext"
                                className="block text-sm font-medium text-gray-900"
                            >
                                Ask your own question
                            </label>
                            <p className="mt-1 text-xs text-gray-500">
                                No market benchmark — a question you write yourself has no market
                                price to compare against.
                            </p>
                            <form onSubmit={submitFreeText} className="mt-2 flex gap-2">
                                <input
                                    id="market-picker-freetext"
                                    type="text"
                                    value={freeText}
                                    onChange={(event) => setFreeText(event.target.value)}
                                    placeholder="Will …?"
                                    className="min-w-0 flex-1 rounded-md border border-gray-300 px-3 py-2 text-sm text-gray-900 placeholder:text-gray-400 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500"
                                />
                                <Button
                                    type="submit"
                                    variant="primary"
                                    size="sm"
                                    disabled={!freeText.trim()}
                                    className="shrink-0"
                                >
                                    Forecast
                                </Button>
                            </form>
                        </div>
                    ) : null}
                </div>
            </div>
        </>
    );
}
