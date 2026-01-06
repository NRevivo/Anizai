import type { Prediction, SentimentDataPoint, TimelineEvent } from '../types';
import { PredictionOverview } from './cards/PredictionOverview';
import { MarketComparison } from './cards/MarketComparison';
import { SentimentAnalysis } from './cards/SentimentAnalysis';
import { EvidenceTimeline } from './cards/EvidenceTimeline';

interface DashboardProps {
    prediction: Prediction;
    sentimentData: SentimentDataPoint[];
    timelineEvents: TimelineEvent[];
}

export function Dashboard({ prediction, sentimentData, timelineEvents }: DashboardProps) {
    return (
        <div className="flex-1 overflow-y-auto bg-gray-50 p-6">
            <div className="max-w-6xl mx-auto space-y-6">
                {/* Current Question */}
                <div className="mb-6">
                    <h1 className="text-3xl font-bold text-gray-900 mb-2">
                        {prediction.question}
                    </h1>
                    <p className="text-sm text-gray-500">
                        Last updated {new Date(prediction.updatedAt).toLocaleString()}
                    </p>
                </div>

                {/* Card Grid */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                    {/* Prediction Overview - Full width on mobile, half on desktop */}
                    <div className="lg:col-span-2">
                        <PredictionOverview
                            probability={prediction.probability}
                            confidenceIndex={prediction.confidenceIndex}
                            explanation={prediction.explanation}
                        />
                    </div>

                    {/* Market Comparison */}
                    <MarketComparison
                        anizaiProbability={prediction.probability}
                        marketProbability={prediction.marketProbability || 0}
                    />

                    {/* Sentiment Analysis */}
                    <SentimentAnalysis data={sentimentData} />

                    {/* Evidence Timeline - Full width */}
                    <div className="lg:col-span-2">
                        <EvidenceTimeline events={timelineEvents} />
                    </div>
                </div>
            </div>
        </div>
    );
}
