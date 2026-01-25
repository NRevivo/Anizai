export function ProductExplanation() {
    return (
        <section className="w-full bg-white py-32 px-6 relative overflow-hidden">
            {/* Subtle background accent */}
            <div className="absolute top-0 left-1/2 -translate-x-1/2 w-[800px] h-[400px] opacity-[0.03]"
                style={{
                    background: 'radial-gradient(ellipse, rgba(59, 130, 246, 0.8), transparent 70%)',
                }}
            />

            <div className="max-w-5xl mx-auto text-center relative z-10">
                {/* Section badge */}
                <div className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full bg-gray-100 text-xs font-medium text-gray-600 mb-6">
                    <div className="w-1.5 h-1.5 rounded-full bg-anizai-teal-500" />
                    How it works
                </div>

                <h2 className="text-3xl sm:text-4xl font-bold text-gray-900 mb-5 tracking-[-0.02em]">
                    Evidence-powered forecasting
                </h2>
                <p className="text-lg text-gray-500 leading-relaxed mb-16 max-w-2xl mx-auto">
                    Anizai synthesizes real-time data from news, expert consensus, and historical precedent
                    into transparent, traceable probability assessments.
                </p>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-6">
                    {/* Card 1 */}
                    <div className="group relative p-8 rounded-2xl bg-gradient-to-br from-white to-gray-50 border border-gray-100 hover:border-anizai-teal-200 hover:shadow-lg transition-all duration-300">
                        <div className="w-14 h-14 bg-gradient-to-br from-anizai-teal-500 to-anizai-teal-400 rounded-xl flex items-center justify-center mb-5 shadow-lg shadow-anizai-teal-500/20 group-hover:scale-110 transition-transform">
                            <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                        </div>
                        <h3 className="text-lg font-semibold text-gray-900 mb-2">Evidence-Based</h3>
                        <p className="text-sm text-gray-500 leading-relaxed">
                            Every forecast is grounded in verifiable data with traceable sources you can audit.
                        </p>
                    </div>

                    {/* Card 2 */}
                    <div className="group relative p-8 rounded-2xl bg-gradient-to-br from-white to-gray-50 border border-gray-100 hover:border-anizai-blue-200 hover:shadow-lg transition-all duration-300">
                        <div className="w-14 h-14 bg-gradient-to-br from-anizai-blue-500 to-anizai-blue-400 rounded-xl flex items-center justify-center mb-5 shadow-lg shadow-anizai-blue-500/20 group-hover:scale-110 transition-transform">
                            <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                            </svg>
                        </div>
                        <h3 className="text-lg font-semibold text-gray-900 mb-2">Real-Time Updates</h3>
                        <p className="text-sm text-gray-500 leading-relaxed">
                            Probabilities update continuously as new information emerges from global sources.
                        </p>
                    </div>

                    {/* Card 3 */}
                    <div className="group relative p-8 rounded-2xl bg-gradient-to-br from-white to-gray-50 border border-gray-100 hover:border-anizai-purple-200 hover:shadow-lg transition-all duration-300">
                        <div className="w-14 h-14 bg-gradient-to-br from-anizai-purple-500 to-anizai-purple-400 rounded-xl flex items-center justify-center mb-5 shadow-lg shadow-anizai-purple-500/20 group-hover:scale-110 transition-transform">
                            <svg className="w-7 h-7 text-white" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                            </svg>
                        </div>
                        <h3 className="text-lg font-semibold text-gray-900 mb-2">Confidence Scoring</h3>
                        <p className="text-sm text-gray-500 leading-relaxed">
                            Not just probability — understand exactly how certain we are about each prediction.
                        </p>
                    </div>
                </div>

                {/* Trusted Sources */}
                <div className="mt-24 pt-16 border-t border-gray-100">
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-widest mb-8">
                        Analyzing data from trusted global sources
                    </p>
                    <div className="flex flex-wrap justify-center items-center gap-x-10 gap-y-6">
                        <span className="text-xl font-serif font-bold text-gray-300 hover:text-gray-500 transition-colors">Reuters</span>
                        <span className="text-xl font-serif font-bold text-gray-300 hover:text-gray-500 transition-colors">Bloomberg</span>
                        <span className="text-xl font-sans font-bold text-gray-300 hover:text-gray-500 transition-colors">Financial Times</span>
                        <span className="text-xl font-serif font-bold text-gray-300 hover:text-gray-500 transition-colors">The Economist</span>
                        <span className="text-xl font-sans font-bold text-gray-300 hover:text-gray-500 transition-colors italic">Nature</span>
                    </div>
                </div>
            </div>
        </section>
    );
}

