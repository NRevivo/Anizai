export interface TrendingForecast {
    id: string;
    question: string;
    probability: number;
    trend: 'up' | 'down' | 'stable';
    badge: 'Trending' | 'High volatility' | 'New' | 'Popular';
}

interface TrendingForecastsProps {
    onSelectForecast: (question: string) => void;
}

export function TrendingForecasts({ onSelectForecast }: TrendingForecastsProps) {
    const forecasts: TrendingForecast[] = [
        {
            id: '1',
            question: 'Will the EU implement comprehensive AI regulation before Q3 2026?',
            probability: 0.72,
            trend: 'up',
            badge: 'Trending'
        },
        {
            id: '2',
            question: 'Probability of Bitcoin exceeding $150k before end of 2026',
            probability: 0.46,
            trend: 'down',
            badge: 'High volatility'
        },
        {
            id: '3',
            question: 'Likelihood of SpaceX completing a crewed Mars landing in 2026',
            probability: 0.12,
            trend: 'stable',
            badge: 'Popular'
        },
        {
            id: '4',
            question: 'Will global inflation rates stabilize below 2% within 2026?',
            probability: 0.39,
            trend: 'up',
            badge: 'New'
        }
    ];

    const getTrendIcon = (trend: TrendingForecast['trend']) => {
        if (trend === 'up') return { icon: '↑', color: 'text-green-600' };
        if (trend === 'down') return { icon: '↓', color: 'text-red-600' };
        return { icon: '→', color: 'text-gray-400' };
    };

    return (
        <div className="space-y-6">
            {/* Header */}
            <div className="border-b border-gray-100 pb-4">
                <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">Trending forecasts</h2>
                <p className="text-sm text-gray-500 mt-1">What prediction markets are currently focused on</p>
            </div>

            {/* Forecast List */}
            <div className="space-y-6">
                {forecasts.map((forecast) => {
                    const trendInfo = getTrendIcon(forecast.trend);
                    return (
                        <div
                            key={forecast.id}
                            className="group cursor-pointer"
                            onClick={() => onSelectForecast(forecast.question)}
                        >
                            {/* Question & Meta */}
                            <div className="space-y-2">
                                <p className="text-gray-800 font-medium leading-relaxed group-hover:text-anizai-teal-700 transition-colors">
                                    {forecast.question}
                                </p>

                                <div className="flex items-center justify-between text-sm">
                                    <div className="flex items-center gap-3">
                                        <div className="flex items-center gap-1.5 font-semibold text-gray-900">
                                            <span>{(forecast.probability * 100).toFixed(0)}%</span>
                                            <span className={trendInfo.color}>{trendInfo.icon}</span>
                                        </div>
                                        {forecast.badge === 'High volatility' && (
                                            <span className="text-xs text-amber-600 bg-amber-50 px-1.5 py-0.5 rounded">Volatile</span>
                                        )}
                                    </div>

                                    <span className="text-anizai-teal-600 opacity-0 group-hover:opacity-100 transition-opacity text-xs font-medium">
                                        Analyze this →
                                    </span>
                                </div>
                            </div>
                        </div>
                    );
                })}
            </div>

            {/* Free Plan Indicator - Minimal */}
            <div className="pt-6 border-t border-gray-100">
                <div className="flex items-center gap-2 text-xs text-gray-500">
                    <div className="w-1.5 h-1.5 rounded-full bg-anizai-teal-500"></div>
                    <span>2 of 3 free forecasts remaining this month</span>
                </div>
            </div>
        </div>
    );
}
