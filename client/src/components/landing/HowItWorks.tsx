export function HowItWorks() {
    return (
        <section className="w-full bg-white py-24 px-6">
            <div className="max-w-5xl mx-auto">
                <div className="text-center mb-16">
                    <h2 className="text-3xl font-semibold text-gray-900 mb-4">
                        How It Works
                    </h2>
                    <p className="text-lg text-gray-600">
                        Three simple steps to evidence-based forecasting
                    </p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-12">
                    {/* Step 1 */}
                    <div className="text-center">
                        <div className="w-16 h-16 bg-gradient-to-br from-anizai-teal-500 to-anizai-teal-400 rounded-full flex items-center justify-center mx-auto mb-6">
                            <span className="text-2xl font-bold text-white">1</span>
                        </div>
                        <h3 className="text-lg font-semibold text-gray-900 mb-3">
                            Ask a Question
                        </h3>
                        <p className="text-sm text-gray-600 leading-relaxed">
                            Submit any future event you want to forecast. Our AI begins gathering relevant evidence immediately.
                        </p>
                    </div>

                    {/* Step 2 */}
                    <div className="text-center">
                        <div className="w-16 h-16 bg-gradient-to-br from-anizai-blue-500 to-anizai-blue-400 rounded-full flex items-center justify-center mx-auto mb-6">
                            <span className="text-2xl font-bold text-white">2</span>
                        </div>
                        <h3 className="text-lg font-semibold text-gray-900 mb-3">
                            We Analyze Evidence
                        </h3>
                        <p className="text-sm text-gray-600 leading-relaxed">
                            Our system gathers and analyzes news, expert opinions, public sentiment, and historical precedents.
                        </p>
                    </div>

                    {/* Step 3 */}
                    <div className="text-center">
                        <div className="w-16 h-16 bg-gradient-to-br from-anizai-purple-500 to-anizai-purple-400 rounded-full flex items-center justify-center mx-auto mb-6">
                            <span className="text-2xl font-bold text-white">3</span>
                        </div>
                        <h3 className="text-lg font-semibold text-gray-900 mb-3">
                            You Receive Insights
                        </h3>
                        <p className="text-sm text-gray-600 leading-relaxed">
                            Get probability forecasts, confidence scores, and a complete evidence timeline that updates in real-time.
                        </p>
                    </div>
                </div>
            </div>
        </section>
    );
}
