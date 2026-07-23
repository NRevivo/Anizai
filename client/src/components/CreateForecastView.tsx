import { useState, useEffect } from 'react';
import { Button } from '../components/ui/button';
import { StateMessage } from '../components/ui/StateMessage';
import { ApiError } from '../lib/api';

interface CreateForecastViewProps {
    onSubmit: (question: string, idempotencyKey: string) => Promise<void>;
    onOpenSubscription?: () => void;
    userPlan?: 'free' | 'premium';
    monthlyForecastsUsed?: number;
}

// Mirrors FREE_FORECAST_LIMIT in server/src/repositories/user.repository.ts.
// The server is the source of truth for the limit; this is display-only.
const FREE_FORECAST_LIMIT = 3;

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
    if (!resetAt) {
        return null;
    }

    const parsed = new Date(resetAt);
    if (Number.isNaN(parsed.getTime())) {
        return null;
    }

    return parsed.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric',
    });
}

export function CreateForecastView({ onSubmit, onOpenSubscription, userPlan = 'free', monthlyForecastsUsed = 0 }: CreateForecastViewProps) {
    const isPremium = userPlan === 'premium';
    const forecastsRemaining = Math.max(0, FREE_FORECAST_LIMIT - monthlyForecastsUsed);
    const freeUsageLabel =
        forecastsRemaining === 0
            ? 'No free forecasts left this month'
            : `${forecastsRemaining} of ${FREE_FORECAST_LIMIT} free forecasts left this month`;
    const [question, setQuestion] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [formError, setFormError] = useState<string | null>(null);
    const [planLimitDetails, setPlanLimitDetails] = useState<PlanLimitDetails | null>(null);
    const [showValidation, setShowValidation] = useState(false);
    const [hints, setHints] = useState<string[]>([]);

    const placeholders = [
        "Will the ECB cut rates before July 2026?",
        "Will Bitcoin trade above $150k before January 2027?",
        "Will SpaceX complete a crewed Mars landing before 2030?",
        "Will US inflation fall below 2.5% in 2026?",
        "Will semantic search overtake keyword search by 2027?"
    ];
    const [placeholderIndex, setPlaceholderIndex] = useState(0);
    const [fade, setFade] = useState(true);
    const [idempotencyKey, setIdempotencyKey] = useState(() => crypto.randomUUID());

    const minLength = 10;
    const maxLength = 500;
    const trimmedQuestion = question.trim();
    const isEmpty = trimmedQuestion.length === 0;
    const isTooShort = !isEmpty && trimmedQuestion.length < minLength;
    const isTooLong = trimmedQuestion.length > maxLength;
    const isValid = !isEmpty && !isTooShort && !isTooLong;

    useEffect(() => {
        if (question) return;

        const interval = setInterval(() => {
            setFade(false);
            setTimeout(() => {
                setPlaceholderIndex((prev) => (prev + 1) % placeholders.length);
                setFade(true);
            }, 200);
        }, 3000);

        return () => clearInterval(interval);
    }, [question]);

    useEffect(() => {
        const newHints: string[] = [];
        const q = question.toLowerCase();

        if (q.match(/\d{4}|q[1-4]|january|february|march|april|may|june|july|august|september|october|november|december/)) {
            newHints.push('Timeframe included');
        }

        if (q.startsWith('will ') || q.includes('yes or no')) {
            newHints.push('Clear yes/no outcome');
        }

        setHints(newHints);
    }, [question]);

    const validationMessage = isEmpty
        ? 'Enter a forecast question to continue.'
        : isTooShort
            ? `Use at least ${minLength} characters so the forecast is specific enough.`
            : isTooLong
                ? `Keep the forecast under ${maxLength} characters.`
                : null;

    const displayError = formError ?? (showValidation ? validationMessage : null);

    const getErrorMessage = (error: unknown): string => {
        const planLimit = getPlanLimitDetails(error);
        if (planLimit) {
            return "You've used your free forecasts this month.";
        }

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
            await onSubmit(trimmedQuestion, idempotencyKey);
            setIdempotencyKey(crypto.randomUUID());
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
        <div className="min-h-full flex flex-col justify-center px-4 sm:px-6 lg:px-8 py-5 sm:py-6 max-w-3xl mx-auto w-full max-w-full overflow-x-hidden font-sans">
            <div className="space-y-5 sm:space-y-6 min-w-0">
                <div className="space-y-2 animate-fadeIn">
                    <h1 className="text-2xl lg:text-3xl font-bold text-gray-900 tracking-tight leading-tight">Create a forecast</h1>
                    <p className="text-base text-gray-500 max-w-xl leading-relaxed">
                        Ask a specific future-facing question. Anizai will estimate probability, confidence, and supporting evidence.
                    </p>
                </div>

                <div className="relative group animate-fadeIn" style={{ animationDelay: '100ms' }}>
                    <div className="relative">
                        <textarea
                            value={question}
                            onChange={(e) => {
                                setQuestion(e.target.value);
                                if (formError) {
                                    setFormError(null);
                                }
                            }}
                            placeholder={placeholders[placeholderIndex]}
                            disabled={isSubmitting}
                            aria-invalid={Boolean(displayError)}
                            className={`w-full min-h-[116px] sm:min-h-[132px] bg-transparent text-lg sm:text-xl lg:text-2xl font-medium text-gray-900 placeholder-gray-300 border-none focus:ring-0 p-0 resize-none transition-all duration-300 disabled:cursor-wait disabled:opacity-60 ${!question && !fade ? 'placeholder-opacity-0' : 'placeholder-opacity-100'}`}
                            style={{ lineHeight: '1.4' }}
                            maxLength={maxLength}
                            autoFocus
                        />

                        {/* Subtle Underline Indicator */}
                        <div className={`absolute bottom-0 left-0 right-0 h-px bg-gray-200 transition-all duration-300 ${question ? 'bg-anizai-teal-500 scale-x-100' : 'scale-x-100 group-focus-within:bg-anizai-teal-500'}`}></div>
                    </div>

                    {/* Intelligent Feedback & Counter */}
                    <div className="flex flex-wrap items-center justify-between gap-2 mt-4 min-h-6">
                        <div className="flex flex-wrap gap-2">
                            {hints.map((hint, index) => (
                                <span key={index} className="inline-flex items-center gap-1.5 text-sm font-medium text-anizai-teal-600 animate-fadeIn">
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                    </svg>
                                    {hint}
                                </span>
                            ))}
                        </div>
                        <span className={`text-sm font-medium transition-colors ${trimmedQuestion.length > maxLength * 0.9 ? 'text-amber-500' : 'text-gray-300'}`}>
                            {trimmedQuestion.length}/{maxLength}
                        </span>
                    </div>

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
                                        className="inline-flex min-h-10 items-center justify-center rounded-md bg-gray-900 px-4 text-sm font-semibold text-white hover:bg-gray-800"
                                    >
                                        Review plans
                                    </button>
                                ) : null}
                            />
                        </div>
                    ) : null}
                </div>

                <div className="pt-2 flex flex-col sm:flex-row sm:items-center gap-3 sm:gap-4 animate-fadeIn" style={{ animationDelay: '200ms' }}>
                    <Button
                        onClick={handleSubmit}
                        disabled={isSubmitting}
                        className="h-12 w-full sm:w-auto px-6 sm:px-8 bg-gray-900 hover:bg-gray-800 text-white text-base font-semibold rounded-lg transition-colors shadow-[0_1px_2px_rgba(15,23,42,0.08)] hover:shadow disabled:opacity-50 disabled:cursor-not-allowed border-0"
                    >
                        {isSubmitting ? (
                            <span className="inline-flex items-center gap-2">
                                <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none" aria-hidden="true">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                                </svg>
                                <span className="truncate">Starting forecast...</span>
                            </span>
                        ) : 'Start forecast'}
                    </Button>

                    <span className="text-sm text-gray-400 font-medium">
                        {isSubmitting
                            ? 'Creating the forecast workspace...'
                            : isPremium
                                ? 'Unlimited forecasts on your plan'
                                : freeUsageLabel}
                    </span>
                </div>

                <div className="pt-5 border-t border-gray-100 animate-fadeIn" style={{ animationDelay: '300ms' }}>
                    <div className="flex flex-wrap gap-4 sm:gap-6 text-gray-400">
                        <div className="group flex items-center gap-2 cursor-help" title="Probability estimate">
                            <span className="p-2 rounded-full bg-gray-50 group-hover:bg-anizai-teal-50 group-hover:text-anizai-teal-600 transition-colors">
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                                </svg>
                            </span>
                            <span className="text-sm font-medium hidden group-hover:inline-block text-gray-600">Probability</span>
                        </div>
                        <div className="group flex items-center gap-2 cursor-help" title="Confidence score">
                            <span className="p-2 rounded-full bg-gray-50 group-hover:bg-anizai-teal-50 group-hover:text-anizai-teal-600 transition-colors">
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                            </span>
                            <span className="text-sm font-medium hidden group-hover:inline-block text-gray-600">Confidence</span>
                        </div>
                        <div className="group flex items-center gap-2 cursor-help" title="Evidence timeline">
                            <span className="p-2 rounded-full bg-gray-50 group-hover:bg-anizai-teal-50 group-hover:text-anizai-teal-600 transition-colors">
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                            </span>
                            <span className="text-sm font-medium hidden group-hover:inline-block text-gray-600">Evidence</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
