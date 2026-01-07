import { GoogleAuthButton } from '../auth/GoogleAuthButton';

interface HeroProps {
    onAuth: () => void;
}

export function Hero({ onAuth }: HeroProps) {
    return (
        <div className="min-h-screen w-full flex flex-col items-center justify-center bg-gray-50 px-6 relative overflow-hidden">
            {/* Subtle radial gradient background */}
            <div className="absolute inset-0 bg-gradient-radial from-anizai-teal-50/20 via-transparent to-transparent opacity-40" />

            <div className="relative z-10 flex flex-col items-center max-w-3xl">
                {/* Brain Icon Logo */}
                <div className="mb-6">
                    <img
                        src="/logo-brain.png"
                        alt="Anizai Brain"
                        className="h-20 w-auto drop-shadow-lg"
                    />
                </div>

                {/* Product Name as Text */}
                <h1 className="text-5xl font-semibold text-gray-900 mb-8 tracking-tight">
                    Anizai
                </h1>

                {/* Main Headline */}
                <h2 className="text-3xl md:text-4xl font-medium text-gray-900 text-center mb-4 leading-tight">
                    Forecast future events with evidence, not guesses.
                </h2>

                {/* Sub-headline */}
                <p className="text-lg text-gray-600 text-center mb-12 max-w-2xl leading-relaxed">
                    Probabilities, confidence scores, and evidence-backed timelines for real-world events.
                </p>

                {/* Primary Action */}
                <div className="flex flex-col items-center gap-6">
                    <GoogleAuthButton onClick={onAuth}>
                        Get started with Google
                    </GoogleAuthButton>

                    {/* Legal Text */}
                    <p className="text-xs text-gray-400 text-center max-w-md">
                        By continuing, you agree to our{' '}
                        <a href="#" className="text-gray-500 hover:text-gray-700 underline">
                            Terms of Service
                        </a>{' '}
                        and{' '}
                        <a href="#" className="text-gray-500 hover:text-gray-700 underline">
                            Privacy Policy
                        </a>
                    </p>
                </div>

                {/* Scroll indicator */}
                <div className="mt-20 flex flex-col items-center gap-2 animate-bounce">
                    <p className="text-xs text-gray-400">Learn more</p>
                    <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                </div>
            </div>
        </div>
    );
}
