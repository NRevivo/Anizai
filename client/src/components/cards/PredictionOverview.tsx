import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { formatProbability } from '../../lib/utils';

interface PredictionOverviewProps {
    probability: number;
    confidenceIndex: number;
    explanation: string;
}

export function PredictionOverview({ probability, confidenceIndex, explanation }: PredictionOverviewProps) {
    const radius = 70;
    const circumference = 2 * Math.PI * radius;
    const strokeDashoffset = circumference - (probability / 100) * circumference;
    const uniqueGradientId = "gauge-gradient-" + Math.random().toString(36).substr(2, 9);

    return (
        <Card className="h-full border-gray-200 bg-white shadow-sm">
            <CardHeader className="pb-4 border-b border-gray-50">
                <div className="flex items-center justify-between">
                    <div>
                        <CardTitle className="text-lg font-semibold text-gray-900">Forecast Analysis</CardTitle>
                        <CardDescription className="text-xs text-gray-500 mt-1">
                            Integrated multi-source assessment
                        </CardDescription>
                    </div>
                    <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-anizai-teal-50 text-anizai-teal-700 border border-anizai-teal-100">
                        High Confidence
                    </span>
                </div>
            </CardHeader>
            <CardContent className="pt-6">
                <div className="flex flex-col xl:flex-row items-center gap-8 xl:gap-10">
                    {/* Probability Gauge */}
                    <div className="relative flex items-center justify-center shrink-0">
                        <svg className="transform -rotate-90" width="180" height="180">
                            {/* Track */}
                            <circle cx="90" cy="90" r={radius} stroke="#f3f4f6" strokeWidth="12" fill="none" />
                            <defs>
                                <linearGradient id={uniqueGradientId} x1="0%" y1="0%" x2="100%" y2="100%">
                                    <stop offset="0%" stopColor="rgb(20, 184, 166)" />
                                    <stop offset="50%" stopColor="rgb(59, 130, 246)" />
                                    <stop offset="100%" stopColor="rgb(168, 85, 247)" />
                                </linearGradient>
                            </defs>
                            {/* Progress */}
                            <circle
                                cx="90" cy="90" r={radius}
                                stroke={`url(#${uniqueGradientId})`}
                                strokeWidth="12" fill="none"
                                strokeDasharray={circumference}
                                strokeDashoffset={strokeDashoffset}
                                strokeLinecap="round"
                                className="transition-all duration-1000 ease-out"
                            />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className="text-4xl font-bold text-gray-900">
                                {formatProbability(probability)}
                            </span>
                            <span className="text-xs font-medium text-gray-500 mt-1 uppercase tracking-wide">Probability</span>
                        </div>
                    </div>

                    {/* Content */}
                    <div className="flex-1 min-w-0 w-full space-y-8">
                        {/* Metrics Grid */}
                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-4">
                            <div className="p-3 bg-gray-50 rounded-lg border border-gray-100">
                                <p className="text-[10px] uppercase tracking-wider font-semibold text-gray-500 mb-1">Confidence</p>
                                <p className="text-lg font-bold text-gray-900">{confidenceIndex}/100</p>
                            </div>
                            <div className="p-3 bg-gray-50 rounded-lg border border-gray-100">
                                <p className="text-[10px] uppercase tracking-wider font-semibold text-gray-500 mb-1">Evidence Vol.</p>
                                <p className="text-lg font-bold text-gray-900">High</p>
                            </div>
                            <div className="p-3 bg-gray-50 rounded-lg border border-gray-100">
                                <p className="text-[10px] uppercase tracking-wider font-semibold text-gray-500 mb-1">Consensus</p>
                                <p className="text-lg font-bold text-gray-900">Strong</p>
                            </div>
                        </div>

                        {/* Explanation */}
                        <div>
                            <h4 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-2 flex items-center gap-2">
                                <div className="w-1.5 h-1.5 rounded-full bg-anizai-blue-500"></div>
                                Executive Summary
                            </h4>
                            <div className="text-sm text-gray-700 leading-relaxed space-y-3">
                                <p className="font-semibold text-gray-900">
                                    <span className="text-anizai-teal-700 uppercase text-[10px] tracking-wider font-bold mr-2">Bottom Line</span>
                                    Legislative momentum and expert consensus strongly signal Q2 passage, outweighing industry resistance.
                                </p>
                                <p className="text-gray-600">{explanation}</p>
                            </div>
                        </div>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
