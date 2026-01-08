import { useState, useEffect } from 'react';
import { Button } from '../components/ui/button';

interface CreateForecastViewProps {
    onSubmit: (question: string) => void;
}

export function CreateForecastView({ onSubmit }: CreateForecastViewProps) {
    const [question, setQuestion] = useState('');
    const [isSubmitting, setIsSubmitting] = useState(false);
    const [hints, setHints] = useState<string[]>([]);

    // Dynamic placeholders
    const placeholders = [
        "Will the ECB cut interest rates before Q3 2026?",
        "Probability of Bitcoin reaching $150k before 2027",
        "Will SpaceX complete a crewed Mars landing by 2026?",
        "Will the US inflation rate drop below 2.5% in 2026?",
        "Likelihood of semantic search replacing keyword search by 2027"
    ];
    const [placeholderIndex, setPlaceholderIndex] = useState(0);
    const [fade, setFade] = useState(true);

    const minLength = 10;
    const maxLength = 500;
    const isValid = question.trim().length >= minLength && question.trim().length <= maxLength;

    // Rotate placeholders
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

    // Intelligent feedback
    useEffect(() => {
        const newHints: string[] = [];
        const q = question.toLowerCase();

        if (q.match(/\d{4}|q[1-4]|january|february|march|april|may|june|july|august|september|october|november|december/)) {
            newHints.push('Clear timeframe detected');
        }

        if (q.startsWith('will ') || q.includes('yes or no')) {
            newHints.push('Binary outcome detected');
        }

        setHints(newHints);
    }, [question]);

    const handleSubmit = async () => {
        if (!isValid) return;
        setIsSubmitting(true);
        await new Promise(resolve => setTimeout(resolve, 500));
        onSubmit(question.trim());
        setIsSubmitting(false);
    };

    return (
        <div className="h-full flex flex-col justify-center px-8 lg:px-12 max-w-4xl mx-auto w-full font-sans">
            <div className="space-y-8 -mt-20">
                {/* Clean Header */}
                <div className="space-y-2 animate-fadeIn">
                    <h1 className="text-3xl font-bold text-gray-900 tracking-tight">What do you want to forecast?</h1>
                    <p className="text-lg text-gray-500 max-w-xl leading-relaxed">
                        Ask about a future event to receive a detailed probability analysis and evidence timeline.
                    </p>
                </div>

                {/* Hero Question Input */}
                <div className="relative group animate-fadeIn" style={{ animationDelay: '100ms' }}>
                    <div className="relative">
                        <textarea
                            value={question}
                            onChange={(e) => setQuestion(e.target.value)}
                            placeholder={placeholders[placeholderIndex]}
                            className={`w-full min-h-[160px] bg-transparent text-2xl lg:text-3xl font-medium text-gray-900 placeholder-gray-300 border-none focus:ring-0 p-0 resize-none transition-all duration-300 ${!question && !fade ? 'placeholder-opacity-0' : 'placeholder-opacity-100'}`}
                            style={{ lineHeight: '1.4' }}
                            maxLength={maxLength}
                            autoFocus
                        />

                        {/* Subtle Underline Indicator */}
                        <div className={`absolute bottom-0 left-0 right-0 h-px bg-gray-200 transition-all duration-300 ${question ? 'bg-anizai-teal-500 scale-x-100' : 'scale-x-100 group-focus-within:bg-anizai-teal-500'}`}></div>
                    </div>

                    {/* Intelligent Feedback & Counter */}
                    <div className="flex items-center justify-between mt-4 h-6">
                        <div className="flex gap-3">
                            {hints.map((hint, index) => (
                                <span key={index} className="inline-flex items-center gap-1.5 text-sm font-medium text-anizai-teal-600 animate-fadeIn">
                                    <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                    </svg>
                                    {hint}
                                </span>
                            ))}
                        </div>
                        <span className={`text-sm font-medium transition-colors ${question.length > maxLength * 0.9 ? 'text-amber-500' : 'text-gray-300'}`}>
                            {question.length}/{maxLength}
                        </span>
                    </div>
                </div>

                {/* Action Area */}
                <div className="pt-4 flex items-center gap-4 animate-fadeIn" style={{ animationDelay: '200ms' }}>
                    <Button
                        onClick={handleSubmit}
                        disabled={!isValid || isSubmitting}
                        className="h-12 px-8 bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 hover:opacity-90 text-white text-base font-semibold rounded-lg transition-all shadow-sm hover:shadow-md disabled:opacity-50 disabled:cursor-not-allowed border-0"
                    >
                        {isSubmitting ? 'Analyzing...' : 'Analyze Forecast'}
                    </Button>

                    <span className="text-sm text-gray-400 font-medium">
                        Uses 1 of 3 free forecasts
                    </span>
                </div>

                {/* Minimal "What You Get" Icons */}
                <div className="pt-8 border-t border-gray-100 animate-fadeIn" style={{ animationDelay: '300ms' }}>
                    <div className="flex gap-8 text-gray-400">
                        <div className="group flex items-center gap-2 cursor-help" title="Probability Estimate">
                            <span className="p-2 rounded-full bg-gray-50 group-hover:bg-anizai-teal-50 group-hover:text-anizai-teal-600 transition-colors">
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                                </svg>
                            </span>
                            <span className="text-sm font-medium hidden group-hover:inline-block text-gray-600">Probability</span>
                        </div>
                        <div className="group flex items-center gap-2 cursor-help" title="Confidence Score">
                            <span className="p-2 rounded-full bg-gray-50 group-hover:bg-anizai-teal-50 group-hover:text-anizai-teal-600 transition-colors">
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                            </span>
                            <span className="text-sm font-medium hidden group-hover:inline-block text-gray-600">Confidence</span>
                        </div>
                        <div className="group flex items-center gap-2 cursor-help" title="Evidence Timeline">
                            <span className="p-2 rounded-full bg-gray-50 group-hover:bg-anizai-teal-50 group-hover:text-anizai-teal-600 transition-colors">
                                <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 8v4l3 3m6-3a9 9 0 11-18 0 9 9 0 0118 0z" />
                                </svg>
                            </span>
                            <span className="text-sm font-medium hidden group-hover:inline-block text-gray-600">Timeline</span>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
