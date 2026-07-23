import { StateMessage } from './ui/StateMessage';

interface TrendingOutcome {
    label: string;
    probability: number;
}

interface TrendingQuestion {
    id: string;
    question: string;
    /** Null for a multi-outcome event — show `outcomes` instead of one number. */
    probability: number | null;
    outcomes: TrendingOutcome[];
    volume24h: number;
    marketCount: number;
    url: string;
}

/** Compact USD volume, matching how Polymarket labels its own cards ("$2M Vol."). */
function formatVolume(value: number): string {
    if (!Number.isFinite(value) || value <= 0) return '—';
    if (value >= 1_000_000) return `$${(value / 1_000_000).toFixed(1)}M`;
    if (value >= 1_000) return `$${Math.round(value / 1_000)}K`;
    return `$${Math.round(value)}`;
}

interface TrendingContextProps {
    onAnalyze: (question: string) => void;
    forecasts?: TrendingQuestion[];
}

export function TrendingContext({ onAnalyze, forecasts = [] }: TrendingContextProps) {
    return (
        <div className="h-full max-w-full bg-white border-l border-gray-200 flex flex-col overflow-hidden">
            <div className="p-4 border-b border-gray-100">
                <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">Trending Forecasts</h2>
                <p className="text-sm text-gray-500 mt-1">Reference questions and market signals</p>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-5">
                {forecasts.length === 0 ? (
                    <StateMessage
                        compact
                        title="No trending forecasts"
                        description="Reference questions will appear here when trending data is available."
                    />
                ) : (
                    forecasts.map((item) => (
                        <div key={item.id} className="group rounded-md border border-transparent p-2 -mx-2 transition-colors hover:border-gray-100 hover:bg-gray-50">
                            <div className="flex items-start justify-between gap-2 mb-2">
                                <h3 className="min-w-0 break-words text-sm font-medium leading-relaxed">
                                    <a
                                        href={item.url}
                                        target="_blank"
                                        rel="noopener noreferrer"
                                        className="text-gray-900 transition-colors hover:underline group-hover:text-anizai-teal-700"
                                    >
                                        {item.question}
                                    </a>
                                </h3>
                                <button
                                    onClick={() => onAnalyze(item.question)}
                                    className="min-h-8 shrink-0 rounded-md px-2 text-xs font-medium text-anizai-teal-600 opacity-100 transition-opacity hover:bg-anizai-teal-50 hover:text-anizai-teal-700 sm:opacity-0 sm:group-hover:opacity-100"
                                >
                                    Use
                                </button>
                            </div>

                            {item.probability !== null ? (
                                <div className="flex min-w-0 items-center gap-3 text-sm">
                                    <span className="font-mono font-medium text-gray-700">
                                        {(item.probability * 100).toFixed(0)}%
                                    </span>
                                    <span className="min-w-0 truncate text-xs text-gray-400">
                                        {formatVolume(item.volume24h)} 24h vol
                                    </span>
                                </div>
                            ) : (
                                // Multi-outcome event: no single probability exists, so
                                // show the leading legs the way Polymarket's own card does.
                                <div className="space-y-1">
                                    {item.outcomes.map((outcome) => (
                                        <div
                                            key={outcome.label}
                                            className="flex min-w-0 items-center justify-between gap-2 text-sm"
                                        >
                                            <span className="min-w-0 truncate text-gray-600">
                                                {outcome.label}
                                            </span>
                                            <span className="shrink-0 font-mono font-medium text-gray-700">
                                                {(outcome.probability * 100).toFixed(0)}%
                                            </span>
                                        </div>
                                    ))}
                                    <p className="pt-0.5 text-xs text-gray-400">
                                        {formatVolume(item.volume24h)} 24h vol · {item.marketCount} outcomes
                                    </p>
                                </div>
                            )}
                        </div>
                    ))
                )}
            </div>

            <div className="p-4 border-t border-gray-100 bg-gray-50">
                <p className="text-xs text-center text-gray-500">
                    Benchmarks are drawn from available prediction-market data
                </p>
            </div>
        </div>
    );
}
