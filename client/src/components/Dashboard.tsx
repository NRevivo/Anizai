import type { AgentEvent, Prediction, SentimentDataPoint, TimelineEvent } from '../types';
import { PredictionOverview } from './cards/PredictionOverview';
import { MarketComparison } from './cards/MarketComparison';
import { SentimentAnalysis } from './cards/SentimentAnalysis';
import { EvidenceTimeline } from './cards/EvidenceTimeline';
import { AgentEventsTimeline } from './cards/AgentEventsTimeline';

interface DashboardProps {
    prediction: Prediction;
    sentimentData: SentimentDataPoint[];
    timelineEvents: TimelineEvent[];
    agentEvents: AgentEvent[];
    isAgentEventsLoading?: boolean;
}

export function Dashboard({ prediction, sentimentData, timelineEvents, agentEvents, isAgentEventsLoading = false }: DashboardProps) {
    return (
        <div className="w-full h-full max-w-full overflow-y-auto overflow-x-hidden bg-slate-50 font-sans">
            <div className="max-w-6xl 2xl:max-w-7xl mx-auto px-3 sm:px-5 xl:px-6 py-3 sm:py-4 lg:py-5 space-y-4">
                <div className="flex items-start justify-between gap-4 border-b border-gray-200 pb-4">
                    <div className="min-w-0">
                        <div className="flex flex-wrap items-center gap-2 mb-2">
                            <span className="inline-flex items-center px-2 py-0.5 rounded text-[10px] font-medium bg-green-100 text-green-800 uppercase tracking-wide">
                                Active forecast
                            </span>
                            <span className="text-xs text-gray-400 font-medium">ID: #8392-A</span>
                        </div>
                        <h1 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-snug break-words">
                            {prediction.question}
                        </h1>
                    </div>
                </div>

                <div className="grid grid-cols-1 gap-4">
                    <div className="w-full">
                        <PredictionOverview
                            probability={prediction.probability}
                            confidenceIndex={prediction.confidenceIndex}
                            explanation={prediction.explanation}
                            evidenceCount={timelineEvents.length}
                        />
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <MarketComparison
                            anizaiProbability={prediction.probability}
                            marketProbability={prediction.marketProbability}
                        />
                        <SentimentAnalysis data={sentimentData} />
                    </div>

                    <div className="w-full">
                        <EvidenceTimeline events={timelineEvents} />
                    </div>

                    <div className="w-full">
                        <AgentEventsTimeline events={agentEvents} isLoading={isAgentEventsLoading} />
                    </div>
                </div>
            </div>
        </div>
    );
}
