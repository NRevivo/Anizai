interface TrendingQuestion {
    id: string;
    question: string;
    probability: number;
    trend: 'up' | 'down' | 'stable';
    context: string;
}

interface TrendingContextProps {
    onAnalyze: (question: string) => void;
}

export function TrendingContext({ onAnalyze }: TrendingContextProps) {
    const trendingQuestions: TrendingQuestion[] = [
        {
            id: '1',
            question: 'Will the EU implement comprehensive AI regulation before Q3 2026?',
            probability: 72,
            trend: 'up',
            context: 'High regulatory activity in Brussels'
        },
        {
            id: '2',
            question: 'Bitcoin > $150k before end of 2026',
            probability: 46,
            trend: 'down',
            context: 'Recent volatility in crypto markets'
        },
        {
            id: '3',
            question: 'SpaceX crewed Mars landing in 2026',
            probability: 12,
            trend: 'stable',
            context: 'Timeline remains ambitious'
        },
        {
            id: '4',
            question: 'Global inflation < 2% in 2026',
            probability: 39,
            trend: 'up',
            context: 'Central banks signaling rate cuts'
        }
    ];

    return (
        <div className="h-full bg-white border-l border-gray-200 flex flex-col">
            <div className="p-6 border-b border-gray-100">
                <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">Trending Forecasts</h2>
                <p className="text-sm text-gray-500 mt-1">Market context & signals</p>
            </div>

            <div className="flex-1 overflow-y-auto p-6 space-y-8">
                {trendingQuestions.map((item) => (
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
                                {item.probability}%
                                <span className={
                                    item.trend === 'up' ? 'text-green-500' :
                                        item.trend === 'down' ? 'text-red-500' : 'text-gray-400'
                                }>
                                    {item.trend === 'up' ? '↑' : item.trend === 'down' ? '↓' : '→'}
                                </span>
                            </div>
                            <span className="text-gray-400 text-xs truncate">
                                {item.context}
                            </span>
                        </div>
                    </div>
                ))}
            </div>

            <div className="p-4 border-t border-gray-100 bg-gray-50">
                <p className="text-xs text-center text-gray-500">
                    Data aggregated from global prediction markets
                </p>
            </div>
        </div>
    );
}
