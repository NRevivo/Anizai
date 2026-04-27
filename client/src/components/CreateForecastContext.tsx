import { StateMessage } from './ui/StateMessage';

interface TrendingQuestion {
    id: string;
    question: string;
    probability: number;
    trend: 'up' | 'down' | 'stable';
    context: string;
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
                                <h3 className="min-w-0 break-words text-sm font-medium text-gray-900 leading-relaxed group-hover:text-anizai-teal-700 transition-colors">
                                    {item.question}
                                </h3>
                                <button
                                    onClick={() => onAnalyze(item.question)}
                                    className="min-h-8 shrink-0 rounded-md px-2 text-xs font-medium text-anizai-teal-600 opacity-100 transition-opacity hover:bg-anizai-teal-50 hover:text-anizai-teal-700 sm:opacity-0 sm:group-hover:opacity-100"
                                >
                                    Use
                                </button>
                            </div>

                            <div className="flex min-w-0 items-center gap-3 text-sm">
                                <div className="flex items-center gap-1 font-mono font-medium text-gray-700">
                                    {item.probability}%
                                    <span
                                        className={
                                            item.trend === 'up' ? 'text-green-500' :
                                                item.trend === 'down' ? 'text-red-500' : 'text-gray-400'
                                        }
                                    >
                                        {item.trend === 'up' ? 'Rising' : item.trend === 'down' ? 'Falling' : 'Steady'}
                                    </span>
                                </div>
                                <span className="min-w-0 text-gray-400 text-xs truncate">
                                    {item.context}
                                </span>
                            </div>
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
