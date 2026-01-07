import { Button } from '../ui/button';

interface PlanFeature {
    text: string;
    included: boolean;
}

interface PlanCardProps {
    name: string;
    price: string;
    description: string;
    features: PlanFeature[];
    onSelect: () => void;
    isPremium?: boolean;
}

export function PlanCard({ name, price, description, features, onSelect, isPremium }: PlanCardProps) {
    return (
        <div className={`bg-white rounded-lg border shadow-sm p-8 flex flex-col ${isPremium ? 'border-anizai-teal-400 shadow-lg' : 'border-gray-200'
            }`}>
            {isPremium && (
                <div className="mb-4">
                    <span className="inline-block px-3 py-1 text-xs font-medium bg-gradient-to-r from-anizai-teal-100 to-anizai-blue-100 text-anizai-teal-700 rounded-full">
                        Recommended
                    </span>
                </div>
            )}

            <h3 className="text-2xl font-semibold text-gray-900 mb-2">{name}</h3>
            <div className="mb-4">
                <span className="text-4xl font-bold text-gray-900">{price}</span>
                {price !== 'Free' && <span className="text-gray-500 ml-2">/month</span>}
            </div>
            <p className="text-sm text-gray-600 mb-8">{description}</p>

            <ul className="space-y-3 mb-8 flex-1">
                {features.map((feature, index) => (
                    <li key={index} className="flex items-start gap-3">
                        {feature.included ? (
                            <svg className="w-5 h-5 text-anizai-teal-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                            </svg>
                        ) : (
                            <svg className="w-5 h-5 text-gray-300 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                            </svg>
                        )}
                        <span className={`text-sm ${feature.included ? 'text-gray-700' : 'text-gray-400'}`}>
                            {feature.text}
                        </span>
                    </li>
                ))}
            </ul>

            <Button
                onClick={onSelect}
                className={`w-full justify-center ${isPremium
                        ? 'bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 hover:from-anizai-teal-600 hover:via-anizai-blue-600 hover:to-anizai-purple-600 text-white border-0'
                        : 'bg-white hover:bg-gray-50 text-gray-900 border border-gray-300'
                    }`}
            >
                {isPremium ? 'Start Premium' : 'Start for Free'}
            </Button>
        </div>
    );
}
