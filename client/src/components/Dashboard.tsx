import { useCallback, useRef, useState } from 'react';
import type { MarketPricePoint, Prediction, SentimentDataPoint, TimelineEvent } from '../types';
import { PredictionOverview } from './cards/predictionOverview';
import { MarketComparison } from './cards/MarketComparison';
import { MarketPriceHistory } from './cards/MarketPriceHistory';
import { SentimentAnalysis } from './cards/SentimentAnalysis';
import { EvidenceTimeline } from './cards/EvidenceTimeline';

interface DashboardProps {
    prediction: Prediction;
    sentimentData: SentimentDataPoint[];
    timelineEvents: TimelineEvent[];
    marketPricePoints: MarketPricePoint[];
}

const HIGHLIGHT_DURATION_MS = 3500;

export function Dashboard({
    prediction,
    sentimentData,
    timelineEvents,
    marketPricePoints,
}: DashboardProps) {
    // Evidence IDs to highlight when a Drivers/Headwinds factor is clicked.
    const [highlightedEvidenceIds, setHighlightedEvidenceIds] = useState<string[]>([]);
    const clearTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

    const handleFactorSelect = useCallback((evidenceIds: string[]) => {
        if (clearTimerRef.current) {
            clearTimeout(clearTimerRef.current);
        }
        if (evidenceIds.length === 0) {
            setHighlightedEvidenceIds([]);
            return;
        }
        // New array identity each call so EvidenceTimeline re-scrolls even
        // when the same factor is clicked twice.
        setHighlightedEvidenceIds([...evidenceIds]);
        clearTimerRef.current = setTimeout(() => {
            setHighlightedEvidenceIds([]);
        }, HIGHLIGHT_DURATION_MS);
    }, []);

    return (
        <div
            className="w-full h-full max-w-full overflow-y-auto overflow-x-hidden bg-slate-50 font-sans"
            style={{
                backgroundImage:
                    'radial-gradient(ellipse 55% 45% at 0% 0%, rgba(168,85,247,0.06), transparent 70%), radial-gradient(ellipse 55% 45% at 100% 100%, rgba(20,184,166,0.06), transparent 70%)',
            }}
        >
            <div className="max-w-6xl 2xl:max-w-7xl mx-auto px-3 sm:px-5 xl:px-6 py-3 sm:py-4 lg:py-5 space-y-4">
                <div className="flex items-start justify-between gap-4 border-b border-gray-200 pb-4">
                    <div className="min-w-0">
                        <div className="mb-2">
                            <span className="inline-flex items-center gap-1.5 text-[11px] font-medium text-slate-500">
                                <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" aria-hidden />
                                Active forecast
                            </span>
                        </div>
                        <h1 className="text-lg sm:text-xl lg:text-2xl font-bold text-gray-900 leading-snug break-words">
                            {prediction.question}
                        </h1>
                    </div>
                </div>

                <div className="grid grid-cols-1 gap-4">
                    <div className="w-full">
                        <PredictionOverview prediction={prediction} onFactorSelect={handleFactorSelect} />
                    </div>

                    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
                        <MarketComparison
                            anizaiProbability={prediction.probability}
                            marketProbability={prediction.marketProbability}
                            tier={prediction.tier}
                            insight={prediction.marketComparisonInsight}
                        />
                        <SentimentAnalysis data={sentimentData} insight={prediction.sentimentAnalysisInsight} />
                    </div>

                    {/* Full width, and directly under the market benchmark it
                        extends: MarketComparison shows our number against one
                        market price, this shows whether that price has been
                        stable or moving. A time series with ~683 points needs
                        the horizontal room, so it does not join the 2-up row. */}
                    <div className="w-full">
                        <MarketPriceHistory
                            points={marketPricePoints}
                            anizaiProbability={prediction.probability}
                            tier={prediction.tier}
                        />
                    </div>

                    <div className="w-full">
                        <EvidenceTimeline
                            events={timelineEvents}
                            insight={prediction.evidenceFeedSummary}
                            highlightedEvidenceIds={highlightedEvidenceIds}
                        />
                    </div>
                    {/* Rule A (live-only): the agent reasoning panel is progress
                        UI, never part of the finished report. Once status === 'done'
                        this view renders, and the panel must not appear — including
                        on re-open of an old session. It lives only in the in-progress
                        branch of DashboardPage. */}
                </div>
            </div>
        </div>
    );
}
