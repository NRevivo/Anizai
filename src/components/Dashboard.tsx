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
        <div className="w-full h-full overflow-y-auto bg-gray-50">
            <div className="px-8 py-8 space-y-8">
                {/* Current Question */}
                <div className="mb-8">
                    <h1 className="text-3xl font-semibold bg-gradient-to-r from-anizai-teal-700 via-anizai-blue-700 to-anizai-purple-700 bg-clip-text text-transparent leading-tight mb-2">
                        {prediction.question}
                    </h1>
                    <p className="text-sm text-gray-500">
                        Last updated {new Date(prediction.updatedAt).toLocaleString()}
                    </p>
                </div>

                {/* Card Grid */}
                <div className="grid grid-cols-1 lg:grid-cols-2 gap-8">
                    {/* Prediction Overview */}
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

                    {/* Evidence Timeline */}
                    <div className="lg:col-span-2">
                        <EvidenceTimeline events={timelineEvents} />
                    </div>
                </div>
            </div>
        </div>
    );
}
