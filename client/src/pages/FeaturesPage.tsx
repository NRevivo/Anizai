import { ArrowLeft, BarChart3, Brain, TrendingUp, Zap, Shield, Users } from 'lucide-react';

interface FeaturesPageProps {
    onBack?: () => void;
}

export function FeaturesPage({ onBack }: FeaturesPageProps) {
    const features = [
        {
            icon: Brain,
            title: 'AI-Powered Analysis',
            description: 'Advanced machine learning algorithms analyze complex market data to generate accurate predictions.'
        },
        {
            icon: BarChart3,
            title: 'Real-Time Data',
            description: 'Access live market data and historical trends to make informed forecasting decisions.'
        },
        {
            icon: TrendingUp,
            title: 'Trend Analysis',
            description: 'Identify emerging trends and patterns before they become mainstream.'
        },
        {
            icon: Zap,
            title: 'Fast Processing',
            description: 'Get instant predictions and analysis with our optimized processing engine.'
        },
        {
            icon: Shield,
            title: 'Secure & Private',
            description: 'Enterprise-grade security ensures your data and predictions remain confidential.'
        },
        {
            icon: Users,
            title: 'Team Collaboration',
            description: 'Share forecasts and insights with your team members in real-time.'
        }
    ];

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
                <div className="max-w-6xl mx-auto">
                    <div className="mb-16">
                        <h1 className="text-4xl font-bold text-gray-900 mb-4">Features</h1>
                        <p className="text-xl text-gray-600">
                            Powerful tools designed to help you forecast with confidence.
                        </p>
                    </div>

                    {/* Features Grid */}
                    <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-8">
                        {features.map((feature, index) => {
                            const Icon = feature.icon;
                            return (
                                <div key={index} className="bg-gray-50 rounded-lg p-8 hover:shadow-lg transition-shadow">
                                    <Icon className="w-12 h-12 text-blue-600 mb-4" />
                                    <h3 className="text-lg font-semibold text-gray-900 mb-2">{feature.title}</h3>
                                    <p className="text-gray-600">{feature.description}</p>
                                </div>
                            );
                        })}
                    </div>
                </div>
            </div>
        </div>
    );
}
