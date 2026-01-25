import { ArrowLeft, CheckCircle, BookOpen, Code2 } from 'lucide-react';

interface MethodologyPageProps {
    onBack?: () => void;
}

export function MethodologyPage({ onBack }: MethodologyPageProps) {
    return (
        <div className="min-h-screen bg-white">
            {/* Header */}
            <div className="bg-gray-50 border-b border-gray-200 px-6 py-6">
                <div className="max-w-6xl mx-auto flex items-center gap-4">
                    <button
                        onClick={onBack}
                        className="flex items-center gap-2 text-gray-600 hover:text-gray-900 transition-colors"
                    >
                        <ArrowLeft className="w-5 h-5" />
                        <span className="font-medium">Back</span>
                    </button>
                </div>
            </div>

            {/* Main Content */}
            <div className="px-6 py-16">
                <div className="max-w-4xl mx-auto">
                    <div className="mb-12">
                        <h1 className="text-4xl font-bold text-gray-900 mb-4">Our Methodology</h1>
                        <p className="text-xl text-gray-600">
                            How we deliver evidence-based forecasting powered by advanced AI and rigorous analysis.
                        </p>
                    </div>

                    {/* Methodology Steps */}
                    <div className="space-y-8">
                        <div className="border-l-4 border-blue-600 pl-6">
                            <div className="flex items-center gap-3 mb-2">
                                <CheckCircle className="w-6 h-6 text-blue-600" />
                                <h3 className="text-2xl font-semibold text-gray-900">Data Collection</h3>
                            </div>
                            <p className="text-gray-600">
                                We aggregate data from hundreds of sources including market data, news feeds, social media sentiment, and expert analysis to create a comprehensive dataset.
                            </p>
                        </div>

                        <div className="border-l-4 border-blue-600 pl-6">
                            <div className="flex items-center gap-3 mb-2">
                                <Code2 className="w-6 h-6 text-blue-600" />
                                <h3 className="text-2xl font-semibold text-gray-900">Advanced Processing</h3>
                            </div>
                            <p className="text-gray-600">
                                Our machine learning models process this data using state-of-the-art algorithms including neural networks, ensemble methods, and ensemble learning techniques.
                            </p>
                        </div>

                        <div className="border-l-4 border-blue-600 pl-6">
                            <div className="flex items-center gap-3 mb-2">
                                <BookOpen className="w-6 h-6 text-blue-600" />
                                <h3 className="text-2xl font-semibold text-gray-900">Evidence-Based Analysis</h3>
                            </div>
                            <p className="text-gray-600">
                                Every prediction is backed by evidence and reasoning. We provide detailed reports showing the key factors that influenced our forecast.
                            </p>
                        </div>

                        <div className="border-l-4 border-blue-600 pl-6">
                            <div className="flex items-center gap-3 mb-2">
                                <CheckCircle className="w-6 h-6 text-blue-600" />
                                <h3 className="text-2xl font-semibold text-gray-900">Continuous Learning</h3>
                            </div>
                            <p className="text-gray-600">
                                Our models learn from outcomes and continuously improve their accuracy over time through feedback loops and retraining cycles.
                            </p>
                        </div>
                    </div>

                    {/* Key Principles */}
                    <div className="mt-16 bg-blue-50 rounded-lg p-8 border border-blue-200">
                        <h2 className="text-2xl font-semibold text-gray-900 mb-6">Key Principles</h2>
                        <ul className="space-y-3">
                            <li className="flex items-start gap-3">
                                <CheckCircle className="w-6 h-6 text-blue-600 mt-0.5 flex-shrink-0" />
                                <span className="text-gray-700"><strong>Transparency:</strong> All predictions are explainable and backed by data</span>
                            </li>
                            <li className="flex items-start gap-3">
                                <CheckCircle className="w-6 h-6 text-blue-600 mt-0.5 flex-shrink-0" />
                                <span className="text-gray-700"><strong>Accuracy:</strong> We prioritize prediction accuracy through rigorous testing</span>
                            </li>
                            <li className="flex items-start gap-3">
                                <CheckCircle className="w-6 h-6 text-blue-600 mt-0.5 flex-shrink-0" />
                                <span className="text-gray-700"><strong>Objectivity:</strong> Our algorithms are free from human bias and emotion</span>
                            </li>
                            <li className="flex items-start gap-3">
                                <CheckCircle className="w-6 h-6 text-blue-600 mt-0.5 flex-shrink-0" />
                                <span className="text-gray-700"><strong>Continuous Improvement:</strong> We constantly refine our methods based on feedback</span>
                            </li>
                        </ul>
                    </div>
                </div>
            </div>
        </div>
    );
}
