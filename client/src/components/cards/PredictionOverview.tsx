import type { KeyFactor, ReasoningStep } from '../../types';
import { useId } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { formatProbability } from '../../lib/utils';

interface PredictionOverviewProps {
    probability: number;
    confidenceIndex: number;
    explanation: string;
    evidenceCount: number;
    keyFactors?: KeyFactor[];
    whatIDidntFind?: string[];
    reasoningChain?: ReasoningStep[];
    generatedAt?: Date | null;
    agentVersion?: string | null;
    tier?: 'tier_1' | 'tier_2' | null;
}

function getConfidenceLabel(confidenceIndex: number): string {
    if (confidenceIndex >= 0.75) return 'High confidence';
    if (confidenceIndex >= 0.5) return 'Moderate confidence';
    if (confidenceIndex > 0) return 'Low confidence';
    return 'Confidence unavailable';
}

function getProbabilityAnswer(probability: number): string {
    if (probability >= 0.66) return 'Likely';
    if (probability <= 0.34) return 'Unlikely';
    return 'Uncertain';
}

function getFactorTone(direction: KeyFactor['direction']): string {
    if (direction === 'supports') return 'bg-emerald-50 text-emerald-700 border-emerald-200';
    if (direction === 'opposes') return 'bg-red-50 text-red-700 border-red-200';
    return 'bg-amber-50 text-amber-700 border-amber-200';
}

export function PredictionOverview({
    probability,
    confidenceIndex,
    explanation,
    evidenceCount,
    keyFactors = [],
    whatIDidntFind = [],
    reasoningChain = [],
    generatedAt = null,
    agentVersion = null,
    tier = null,
}: PredictionOverviewProps) {
    const radius = 70;
    const circumference = 2 * Math.PI * radius;
    const strokeDashoffset = circumference - probability * circumference;
    const uniqueGradientId = useId().replace(/:/g, '');
    const confidenceLabel = getConfidenceLabel(confidenceIndex);
    const probabilityAnswer = getProbabilityAnswer(probability);
    const confidenceScore = Math.round(confidenceIndex * 100);
    const hasKeyFactors = keyFactors.length > 0;
    const hasWhatIDidntFind = whatIDidntFind.length > 0;
    const hasReasoningChain = reasoningChain.length > 0;

    return (
        <Card className="h-full max-w-full overflow-hidden border-gray-200 bg-white shadow-sm ring-1 ring-anizai-teal-500/10">
            <CardHeader className="p-4 sm:p-5 pb-3 border-b border-gray-100">
                <div className="flex flex-wrap items-center justify-between gap-3">
                    <div className="min-w-0">
                        <CardTitle className="text-lg font-semibold text-gray-900">Forecast Result</CardTitle>
                        <CardDescription className="text-xs text-gray-500 mt-1">
                            Final probability, confidence, and rationale
                        </CardDescription>
                    </div>
                    <span className="inline-flex items-center px-2.5 py-1 rounded-full text-xs font-medium bg-anizai-teal-50 text-anizai-teal-700 border border-anizai-teal-100">
                        {confidenceLabel}
                    </span>
                </div>
            </CardHeader>
            <CardContent className="p-4 sm:p-5 pt-4">
                <div className="grid grid-cols-1 lg:grid-cols-[176px_minmax(0,1fr)] 2xl:grid-cols-[190px_minmax(0,1fr)] gap-5 xl:gap-6">
                    <div className="relative flex items-center justify-center shrink-0">
                        <svg className="transform -rotate-90 h-[148px] w-[148px] sm:h-[168px] sm:w-[168px]" viewBox="0 0 180 180">
                            <circle cx="90" cy="90" r={radius} stroke="#f3f4f6" strokeWidth="12" fill="none" />
                            <defs>
                                <linearGradient id={`gauge-gradient-${uniqueGradientId}`} x1="0%" y1="0%" x2="100%" y2="100%">
                                    <stop offset="0%" stopColor="rgb(20, 184, 166)" />
                                    <stop offset="50%" stopColor="rgb(59, 130, 246)" />
                                    <stop offset="100%" stopColor="rgb(168, 85, 247)" />
                                </linearGradient>
                            </defs>
                            <circle
                                cx="90" cy="90" r={radius}
                                stroke={`url(#gauge-gradient-${uniqueGradientId})`}
                                strokeWidth="12" fill="none"
                                strokeDasharray={circumference}
                                strokeDashoffset={strokeDashoffset}
                                strokeLinecap="round"
                                className="transition-all duration-1000 ease-out"
                            />
                        </svg>
                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                            <span className="text-3xl font-bold text-gray-900">
                                {formatProbability(probability)}
                            </span>
                            <span className="text-xs font-medium text-gray-500 mt-1 uppercase tracking-wide">Probability</span>
                        </div>
                    </div>

                    <div className="min-w-0 w-full space-y-4">
                        <div className="rounded-lg border border-anizai-teal-100 bg-anizai-teal-50/50 p-4">
                            <p className="text-[10px] uppercase tracking-wider font-semibold text-anizai-teal-700 mb-1">
                                Bottom line
                            </p>
                            <p className="text-xl sm:text-2xl font-bold text-gray-950 leading-tight break-words">
                                {probabilityAnswer} at {formatProbability(probability)}
                            </p>
                            <p className="mt-2 text-sm text-gray-600 leading-relaxed break-words">
                                Current estimate: {formatProbability(probability)} probability with {confidenceScore}/100 confidence.
                            </p>
                        </div>

                        <div className="grid grid-cols-1 sm:grid-cols-3 gap-3">
                            <div className="p-3 bg-gray-50 rounded-md border border-gray-100">
                                <p className="text-[10px] uppercase tracking-wider font-semibold text-gray-500 mb-1">Answer</p>
                                <p className="text-lg font-bold text-gray-900">{probabilityAnswer}</p>
                            </div>
                            <div className="p-3 bg-gray-50 rounded-md border border-gray-100">
                                <p className="text-[10px] uppercase tracking-wider font-semibold text-gray-500 mb-1">Confidence</p>
                                <p className="text-lg font-bold text-gray-900">{confidenceScore}/100</p>
                            </div>
                            <div className="p-3 bg-gray-50 rounded-md border border-gray-100">
                                <p className="text-[10px] uppercase tracking-wider font-semibold text-gray-500 mb-1">Evidence</p>
                                <p className="text-lg font-bold text-gray-900">{evidenceCount}</p>
                            </div>
                        </div>

                        <div>
                            <h4 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-2 flex items-center gap-2">
                                <div className="w-1.5 h-1.5 rounded-full bg-anizai-blue-500"></div>
                                Rationale
                            </h4>
                            <p className="text-sm text-gray-600 leading-relaxed max-w-3xl break-words">{explanation}</p>
                        </div>

                        {hasKeyFactors ? (
                            <div>
                                <h4 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-2">Key factors</h4>
                                <div className="space-y-2">
                                    {keyFactors
                                        .slice()
                                        .sort((a, b) => a.rank - b.rank)
                                        .map((factor) => (
                                            <div key={`${factor.rank}-${factor.title}`} className="rounded-md border border-gray-100 bg-gray-50 p-3">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <p className="text-sm font-semibold text-gray-900">{factor.title}</p>
                                                    <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${getFactorTone(factor.direction)}`}>
                                                        {factor.direction}
                                                    </span>
                                                    <span className="text-[11px] text-gray-500">
                                                        Weight {Math.round(factor.weight * 100)}%
                                                    </span>
                                                </div>
                                                <p className="mt-1.5 text-sm text-gray-600 break-words">{factor.explanation}</p>
                                            </div>
                                        ))}
                                </div>
                            </div>
                        ) : null}

                        {hasWhatIDidntFind ? (
                            <div>
                                <h4 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-2">What we did not find</h4>
                                <ul className="space-y-2 text-sm text-gray-600">
                                    {whatIDidntFind.map((item) => (
                                        <li key={item} className="rounded-md border border-gray-100 bg-gray-50 px-3 py-2 break-words">
                                            {item}
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ) : null}

                        {hasReasoningChain ? (
                            <div>
                                <h4 className="text-xs font-bold text-gray-900 uppercase tracking-wider mb-2">Reasoning chain</h4>
                                <div className="space-y-2">
                                    {reasoningChain
                                        .slice()
                                        .sort((a, b) => a.sequence - b.sequence)
                                        .map((step) => (
                                            <div key={`${step.sequence}-${step.description}`} className="rounded-md border border-gray-100 bg-white px-3 py-2">
                                                <div className="flex flex-wrap items-center gap-2">
                                                    <span className="text-[10px] font-semibold uppercase tracking-wide text-gray-500">Step {step.sequence}</span>
                                                    <span className="text-[11px] text-gray-500">{step.outcome}</span>
                                                </div>
                                                <p className="mt-1 text-sm text-gray-600 break-words">{step.description}</p>
                                            </div>
                                        ))}
                                </div>
                            </div>
                        ) : null}

                        {(generatedAt || agentVersion || tier) ? (
                            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
                                {generatedAt ? <span>Generated {generatedAt.toLocaleString()}</span> : null}
                                {agentVersion ? <span>{agentVersion}</span> : null}
                                {tier ? <span>{tier.replace('_', ' ')}</span> : null}
                            </div>
                        ) : null}
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
