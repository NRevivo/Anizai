import ReactMarkdown from 'react-markdown';
import type { Components } from 'react-markdown';
import type { Prediction } from '../../../types';
import { Card, CardContent } from '../../ui/card';
import { ProbabilityRing } from './ProbabilityRing';
import { VerdictBanner } from './VerdictBanner';
import { MetricsRow } from './MetricsRow';
import { DriversAndHeadwinds } from './DriversAndHeadwinds';
import { GapsNotice } from './GapsNotice';
import { ReasoningChain } from './ReasoningChain';
import { deriveVerdict } from './lib/deriveVerdict';
import { extractDeadline } from './lib/extractDeadline';

// No Tailwind typography plugin is installed, so markdown elements are styled
// explicitly here rather than relying on `prose`.
const MARKDOWN_COMPONENTS: Components = {
    p: ({ children }) => <p className="mb-2 last:mb-0 leading-relaxed">{children}</p>,
    ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-5">{children}</ul>,
    ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-5">{children}</ol>,
    li: ({ children }) => <li className="leading-relaxed">{children}</li>,
    h1: ({ children }) => <h3 className="mb-2 mt-3 text-base font-semibold text-gray-900">{children}</h3>,
    h2: ({ children }) => <h4 className="mb-2 mt-3 text-sm font-semibold text-gray-900">{children}</h4>,
    h3: ({ children }) => <h5 className="mb-1 mt-2 text-sm font-semibold text-gray-900">{children}</h5>,
    strong: ({ children }) => <strong className="font-semibold text-gray-900">{children}</strong>,
    a: ({ children, href }) => (
        <a href={href} target="_blank" rel="noreferrer" className="text-anizai-blue-600 underline">
            {children}
        </a>
    ),
};

const SUMMARY_COLLAPSE_THRESHOLD = 400;

function MarkdownSummary({ markdown }: { markdown: string }) {
    const body = (
        <div className="text-sm text-slate-600">
            <ReactMarkdown components={MARKDOWN_COMPONENTS}>{markdown}</ReactMarkdown>
        </div>
    );

    if (markdown.length <= SUMMARY_COLLAPSE_THRESHOLD) {
        return body;
    }

    return (
        <details className="group rounded-xl bg-white p-4 ring-1 ring-slate-900/[0.04]">
            <summary className="cursor-pointer text-sm font-medium text-slate-600 marker:content-['']">
                Full summary
                <span className="ml-2 text-xs font-normal text-slate-400 group-open:hidden">
                    (click to expand)
                </span>
            </summary>
            <div className="mt-3">{body}</div>
        </details>
    );
}

interface PredictionOverviewProps {
    prediction: Prediction;
    /** Called with a factor's evidence_ids when a Drivers/Headwinds row is clicked. */
    onFactorSelect?: (evidenceIds: string[]) => void;
}

export function PredictionOverview({ prediction, onFactorSelect }: PredictionOverviewProps) {
    const verdict = deriveVerdict({
        finalProbability: prediction.probability,
        confidence: prediction.confidenceIndex,
    });
    const deadline = extractDeadline(prediction.question);
    const keyFactors = prediction.keyFactors ?? [];
    const whatIDidntFind = prediction.whatIDidntFind ?? [];
    const reasoningChain = prediction.reasoningChain ?? [];
    const summaryMarkdown = prediction.summaryMarkdown;

    return (
        <Card className="h-full max-w-full overflow-hidden border-0 bg-white shadow-[0_4px_24px_rgba(15,23,42,0.06),0_1px_3px_rgba(15,23,42,0.05)] ring-1 ring-slate-900/[0.05]">
            <CardContent className="p-5 pt-7 sm:p-7 sm:pt-8">
                <div className="flex flex-col items-center gap-6 pb-7 sm:flex-row sm:gap-7 sm:pb-8">
                    <ProbabilityRing probability={prediction.probability} />
                    <VerdictBanner
                        verdict={verdict}
                        deadline={deadline}
                        finalProbability={prediction.probability}
                        confidenceLabel={prediction.confidenceLabel}
                        bottomLineAnswer={prediction.bottomLineAnswer}
                        detailedExplanation={prediction.detailedExplanation}
                        summaryMarkdown={summaryMarkdown}
                    />
                </div>

                <div className="space-y-8">
                    <MetricsRow
                        confidence={prediction.confidenceIndex}
                        confidenceLabel={prediction.confidenceLabel}
                        consensusStrength={prediction.consensusStrength}
                    />

                    <DriversAndHeadwinds keyFactors={keyFactors} onFactorSelect={onFactorSelect} />

                    {summaryMarkdown && summaryMarkdown.trim() ? (
                        <MarkdownSummary markdown={summaryMarkdown} />
                    ) : null}

                    <GapsNotice gaps={whatIDidntFind} />

                    <ReasoningChain steps={reasoningChain} />
                </div>
            </CardContent>
        </Card>
    );
}
