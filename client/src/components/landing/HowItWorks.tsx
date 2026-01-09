export function HowItWorks() {
    return (
        <section className="w-full bg-gradient-to-b from-gray-50 to-white py-32 px-6">
            <div className="max-w-5xl mx-auto">
                <div className="text-center mb-20">
                    {/* Section badge */}
                    <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-white border border-gray-200 text-xs font-medium text-gray-600 mb-6 shadow-sm">
                        <div className="w-1.5 h-1.5 rounded-full bg-anizai-blue-500" />
                        3 Simple Steps
                    </div>

                    <h2 className="text-3xl sm:text-4xl font-bold text-gray-900 mb-5 tracking-[-0.02em]">
                        From question to insight in seconds
                    </h2>
                    <p className="text-lg text-gray-500 max-w-xl mx-auto">
                        Get evidence-backed probability forecasts for any future event
                    </p>
                </div>

                <div className="relative">
                    {/* Connecting line */}
                    <div className="hidden md:block absolute top-20 left-[calc(16.67%-12px)] right-[calc(16.67%-12px)] h-0.5 bg-gradient-to-r from-anizai-teal-300 via-anizai-blue-300 to-anizai-purple-300" />

                    <div className="grid grid-cols-1 md:grid-cols-3 gap-12 md:gap-8">
                        {/* Step 1 */}
                        <div className="relative text-center">
                            <div className="relative z-10 w-20 h-20 bg-gradient-to-br from-anizai-teal-500 to-anizai-teal-400 rounded-2xl flex items-center justify-center mx-auto mb-8 shadow-xl shadow-anizai-teal-500/25 rotate-3 hover:rotate-0 transition-transform">
                                <span className="text-3xl font-bold text-white">1</span>
                            </div>
                            <h3 className="text-xl font-semibold text-gray-900 mb-3">
                                Ask any question
                            </h3>
                            <p className="text-sm text-gray-500 leading-relaxed max-w-xs mx-auto">
                                Submit any future event you want to forecast. Our AI begins gathering relevant evidence immediately.
                            </p>
                        </div>

                        {/* Step 2 */}
                        <div className="relative text-center">
                            <div className="relative z-10 w-20 h-20 bg-gradient-to-br from-anizai-blue-500 to-anizai-blue-400 rounded-2xl flex items-center justify-center mx-auto mb-8 shadow-xl shadow-anizai-blue-500/25 -rotate-3 hover:rotate-0 transition-transform">
                                <span className="text-3xl font-bold text-white">2</span>
                            </div>
                            <h3 className="text-xl font-semibold text-gray-900 mb-3">
                                We analyze evidence
                            </h3>
                            <p className="text-sm text-gray-500 leading-relaxed max-w-xs mx-auto">
                                Our system gathers news, expert opinions, public sentiment, and historical precedents in real-time.
                            </p>
                        </div>

                        {/* Step 3 */}
                        <div className="relative text-center">
                            <div className="relative z-10 w-20 h-20 bg-gradient-to-br from-anizai-purple-500 to-anizai-purple-400 rounded-2xl flex items-center justify-center mx-auto mb-8 shadow-xl shadow-anizai-purple-500/25 rotate-3 hover:rotate-0 transition-transform">
                                <span className="text-3xl font-bold text-white">3</span>
                            </div>
                            <h3 className="text-xl font-semibold text-gray-900 mb-3">
                                Get actionable insights
                            </h3>
                            <p className="text-sm text-gray-500 leading-relaxed max-w-xs mx-auto">
                                Receive probability forecasts, confidence scores, and a complete evidence timeline that updates live.
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
}

