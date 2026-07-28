import { useEffect, useRef, useState } from 'react';
import { Button } from './ui/button';
import { StateMessage } from './ui/StateMessage';
import { ApiError } from '../lib/api';
import { randomUUID } from '../lib/utils';

interface FreeformQuestionModalProps {
    onSubmit: (question: string, idempotencyKey: string) => Promise<void>;
    onClose: () => void;
    onOpenSubscription?: () => void;
    userPlan?: 'free' | 'premium';
    monthlyForecastsUsed?: number;
}

// Mirrors FREE_FORECAST_LIMIT in server/src/repositories/user.repository.ts.
// The server is the source of truth for the limit; this is display-only.
const FREE_FORECAST_LIMIT = 3;

const MIN_LENGTH = 10;
const MAX_LENGTH = 500;

const PLACEHOLDERS = [
    'Will the ECB cut rates before July 2026?',
    'Will Bitcoin trade above $150k in 2027?',
    'Will US inflation fall below 2.5% in 2026?',
    'Will SpaceX land a crew on Mars before 2030?',
];

interface PlanLimitDetails {
    used?: number;
    limit?: number;
    planTier?: string;
    resetAt?: string;
}

function getPlanLimitDetails(error: unknown): PlanLimitDetails | null {
    if (!(error instanceof ApiError) || error.code !== 'PLAN_LIMIT_EXCEEDED') {
        return null;
    }
    if (!error.details || typeof error.details !== 'object') {
        return {};
    }
    return error.details as PlanLimitDetails;
}

function formatResetDate(resetAt?: string): string | null {
    if (!resetAt) return null;
    const parsed = new Date(resetAt);
    if (Number.isNaN(parsed.getTime())) return null;
    return parsed.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric' });
}

/**
 * Write-your-own-question step.
 *
 * A modal rather than the page body: the new-forecast screen now leads with real
 * markets, and a permanently-open text box next to them competed for the same
 * attention while offering strictly less. Opening it on demand keeps the default
 * path (pick a market with a live price behind it) visually first.
 *
 * A self-written question resolves to no market, so it gets no benchmark — said
 * plainly here rather than discovered later on an empty card.
 */
export function FreeformQuestionModal({
    onSubmit,
    onClose,
    onOpenSubscription,
    userPlan = 'free',
    monthlyForecastsUsed = 0,
}: FreeformQuestionModalProps) {
    const [question, setQuestion] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const [planLimitDetails, setPlanLimitDetails] = useState<PlanLimitDetails | null>(null);
    const [showValidation, setShowValidation] = useState(false);
    const [placeholderIndex, setPlaceholderIndex] = useState(0);
    const [idempotencyKey, setIdempotencyKey] = useState(() => randomUUID());
    const textareaRef = useRef<HTMLTextAreaElement>(null);

    const isPremium = userPlan === 'premium';
    const forecastsRemaining = Math.max(0, FREE_FORECAST_LIMIT - monthlyForecastsUsed);
    const trimmed = question.trim();
    const isEmpty = trimmed.length === 0;
    const isTooShort = !isEmpty && trimmed.length < MIN_LENGTH;
    const isTooLong = trimmed.length > MAX_LENGTH;
    const isValid = !isEmpty && !isTooShort && !isTooLong;

    const validationMessage = isEmpty
        ? 'Enter a forecast question to continue.'
        : isTooShort
            ? `Use at least ${MIN_LENGTH} characters so the forecast is specific enough.`
            : isTooLong
                ? `Keep the forecast under ${MAX_LENGTH} characters.`
                : null;

    const displayError = formError ?? (showValidation ? validationMessage : null);

    // Timeframe / yes-no cues, shown as positive reinforcement while typing.
    const hints: string[] = [];
    const lowered = question.toLowerCase();
    if (lowered.match(/\d{4}|q[1-4]|january|february|march|april|may|june|july|august|september|october|november|december/)) {
        hints.push('Timeframe included');
    }
    if (lowered.startsWith('will ') || lowered.includes('yes or no')) {
        hints.push('Clear yes/no outcome');
    }

    useEffect(() => {
        textareaRef.current?.focus();
        const onKeyDown = (event: KeyboardEvent) => {
            if (event.key === 'Escape' && !isSubmitting) onClose();
        };
        document.addEventListener('keydown', onKeyDown);
        return () => document.removeEventListener('keydown', onKeyDown);
    }, [onClose, isSubmitting]);

    useEffect(() => {
        if (question) return;
        const interval = setInterval(() => {
            setPlaceholderIndex((prev) => (prev + 1) % PLACEHOLDERS.length);
        }, 3200);
        return () => clearInterval(interval);
    }, [question]);

    const getErrorMessage = (error: unknown): string => {
        if (getPlanLimitDetails(error)) return "You've used your free forecasts this month.";
        if (error instanceof Error) {
            const message = error.message.toLowerCase();
            if (message.includes('limit') || message.includes('upgrade') || message.includes('premium')) {
                return 'You have reached your forecast limit. Upgrade to Premium or wait for your monthly limit to reset.';
            }
            return error.message;
        }
        return 'Could not start the forecast. Please try again.';
    };

    const handleSubmit = async () => {
        if (isSubmitting) return;
        setFormError(null);
        setPlanLimitDetails(null);
        setShowValidation(true);
        if (!isValid) {
            setFormError(validationMessage);
            return;
        }
        setShowValidation(false);
        setIsSubmitting(true);
        try {
            await onSubmit(trimmed, idempotencyKey);
            setIdempotencyKey(randomUUID());
        } catch (error) {
            setPlanLimitDetails(getPlanLimitDetails(error));
            setFormError(getErrorMessage(error));
        } finally {
            setIsSubmitting(false);
        }
    };

    const resetDateLabel = formatResetDate(planLimitDetails?.resetAt);
    const planLimitDescription = planLimitDetails
        ? [
            typeof planLimitDetails.used === 'number' && typeof planLimitDetails.limit === 'number'
                ? `${planLimitDetails.used} of ${planLimitDetails.limit} forecasts used`
                : null,
            planLimitDetails.planTier ? `Plan: ${planLimitDetails.planTier}` : null,
            resetDateLabel ? `Resets on ${resetDateLabel}` : null,
        ].filter(Boolean).join(' • ')
        : null;

    return (
        <>
            <div
                className="fixed inset-0 z-50 bg-slate-900/50 backdrop-blur-[2px]"
                onClick={() => !isSubmitting && onClose()}
                aria-hidden="true"
            />
            <div className="fixed inset-0 z-50 flex items-center justify-center p-4">
                <div
                    role="dialog"
                    aria-modal="true"
                    aria-labelledby="freeform-title"
                    className="relative z-50 flex max-h-[85vh] w-full max-w-lg flex-col overflow-hidden rounded-2xl bg-white shadow-[0_24px_60px_-18px_rgba(15,23,42,0.35)] ring-1 ring-slate-900/[0.06]"
                >
                    <div className="flex items-start justify-between gap-3 px-5 pt-5 pb-3 sm:px-6 sm:pt-6">
                        <div className="min-w-0">
                            <h2
                                id="freeform-title"
                                className="text-lg font-semibold tracking-[-0.01em] text-gray-900"
                            >
                                Ask your own question
                            </h2>
                            <p className="mt-1 text-[13px] text-slate-500">
                                No market price to compare this against.
                            </p>
                        </div>
                        <button
                            type="button"
                            onClick={onClose}
                            disabled={isSubmitting}
                            aria-label="Close"
                            className="-mr-1.5 -mt-1.5 inline-flex h-9 w-9 shrink-0 cursor-pointer items-center justify-center rounded-lg text-slate-400 transition-colors hover:bg-slate-100 hover:text-slate-600 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500 disabled:opacity-40"
                        >
                            <svg className="h-4 w-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2} strokeLinecap="round" aria-hidden="true">
                                <path d="M18 6 6 18M6 6l12 12" />
                            </svg>
                        </button>
                    </div>

                    <div className="min-h-0 flex-1 overflow-y-auto px-5 pb-4 sm:px-6">
                        <label htmlFor="freeform-question" className="sr-only">
                            Forecast question
                        </label>
                        {/* The counter sits inside the field, bottom-right. Outside, it
                            forced an always-present row that left ~30px of dead band
                            under the box whenever nothing had been typed. */}
                        <div className="relative">
                            <textarea
                                id="freeform-question"
                                ref={textareaRef}
                                value={question}
                                onChange={(event) => {
                                    setQuestion(event.target.value);
                                    if (formError) setFormError(null);
                                }}
                                placeholder={PLACEHOLDERS[placeholderIndex]}
                                disabled={isSubmitting}
                                aria-invalid={Boolean(displayError)}
                                maxLength={MAX_LENGTH}
                                rows={2}
                                className="w-full resize-none rounded-xl border border-slate-200 bg-white px-4 pb-7 pt-3 text-[15px] leading-relaxed text-gray-900 transition-colors placeholder:text-slate-400 focus:border-anizai-teal-400 focus:outline-none focus:ring-4 focus:ring-anizai-teal-500/10 disabled:cursor-wait disabled:opacity-60"
                            />
                            {/* Only once it can matter — an unprompted "0/500" is noise. */}
                            {trimmed.length > 0 ? (
                                <span className={`pointer-events-none absolute bottom-2.5 right-4 text-[11px] tabular-nums ${trimmed.length > MAX_LENGTH * 0.9 ? 'text-amber-500' : 'text-slate-400'}`}>
                                    {trimmed.length}/{MAX_LENGTH}
                                </span>
                            ) : null}
                        </div>

                        {hints.length > 0 ? (
                            <div className="mt-2 flex flex-wrap gap-3">
                                {hints.map((hint) => (
                                    <span key={hint} className="inline-flex items-center gap-1 text-[12px] font-medium text-anizai-teal-600">
                                        <svg className="h-3.5 w-3.5" fill="none" stroke="currentColor" viewBox="0 0 24 24" aria-hidden="true">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                        </svg>
                                        {hint}
                                    </span>
                                ))}
                            </div>
                        ) : null}

                        {displayError ? (
                            <div className="mt-3">
                                <StateMessage
                                    compact
                                    variant={planLimitDetails ? 'warning' : 'error'}
                                    title={planLimitDetails ? 'Free forecast limit reached' : (formError ? 'Forecast was not started' : 'Check the question')}
                                    description={planLimitDescription ? `${displayError} ${planLimitDescription}` : displayError}
                                    action={planLimitDetails && onOpenSubscription ? (
                                        <button
                                            type="button"
                                            onClick={onOpenSubscription}
                                            className="inline-flex min-h-10 items-center justify-center rounded-lg bg-gray-900 px-4 text-sm font-semibold text-white hover:bg-gray-800"
                                        >
                                            Review plans
                                        </button>
                                    ) : null}
                                />
                            </div>
                        ) : null}
                    </div>

                    <div className="flex flex-col gap-3 border-t border-slate-100 bg-slate-50/40 px-5 py-3.5 sm:flex-row sm:items-center sm:justify-between sm:px-6">
                        <span className="text-[12px] text-slate-400">
                            {isSubmitting
                                ? 'Creating the forecast workspace…'
                                : isPremium
                                    ? 'Unlimited forecasts on your plan'
                                    : forecastsRemaining === 0
                                        ? 'No free forecasts left this month'
                                        : `${forecastsRemaining} of ${FREE_FORECAST_LIMIT} free forecasts left this month`}
                        </span>
                        <Button
                            onClick={handleSubmit}
                            disabled={isSubmitting}
                            className="h-10 w-full shrink-0 rounded-lg border-0 bg-gray-900 px-5 text-[13px] font-semibold text-white transition-colors hover:bg-gray-800 disabled:cursor-not-allowed disabled:opacity-50 sm:w-auto"
                        >
                            {isSubmitting ? (
                                <span className="inline-flex items-center gap-2">
                                    <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                        <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                        <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                                    </svg>
                                    Starting forecast…
                                </span>
                            ) : 'Start forecast'}
                        </Button>
                    </div>
                </div>
            </div>
        </>
    );
}
