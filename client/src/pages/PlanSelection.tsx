import { PlanCard } from '../components/plans/PlanCard';

interface PlanSelectionProps {
    onSelectPlan: (plan: 'free' | 'premium') => void;
}

export function PlanSelection({ onSelectPlan }: PlanSelectionProps) {
    const freePlan = {
        name: 'Free',
        price: 'Free',
        description: 'Perfect for exploring Anizai and tracking a few key predictions',
        features: [
            { text: 'Up to 3 prediction sessions', included: true },
            { text: 'Basic probability and confidence', included: true },
            { text: 'Limited evidence timeline', included: true },
            { text: 'Full evidence timeline', included: false },
            { text: 'Reliability breakdown', included: false },
            { text: 'Alerts and event tracking', included: false },
            { text: 'Historical comparisons', included: false },
        ]
    };

    const premiumPlan = {
        name: 'Premium',
        price: '$19',
        description: 'Full access to all features for serious forecasting and analysis',
        features: [
            { text: 'Unlimited prediction sessions', included: true },
            { text: 'Full probability and confidence metrics', included: true },
            { text: 'Complete evidence timeline', included: true },
            { text: 'Turning point detection', included: true },
            { text: 'Reliability and source scoring', included: true },
            { text: 'Real-time alerts and tracking', included: true },
            { text: 'Comparison to similar past events', included: true },
        ]
    };

    return (
        <div className="h-screen w-full bg-gray-50 overflow-y-auto">
            <div className="max-w-6xl w-full mx-auto px-6 py-12">
                {/* Header */}
                <div className="text-center mb-12">
                    <h1 className="text-4xl font-semibold bg-gradient-to-r from-anizai-teal-700 via-anizai-blue-700 to-anizai-purple-700 bg-clip-text text-transparent mb-4">
                        Welcome to Anizai
                    </h1>
                    <p className="text-lg text-gray-600 max-w-2xl mx-auto">
                        Choose the plan that fits your forecasting needs. You can upgrade or downgrade anytime.
                    </p>
                </div>

                {/* Plans Grid */}
                <div className="grid grid-cols-1 md:grid-cols-2 gap-8 max-w-5xl mx-auto">
                    <PlanCard
                        {...freePlan}
                        onSelect={() => onSelectPlan('free')}
                    />
                    <PlanCard
                        {...premiumPlan}
                        onSelect={() => onSelectPlan('premium')}
                        isPremium
                    />
                </div>

                {/* Footer Note */}
                <p className="text-center text-sm text-gray-500 mt-8">
                    All plans include access to the core Anizai platform and community features
                </p>
            </div>
        </div>
    );
}
