import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { formatProbability } from '../../lib/utils';

interface PredictionOverviewProps {
    probability: number;
    confidenceIndex: number;
    explanation: string;
}

export function PredictionOverview({ probability, confidenceIndex, explanation }: PredictionOverviewProps) {
    // Calculate stroke dasharray for circular gauge
    const radius = 70;
    const circumference = 2 * Math.PI * radius;
    const strokeDashoffset = circumference - (probability / 100) * circumference;

    return (
        <Card>
            <CardHeader>
                <CardTitle>Prediction Overview</CardTitle>
                <CardDescription>Current probability and confidence assessment</CardDescription>
            </CardHeader>
            <CardContent>
                <div className="flex items-center gap-8">
                    {/* Probability Gauge */}
                    <div className="relative flex items-center justify-center">
                        <svg className="transform -rotate-90" width="160" height="160">
                            {/* Background circle */}
                            <circle
                                cx="80"
                                cy="80"
                                r={radius}
                                stroke="#e5e7eb"
                                strokeWidth="12"
                                fill="none"
                            />
                            {/* Progress circle with gradient */}
                            <defs>
                                <linearGradient id="gaugeGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                                    <stop offset="0%" stopColor="rgb(20, 184, 166)" />
                                    <stop offset="50%" stopColor="rgb(59, 130, 246)" />
                                    <stop offset="100%" stopColor="rgb(168, 85, 247)" />
                                </linearGradient>
                            </defs>
                            <circle
                                cx="80"
                                cy="80"
                                r={radius}
                                stroke="url(#gaugeGradient)"
                                strokeWidth="12"
                                fill="none"
                                strokeDasharray={circumference}
                                strokeDashoffset={strokeDashoffset}
                                strokeLinecap="round"
                                className="transition-all duration-1000 ease-out"
                            />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className="text-4xl font-bold gradient-text">
                                {formatProbability(probability)}
                            </span>
                            <span className="text-xs text-gray-500 mt-1">Probability</span>
                        </div>
                    </div>

                    {/* Confidence & Explanation */}
                    <div className="flex-1">
                        <div className="mb-4">
                            <div className="flex items-center justify-between mb-2">
                                <span className="text-sm font-medium text-gray-700">Confidence Index</span>
                                <span className="text-sm font-semibold text-gray-900">{confidenceIndex}/100</span>
                            </div>
                            <div className="w-full bg-gray-200 rounded-full h-2">
                                <div
                                    className="bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 h-2 rounded-full transition-all duration-1000"
                                    style={{ width: `${confidenceIndex}%` }}
                                />
                            </div>
                        </div>
                        <p className="text-sm text-gray-600 leading-relaxed">
                            {explanation}
                        </p>
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
