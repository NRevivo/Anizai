import { GoogleAuthButton } from '../auth/GoogleAuthButton';

interface HeroProps {
    onAuth: () => void;
}

export function Hero({ onAuth }: HeroProps) {
    return (
        <section className="min-h-screen w-full flex flex-col items-center justify-center px-6 py-24 relative overflow-hidden bg-[#fafbfc]">
            {/* Animated gradient orbs */}
            <div className="absolute inset-0 pointer-events-none overflow-hidden">
                {/* Large teal orb */}
                <div
                    className="absolute -top-40 -left-40 w-[600px] h-[600px] rounded-full opacity-30"
                    style={{
                        background: 'radial-gradient(circle, rgba(20, 184, 166, 0.4) 0%, transparent 70%)',
                        animation: 'float 20s ease-in-out infinite',
                    }}
                />
                {/* Large purple orb */}
                <div
                    className="absolute -bottom-40 -right-40 w-[500px] h-[500px] rounded-full opacity-25"
                    style={{
                        background: 'radial-gradient(circle, rgba(168, 85, 247, 0.35) 0%, transparent 70%)',
                        animation: 'float 25s ease-in-out infinite reverse',
                    }}
                />
                {/* Subtle blue accent */}
                <div
                    className="absolute top-1/3 right-1/4 w-[300px] h-[300px] rounded-full opacity-20"
                    style={{
                        background: 'radial-gradient(circle, rgba(59, 130, 246, 0.3) 0%, transparent 70%)',
                        animation: 'float 18s ease-in-out infinite',
                    }}
                />
            </div>

            {/* Grid pattern overlay */}
            <div
                className="absolute inset-0 pointer-events-none opacity-[0.03]"
                style={{
                    backgroundImage: 'linear-gradient(rgba(0,0,0,0.1) 1px, transparent 1px), linear-gradient(90deg, rgba(0,0,0,0.1) 1px, transparent 1px)',
                    backgroundSize: '60px 60px',
                }}
            />

            <div className="relative z-10 flex flex-col items-center max-w-3xl text-center">
                {/* Logo with glow */}
                <div className="mb-8 relative">
                    <div className="absolute inset-0 blur-2xl opacity-40 scale-150">
                        <div className="w-full h-full bg-gradient-to-r from-anizai-teal-400 via-anizai-blue-400 to-anizai-purple-400 rounded-full" />
                    </div>
                    <img
                        src="/logo-brain.png"
                        alt="Anizai"
                        className="h-48 sm:h-52 w-auto relative mix-blend-multiply"
                    />
                </div>

                {/* Product Name with gradient */}
                <h1 className="text-5xl sm:text-6xl lg:text-7xl font-bold tracking-[-0.03em] mb-6">
                    <span className="bg-gradient-to-r from-gray-900 via-gray-800 to-gray-900 bg-clip-text text-transparent">
                        Anizai
                    </span>
                </h1>

                {/* Tagline - impactful */}
                <h2 className="text-xl sm:text-2xl lg:text-3xl font-medium text-gray-700 mb-5 tracking-[-0.01em] leading-snug max-w-2xl">
                    Forecast the future with
                    <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent font-semibold"> evidence</span>,
                    not guesses.
                </h2>

                {/* Subheadline */}
                <p className="text-base sm:text-lg text-gray-500 mb-10 max-w-lg leading-relaxed">
                    AI-powered probabilities, confidence scores, and real-time evidence tracking — all in one place.
                </p>

                {/* CTA with emphasis */}
                <div className="flex flex-col items-center gap-4 w-full max-w-sm">
                    <GoogleAuthButton onClick={onAuth} className="w-full shadow-lg hover:shadow-xl !h-14 !text-base">
                        Get started — it's free
                    </GoogleAuthButton>

                    <p className="text-[11px] text-gray-400 leading-relaxed">
                        No credit card required •{' '}
                        <a href="#" className="text-gray-500 hover:text-gray-700 underline underline-offset-2">
                            Terms
                        </a>
                        {' '}•{' '}
                        <a href="#" className="text-gray-500 hover:text-gray-700 underline underline-offset-2">
                            Privacy
                        </a>
                    </p>
                </div>

                {/* Stats bar - social proof */}
                <div className="mt-16 flex items-center gap-8 sm:gap-12 text-center">
                    <div>
                        <p className="text-2xl sm:text-3xl font-bold text-gray-900">10K+</p>
                        <p className="text-xs text-gray-500 mt-1">Forecasts made</p>
                    </div>
                    <div className="w-px h-10 bg-gray-200" />
                    <div>
                        <p className="text-2xl sm:text-3xl font-bold text-gray-900">92%</p>
                        <p className="text-xs text-gray-500 mt-1">Accuracy rate</p>
                    </div>
                    <div className="w-px h-10 bg-gray-200" />
                    <div>
                        <p className="text-2xl sm:text-3xl font-bold text-gray-900">500+</p>
                        <p className="text-xs text-gray-500 mt-1">Active users</p>
                    </div>
                </div>

                {/* Scroll indicator */}
                <div className="mt-20 flex flex-col items-center gap-1.5 opacity-40 hover:opacity-70 transition-opacity cursor-pointer">
                    <span className="text-[10px] uppercase tracking-widest text-gray-400 font-medium">
                        See how it works
                    </span>
                    <svg className="w-4 h-4 text-gray-400 animate-bounce" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                </div>
            </div>

            {/* CSS Keyframes */}
            <style>{`
                @keyframes float {
                    0%, 100% { transform: translate(0, 0) scale(1); }
                    25% { transform: translate(30px, -30px) scale(1.05); }
                    50% { transform: translate(-20px, 20px) scale(0.95); }
                    75% { transform: translate(20px, 10px) scale(1.02); }
                }
            `}</style>
        </section>
    );
}


