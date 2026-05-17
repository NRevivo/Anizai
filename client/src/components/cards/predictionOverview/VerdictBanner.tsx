import type { Prediction } from '../../../types';
import type { Verdict, VerdictTone } from './lib/deriveVerdict';
import type { Deadline } from './lib/extractDeadline';

interface VerdictBannerProps {
    verdict: Verdict;
    deadline: Deadline | null;
    finalProbability: number;
    confidenceLabel: Prediction['confidenceLabel'];
    bottomLineAnswer: Prediction['bottomLineAnswer'];
    detailedExplanation: Prediction['detailedExplanation'];
    summaryMarkdown: Prediction['summaryMarkdown'];
}

// Subdued status treatment: pastel tint behind darker, readable text.
const PILL_TONE: Record<VerdictTone, string> = {
    positive: 'bg-anizai-teal-50 text-anizai-teal-800',
    negative: 'bg-rose-50 text-rose-800',
    neutral: 'bg-slate-100 text-slate-700',
    warning: 'bg-amber-50 text-amber-900',
};

const DOT_TONE: Record<VerdictTone, string> = {
    positive: 'bg-anizai-teal-500',
    negative: 'bg-rose-500',
    neutral: 'bg-slate-400',
    warning: 'bg-amber-500',
};

function stripMarkdown(text: string): string {
    return text
        .replace(/!?\[([^\]]*)\]\([^)]*\)/g, '$1') // images / links → label
        .replace(/[*_`#>]/g, '')
        .replace(/\s+/g, ' ')
        .trim();
}

function firstSentence(text: string): string {
    const trimmed = text.trim();
    const match = trimmed.match(/^.*?[.!?](?=\s|$)/);
    return (match ? match[0] : trimmed).trim();
}

function deriveThesis(props: VerdictBannerProps): string {
    const { bottomLineAnswer, detailedExplanation, summaryMarkdown } = props;

    if (bottomLineAnswer && bottomLineAnswer.trim()) {
        return bottomLineAnswer.trim();
    }
    if (detailedExplanation && detailedExplanation.trim()) {
        return firstSentence(detailedExplanation);
    }
    if (summaryMarkdown && summaryMarkdown.trim()) {
        return firstSentence(stripMarkdown(summaryMarkdown));
    }

    const pct = Math.round(props.finalProbability * 100);
    const confidenceText = (props.confidenceLabel ?? 'unknown confidence').toLowerCase();
    return `${props.verdict.label}. ${pct}% probability with ${confidenceText}.`;
}

export function VerdictBanner(props: VerdictBannerProps) {
    const { verdict, deadline } = props;
    const thesis = deriveThesis(props);

    return (
        <div className="min-w-0 flex-1 space-y-3">
            <div className="flex flex-wrap items-center gap-2">
                <span
                    className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1 text-xs font-semibold ${PILL_TONE[verdict.tone]}`}
                >
                    <span className={`h-1.5 w-1.5 rounded-full ${DOT_TONE[verdict.tone]}`} />
                    {verdict.label}
                </span>
                {deadline ? (
                    <span className="inline-flex items-center rounded-full bg-slate-100 px-2.5 py-1 text-xs font-medium text-slate-500">
                        {deadline.label}
                    </span>
                ) : null}
            </div>
            <p className="text-xl font-semibold leading-snug text-gray-900 break-words sm:text-[1.7rem] sm:leading-[1.3]">
                {thesis}
            </p>
        </div>
    );
}
