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
        <div className="w-full h-full overflow-y-auto bg-slate-50 font-sans">
            <div className="max-w-7xl mx-auto px-6 py-8 pt-20 xl:pt-8 space-y-6">
                {/* Header - Simplified */}
                <div className="flex items-center justify-between pb-2">
                    <div>
                        <div className="flex items-center gap-2 mb-1">
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-green-100 text-green-800 uppercase tracking-wide">
                                Live Prediction
                            </span>
                            <span className="text-xs text-gray-400 font-medium">ID: #8392-A</span>
                        </div>
                        <h1 className="text-2xl font-bold text-gray-900 leading-tight">
                            {prediction.question}
                        </h1>
                    </div>
                    <div className="flex gap-3">
                        <button className="px-3 py-1.5 bg-white border border-gray-200 text-gray-600 text-xs font-medium rounded-md shadow-sm hover:bg-gray-50 transition-colors">
                            Share
                        </button>
                        <button className="px-3 py-1.5 bg-white border border-gray-200 text-gray-600 text-xs font-medium rounded-md shadow-sm hover:bg-gray-50 transition-colors">
                            Export Data
                        </button>
                    </div>
                </div>

                {/* Card Grid */}
                <div className="grid grid-cols-1 gap-6">
                    {/* Top Row: Prediction Overview - Full Width */}
                    <div className="w-full">
                        <PredictionOverview
                            probability={prediction.probability}
                            confidenceIndex={prediction.confidenceIndex}
                            explanation={prediction.explanation}
                        />
                    </div>

                    {/* Charts Row: Side-by-side on large screens */}
                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                        <MarketComparison
                            anizaiProbability={prediction.probability}
                            marketProbability={prediction.marketProbability || 0}
                        />
                        <SentimentAnalysis data={sentimentData} />
                    </div>

                    {/* Evidence Feed: Full width below charts */}
                    <div className="w-full">
                        <EvidenceTimeline events={timelineEvents} />
                    </div>
                </div>
            </div>
        </div>
    );
}
