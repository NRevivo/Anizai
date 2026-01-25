import { ArrowLeft, Users, Target, Lightbulb } from 'lucide-react';

interface AboutPageProps {
    onBack?: () => void;
}

export function AboutPage({ onBack }: AboutPageProps) {
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
                        <h1 className="text-4xl font-bold text-gray-900 mb-4">About Anizai</h1>
                        <p className="text-xl text-gray-600">
                            Transforming uncertainty into confidence through evidence-based forecasting.
                        </p>
                    </div>

                    {/* Story Section */}
                    <div className="mb-16">
                        <h2 className="text-2xl font-semibold text-gray-900 mb-4">Our Story</h2>
                        <p className="text-gray-700 mb-4 leading-relaxed">
                            Anizai was founded with a simple belief: in an increasingly complex world, better predictions should be accessible to everyone. Our team of data scientists, economists, and forecasting experts came together to solve one of the most challenging problems in modern business—making accurate predictions about the future.
                        </p>
                        <p className="text-gray-700 leading-relaxed">
                            We built Anizai to combine the power of artificial intelligence with time-tested forecasting methodologies. The result is a platform that doesn't just give you predictions, but explains the reasoning behind them—making you more confident in your decisions.
                        </p>
                    </div>

                    {/* Mission Section */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-8 mb-16">
                        <div className="bg-blue-50 rounded-lg p-8 border border-blue-200">
                            <Target className="w-8 h-8 text-blue-600 mb-4" />
                            <h3 className="text-lg font-semibold text-gray-900 mb-2">Our Mission</h3>
                            <p className="text-gray-700">
                                To empower organizations with accurate, evidence-based forecasting tools that drive better decision-making.
                            </p>
                        </div>
                        <div className="bg-blue-50 rounded-lg p-8 border border-blue-200">
                            <Lightbulb className="w-8 h-8 text-blue-600 mb-4" />
                            <h3 className="text-lg font-semibold text-gray-900 mb-2">Our Vision</h3>
                            <p className="text-gray-700">
                                A world where data-driven insights are available to organizations of all sizes, empowering confident decision-making.
                            </p>
                        </div>
                        <div className="bg-blue-50 rounded-lg p-8 border border-blue-200">
                            <Users className="w-8 h-8 text-blue-600 mb-4" />
                            <h3 className="text-lg font-semibold text-gray-900 mb-2">Our Values</h3>
                            <p className="text-gray-700">
                                Transparency, accuracy, integrity, and continuous improvement guide everything we do.
                            </p>
                        </div>
                    </div>

                    {/* Team Section */}
                    <div>
                        <h2 className="text-2xl font-semibold text-gray-900 mb-6">Our Team</h2>
                        <p className="text-gray-700 mb-8">
                            Our team is composed of experienced professionals from leading tech companies, research institutions, and financial firms. Together, we bring decades of expertise in machine learning, data science, and forecasting.
                        </p>
                        <div className="bg-gray-50 rounded-lg p-8">
                            <p className="text-gray-600 text-center">
                                Founded in 2023 • Based Globally • 50+ Team Members
                            </p>
                        </div>
                    </div>
                </div>
            </div>
        </div>
    );
}
