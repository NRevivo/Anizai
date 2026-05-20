import { Button } from '../ui/button';

interface PlanFeature {
    text: string;
    included: boolean;
}

interface PlanCardProps {
    name: string;
    price: string;
    priceSuffix?: string;
    description: string;
    features: PlanFeature[];
    onSelect: () => void;
    isPremium?: boolean;
}

/**
 * Card used by the pricing page. Matches the bento card visual language —
 * rounded-2xl + soft elevation + hairline ring. The "premium" variant is
 * marked with a subtle lavender accent on the ring and a recommended pill,
 * not by switching to a gradient background.
 */
export function PlanCard({
    name,
    price,
    priceSuffix,
    description,
    features,
    onSelect,
    isPremium,
}: PlanCardProps) {
    return (
        <article
            className={`relative rounded-2xl bg-white p-8 flex flex-col shadow-[0_4px_24px_rgba(15,23,42,0.05),0_1px_3px_rgba(15,23,42,0.04)] ${
                isPremium
                    ? 'ring-1 ring-anizai-purple-300/70'
                    : 'ring-1 ring-slate-900/[0.05]'
            }`}
        >
            {isPremium && (
                <div className="absolute -top-3 left-7">
                    <span className="inline-flex items-center gap-1.5 rounded-full bg-anizai-purple-50 px-3 py-1 ring-1 ring-anizai-purple-200">
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-500" />
                        <span className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-anizai-purple-700">
                            Recommended
                        </span>
                    </span>
                </div>
            )}

            <div className="mb-6">
                <p className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-slate-500">
                    {name}
                </p>
                <div className="mt-3 flex items-baseline gap-2">
                    <span className="text-5xl font-light tracking-tight text-gray-900 tabular-nums">
                        {price}
                    </span>
                    {priceSuffix ? (
                        <span className="text-[13px] text-slate-500">{priceSuffix}</span>
                    ) : null}
                </div>
                <p className="mt-4 text-[14px] leading-[1.6] text-slate-600 max-w-xs">
                    {description}
                </p>
            </div>

            <div className="h-px bg-slate-200/70 my-2" />

            <ul className="space-y-3 my-6 flex-1">
                {features.map((feature) => (
                    <li key={feature.text} className="flex items-start gap-3">
                        {feature.included ? (
                            <svg
                                className="w-4 h-4 text-anizai-teal-500 shrink-0 mt-0.5"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth={2.5}
                                aria-hidden
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
                            </svg>
                        ) : (
                            <svg
                                className="w-4 h-4 text-slate-300 shrink-0 mt-0.5"
                                viewBox="0 0 24 24"
                                fill="none"
                                stroke="currentColor"
                                strokeWidth={2}
                                aria-hidden
                            >
                                <path strokeLinecap="round" strokeLinejoin="round" d="M6 18L18 6M6 6l12 12" />
                            </svg>
                        )}
                        <span
                            className={`text-[14px] leading-snug ${
                                feature.included ? 'text-slate-700' : 'text-slate-400'
                            }`}
                        >
                            {feature.text}
                        </span>
                    </li>
                ))}
            </ul>

            <Button
                onClick={onSelect}
                className={
                    isPremium
                        ? 'w-full h-11 justify-center text-[14.5px] font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-lg shadow-[0_1px_2px_rgba(15,23,42,0.08)]'
                        : 'w-full h-11 justify-center text-[14.5px] font-medium bg-white text-gray-900 hover:bg-slate-50 rounded-lg ring-1 ring-slate-200'
                }
            >
                {isPremium ? 'Start Premium' : 'Start for free'}
            </Button>
        </article>
    );
}
