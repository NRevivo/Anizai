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
        <div className="h-full bg-white border-l border-gray-200 flex flex-col">
            <div className="p-6 border-b border-gray-100">
                <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">Trending Forecasts</h2>
                <p className="text-sm text-gray-500 mt-1">Market context & signals</p>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-8">
                {forecasts.length === 0 ? (
                    <div className="rounded-md border border-dashed border-gray-200 bg-gray-50 p-4 text-sm text-gray-500">
                        No trending forecasts yet. New trends will appear as data is added to Firestore.
                    </div>
                ) : (
                    forecasts.map((item) => (
                        <div key={item.id} className="group">
                            <div className="flex items-start justify-between mb-2">
                                <h3 className="text-sm font-medium text-gray-900 leading-relaxed group-hover:text-anizai-teal-700 transition-colors">
                                    {item.question}
                                </h3>
                                <button
                                    onClick={() => onAnalyze(item.question)}
                                    className="opacity-0 group-hover:opacity-100 transition-opacity text-xs font-medium text-anizai-teal-600 hover:text-anizai-teal-700 whitespace-nowrap ml-2"
                                >
                                    Analyze →
                                </button>
                            </div>

                            <div className="flex items-center gap-3 text-sm">
                                <div className="flex items-center gap-1 font-mono font-medium text-gray-700">
                                    {(item.probability * 100).toFixed(0)}%
                                    <span
                                        className={
                                            item.trend === 'up' ? 'text-green-500' :
                                                item.trend === 'down' ? 'text-red-500' : 'text-gray-400'
                                        }
                                    >
                                        {item.trend === 'up' ? '↑' : item.trend === 'down' ? '↓' : '→'}
                                    </span>
                                </div>
                                <span className="text-gray-400 text-xs truncate">
                                    {item.context}
                                </span>
                            </div>
                        </div>
                    ))
                )}
            </div>

            <div className="p-4 border-t border-gray-100 bg-gray-50">
                <p className="text-xs text-center text-gray-500">
                    Data aggregated from global prediction markets
                </p>
            </div>
        </div>
    );
}
