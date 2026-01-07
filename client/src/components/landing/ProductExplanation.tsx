export function ProductExplanation() {
    return (
        <section className="w-full bg-white py-24 px-6">
            <div className="max-w-4xl mx-auto text-center">
                <h2 className="text-3xl font-semibold text-gray-900 mb-6">
                    What Anizai Does
                </h2>
                <p className="text-lg text-gray-600 leading-relaxed mb-12">
                    Anizai analyzes real-time evidence from news, expert consensus, and historical precedent
                    to provide transparent, data-driven probability assessments for future events. Every prediction
                    is backed by traceable sources and clear reasoning.
                </p>
                <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mt-12">
                    <div className="p-6">
                        <div className="w-12 h-12 bg-gradient-to-br from-anizai-teal-100 to-anizai-teal-50 rounded-lg flex items-center justify-center mb-4 mx-auto">
                            <svg className="w-6 h-6 text-anizai-teal-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                            </svg>
                        </div>
                        <h3 className="text-sm font-semibold text-gray-900 mb-2">Evidence-Based</h3>
                        <p className="text-sm text-gray-600">
                            Every forecast is grounded in verifiable data and traceable sources
                        </p>
                    </div>
                    <div className="p-6">
                        <div className="w-12 h-12 bg-gradient-to-br from-anizai-blue-100 to-anizai-blue-50 rounded-lg flex items-center justify-center mb-4 mx-auto">
                            <svg className="w-6 h-6 text-anizai-blue-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                            </svg>
                        </div>
                        <h3 className="text-sm font-semibold text-gray-900 mb-2">Real-Time Analysis</h3>
                        <p className="text-sm text-gray-600">
                            Probabilities update continuously as new information emerges
                        </p>
                    </div>
                    <div className="p-6">
                        <div className="w-12 h-12 bg-gradient-to-br from-anizai-purple-100 to-anizai-purple-50 rounded-lg flex items-center justify-center mb-4 mx-auto">
                            <svg className="w-6 h-6 text-anizai-purple-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                            </svg>
                        </div>
                        <h3 className="text-sm font-semibold text-gray-900 mb-2">Confidence Scoring</h3>
                        <p className="text-sm text-gray-600">
                            Understand not just the probability, but how certain we are
                        </p>
                    </div>
                </div>
                {/* Trusted Sources Indicator */}
                <div className="mt-20 border-t border-gray-100 pt-10">
                    <p className="text-sm font-medium text-gray-400 uppercase tracking-widest mb-6">
                        Analyzing data from trusted global sources
                    </p>
                    <div className="flex flex-wrap justify-center gap-x-12 gap-y-6 grayscale opacity-50">
                        {/* Using text representation for sources instead of logos to avoid external interactions/missing assets, but styled to look like logos */}
                        <span className="text-xl font-serif font-bold text-gray-600">Reuters</span>
                        <span className="text-xl font-serif font-bold text-gray-600">Bloomberg</span>
                        <span className="text-xl font-sans font-bold text-gray-600">Financial Times</span>
                        <span className="text-xl font-serif font-bold text-gray-600">The Economist</span>
                        <span className="text-xl font-sans font-bold text-gray-600 italic">Nature</span>
                    </div>
                </div>
            </div>
        </section>
    );
}
