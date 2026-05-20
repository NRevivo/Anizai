import { PlanCard } from '../components/plans/PlanCard';
import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';

interface PlanSelectionProps extends Omit<PageShellProps, 'children'> {
    onSelectPlan: (plan: 'free' | 'premium') => void;
}

const FREE_PLAN = {
    name: 'Free',
    price: '$0',
    priceSuffix: 'forever',
    description: 'Run a few forecasts, see how the workspace fits.',
    features: [
        { text: 'Up to 3 forecast sessions', included: true },
        { text: 'Probability and confidence labels', included: true },
        { text: 'Limited evidence timeline', included: true },
        { text: 'Full evidence timeline with credibility tiers', included: false },
        { text: 'Reasoning chain audit view', included: false },
        { text: 'Alerts and event tracking', included: false },
        { text: 'Historical comparisons', included: false },
    ],
};

const PREMIUM_PLAN = {
    name: 'Premium',
    price: '$19',
    priceSuffix: '/ month',
    description: 'Full access — for serious forecasting and decision-making.',
    features: [
        { text: 'Unlimited forecast sessions', included: true },
        { text: 'Full probability and confidence metrics', included: true },
        { text: 'Complete evidence timeline with credibility tiers', included: true },
        { text: 'Reasoning chain audit view', included: true },
        { text: 'Per-evidence relevance and recency scoring', included: true },
        { text: 'Alerts when key evidence shifts', included: true },
        { text: 'Comparisons to similar past forecasts', included: true },
    ],
};

const FAQ = [
    {
        q: 'What counts as a forecast session?',
        a: 'A session is one future-event question that the agent answers end-to-end — parsed, retrieved, rated, and synthesized into a structured forecast you can audit and follow up on.',
    },
    {
        q: 'Can I upgrade or cancel anytime?',
        a: 'Yes. Plans switch immediately, and cancellations apply at the end of the current billing period. No retention dance.',
    },
    {
        q: 'Is anything different about the model behind the forecast?',
        a: 'No. Both tiers use the same pipeline — the same retrieval, the same per-evidence rating, the same GPT-4o synthesis. Premium just removes the session cap and unlocks the deeper audit surfaces.',
    },
    {
        q: 'What does "private beta" mean for pricing?',
        a: 'During the beta, pricing is provisional and may change. Existing accounts will be notified in advance of any change.',
    },
];

export function PlanSelection(props: PlanSelectionProps) {
    const { onSelectPlan, ...shellProps } = props;

    return (
        <PageShell {...shellProps}>
            <PageHeader
                eyebrow="Pricing"
                title={<>Two plans. Same pipeline.</>}
                description="Start free, upgrade when you outgrow the session cap. Everything else — retrieval, evidence rating, synthesis — is identical across tiers."
            />

            <section className="w-full px-6 pb-16">
                <div className="max-w-5xl mx-auto">
                    <div className="grid grid-cols-1 md:grid-cols-2 gap-6 lg:gap-8">
                        <PlanCard
                            {...FREE_PLAN}
                            onSelect={() => onSelectPlan('free')}
                        />
                        <PlanCard
                            {...PREMIUM_PLAN}
                            onSelect={() => onSelectPlan('premium')}
                            isPremium
                        />
                    </div>
                </div>
            </section>

            <section className="w-full px-6 py-16 lg:py-24">
                <div className="max-w-3xl mx-auto">
                    <div className="mb-10 lg:mb-12">
                        <div className="mb-4 inline-flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" />
                            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                                Frequently asked
                            </span>
                        </div>
                        <h2 className="text-3xl lg:text-[2.25rem] font-medium leading-tight tracking-[-0.025em] text-gray-900">
                            What you might be wondering.
                        </h2>
                    </div>

                    <div className="rounded-2xl bg-white ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)] overflow-hidden">
                        <dl className="divide-y divide-slate-200/70">
                            {FAQ.map((item) => (
                                <div
                                    key={item.q}
                                    className="px-7 py-6 grid grid-cols-1 md:grid-cols-12 gap-4 md:gap-8"
                                >
                                    <dt className="md:col-span-5 text-[15px] font-medium tracking-tight text-gray-900">
                                        {item.q}
                                    </dt>
                                    <dd className="md:col-span-7 text-[14.5px] leading-[1.7] text-slate-600">
                                        {item.a}
                                    </dd>
                                </div>
                            ))}
                        </dl>
                    </div>

                    <p className="mt-6 text-[12.5px] text-slate-500">
                        Have a question we didn’t cover? Reach out via the Contact page.
                    </p>
                </div>
            </section>
        </PageShell>
    );
}
