import { Button } from '../components/ui/button';
import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';

interface FeaturesPageProps extends Omit<PageShellProps, 'children'> {
    onGetStarted?: () => void;
}

interface Feature {
    eyebrow: string;
    title: string;
    body: string;
    span: string;
    visual: 'verdict' | 'evidence' | 'drivers' | 'reasoning' | 'pipeline' | 'sources';
}

const FEATURES: Feature[] = [
    {
        eyebrow: 'The call',
        title: 'Decisive verdicts.',
        body:
            'Every forecast resolves to a clear call — Strong Yes, Lean No, Coin Flip — Avoid — alongside the probability, confidence label, and a one-sentence thesis. No "it depends" outputs.',
        span: 'md:col-span-7',
        visual: 'verdict',
    },
    {
        eyebrow: 'What’s moving it',
        title: 'Drivers and headwinds, separated.',
        body:
            "Key factors aren't a single weight-sorted list. Drivers are the forces pushing the probability up; headwinds are pushing it down. Each one is weighted and linked to the evidence behind it.",
        span: 'md:col-span-5',
        visual: 'drivers',
    },
    {
        eyebrow: 'The receipts',
        title: 'Every claim cited.',
        body:
            'Each piece of evidence is independently scored for relevance, credibility tier, and recency before it can influence the forecast. Hover any claim to see the original source.',
        span: 'md:col-span-5',
        visual: 'evidence',
    },
    {
        eyebrow: 'The agent’s work',
        title: 'Reasoning you can audit.',
        body:
            "A step-by-step chain of how the agent got from question to call — the parsed intent, the retrieved candidates, the per-item ratings, the final synthesis. No black box.",
        span: 'md:col-span-7',
        visual: 'reasoning',
    },
    {
        eyebrow: 'The pipeline',
        title: 'A four-step process. Every step writes a structured artifact.',
        body:
            'Ask, Retrieve, Rate, Synthesize. The graph is LangGraph; retrieval uses pgvector with text-embedding-3-small; synthesis is GPT-4o. Each stage emits an inspectable record.',
        span: 'md:col-span-12',
        visual: 'pipeline',
    },
    {
        eyebrow: 'The vault',
        title: 'Multi-source evidence index.',
        body:
            'News wires, arXiv preprints, market data, public discussion forums, and federal economic data — embedded, deduplicated, and continuously updated.',
        span: 'md:col-span-12',
        visual: 'sources',
    },
];

const SOURCES = [
    'News wires',
    'arXiv',
    'Hacker News',
    'Telegram',
    'FRED economic data',
    'Polymarket',
];

const PIPELINE_STEPS = [
    { n: '01', title: 'Ask', tag: 'LangGraph' },
    { n: '02', title: 'Retrieve', tag: 'pgvector' },
    { n: '03', title: 'Rate', tag: 'Per-item scoring' },
    { n: '04', title: 'Synthesize', tag: 'GPT-4o' },
];

export function FeaturesPage(props: FeaturesPageProps) {
    const { onGetStarted, ...shellProps } = props;

    return (
        <PageShell {...shellProps}>
            <PageHeader
                eyebrow="Features"
                title={
                    <>
                        Everything the agent produces, in one place.
                    </>
                }
                description="Anizai isn't a black box that returns a number. It's a pipeline that produces a stack of inspectable artifacts: a verdict, the forces driving it, the evidence behind each claim, and the reasoning that connects them."
            />

            <section className="w-full px-6 pb-20 lg:pb-28">
                <div className="max-w-5xl mx-auto">
                    <div className="grid grid-cols-1 md:grid-cols-12 gap-4">
                        {FEATURES.map((feature) => (
                            <FeatureCard key={feature.title} feature={feature} />
                        ))}
                    </div>

                    <div className="mt-12 rounded-2xl bg-white p-8 lg:p-10 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)]">
                        <h3 className="text-xl font-medium tracking-tight text-gray-900">
                            See it on a real question.
                        </h3>
                        <p className="mt-2 max-w-xl text-[15px] leading-[1.65] text-slate-600">
                            The fastest way to understand what Anizai produces is to run
                            a forecast on something you actually care about.
                        </p>
                        <div className="mt-6">
                            <Button
                                onClick={onGetStarted}
                                disabled={!onGetStarted}
                                className="h-11 px-6 text-[14.5px] font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-lg shadow-[0_1px_2px_rgba(15,23,42,0.08)]"
                            >
                                Get started
                            </Button>
                        </div>
                    </div>
                </div>
            </section>
        </PageShell>
    );
}

function FeatureCard({ feature }: { feature: Feature }) {
    return (
        <article
            className={`rounded-2xl bg-white p-7 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05),0_1px_3px_rgba(15,23,42,0.04)] flex flex-col ${feature.span}`}
        >
            <p className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-anizai-purple-600 mb-3">
                {feature.eyebrow}
            </p>
            <h3 className="text-xl lg:text-[1.375rem] font-medium tracking-tight text-gray-900">
                {feature.title}
            </h3>
            <p className="mt-3 text-[14.5px] leading-[1.7] text-slate-600 max-w-xl">
                {feature.body}
            </p>
            <div className="mt-6 flex-1">
                <FeatureVisual variant={feature.visual} />
            </div>
        </article>
    );
}

function FeatureVisual({ variant }: { variant: Feature['visual'] }) {
    if (variant === 'verdict') {
        return (
            <div className="rounded-xl bg-slate-50/80 ring-1 ring-slate-900/[0.04] p-5 flex items-center gap-5">
                <div className="relative w-[78px] h-[78px] shrink-0">
                    <svg viewBox="0 0 78 78" className="w-full h-full -rotate-90">
                        <defs>
                            <linearGradient id="featRing" x1="0%" y1="0%" x2="100%" y2="100%">
                                <stop offset="0%" stopColor="#c084fc" />
                                <stop offset="100%" stopColor="#2dd4bf" />
                            </linearGradient>
                        </defs>
                        <circle cx="39" cy="39" r="32" fill="none" stroke="#eef0f4" strokeWidth="7" />
                        <circle
                            cx="39"
                            cy="39"
                            r="32"
                            fill="none"
                            stroke="url(#featRing)"
                            strokeWidth="7"
                            strokeLinecap="round"
                            strokeDasharray={2 * Math.PI * 32}
                            strokeDashoffset={2 * Math.PI * 32 * (1 - 0.72)}
                        />
                    </svg>
                    <div className="absolute inset-0 flex flex-col items-center justify-center">
                        <span className="text-lg font-light text-gray-900 tabular-nums leading-none">
                            72<span className="text-sm text-slate-400">%</span>
                        </span>
                    </div>
                </div>
                <div className="min-w-0 flex-1">
                    <div className="inline-flex items-center gap-1.5 rounded-full bg-anizai-teal-50 px-2.5 py-0.5">
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" />
                        <span className="text-[11px] font-medium text-anizai-teal-800">
                            Lean Yes
                        </span>
                    </div>
                    <p className="mt-2 text-[13px] leading-snug text-slate-700">
                        Recession by end of 2026 — High Confidence.
                    </p>
                </div>
            </div>
        );
    }
    if (variant === 'drivers') {
        const rows = [
            { label: 'Yield curve inversion', dir: 'up' as const, w: 0.85 },
            { label: 'Labor market softening', dir: 'up' as const, w: 0.55 },
            { label: 'Consumer spending resilient', dir: 'down' as const, w: 0.65 },
        ];
        return (
            <div className="space-y-2.5">
                {rows.map((r) => (
                    <div key={r.label}>
                        <div className="flex items-center justify-between text-[12px] mb-1">
                            <span className="text-slate-700 truncate">{r.label}</span>
                            <span
                                className={`text-[10.5px] font-medium uppercase tracking-[0.08em] ${
                                    r.dir === 'up' ? 'text-anizai-teal-700' : 'text-rose-700'
                                }`}
                            >
                                {r.dir === 'up' ? '↑ Driver' : '↓ Headwind'}
                            </span>
                        </div>
                        <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
                            <div
                                className={`h-full rounded-full ${
                                    r.dir === 'up' ? 'bg-anizai-teal-400' : 'bg-rose-400'
                                }`}
                                style={{ width: `${r.w * 100}%` }}
                            />
                        </div>
                    </div>
                ))}
            </div>
        );
    }
    if (variant === 'evidence') {
        const items = [
            { src: 'reuters.com', age: '11h', tier: 'Tier 1', body: 'Fed signals slower cut path' },
            { src: 'arxiv.org', age: '2d', tier: 'Tier 1', body: 'Yield-curve recession indicator paper' },
        ];
        return (
            <ul className="space-y-2">
                {items.map((item) => (
                    <li
                        key={item.body}
                        className="rounded-lg bg-slate-50/80 ring-1 ring-slate-900/[0.04] px-3.5 py-2.5 flex items-center gap-3"
                    >
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400 shrink-0" />
                        <div className="min-w-0 flex-1">
                            <p className="text-[12.5px] text-slate-800 truncate">{item.body}</p>
                            <p className="text-[10.5px] text-slate-500 mt-0.5">
                                <span className="font-medium uppercase tracking-wide">{item.src}</span>
                                <span className="mx-1.5 text-slate-300">·</span>
                                {item.age} ago
                                <span className="mx-1.5 text-slate-300">·</span>
                                {item.tier}
                            </p>
                        </div>
                    </li>
                ))}
            </ul>
        );
    }
    if (variant === 'reasoning') {
        const steps = [
            'Parse question intent and time window',
            'Retrieve candidate evidence from vault',
            'Score each item: relevance, tier, recency',
            'Weight drivers vs. headwinds by impact',
            'Synthesize verdict, probability, confidence',
        ];
        return (
            <ol className="space-y-1.5">
                {steps.map((step, i) => (
                    <li key={step} className="flex items-start gap-3 text-[12.5px]">
                        <span className="mt-0.5 inline-flex items-center justify-center h-5 w-5 rounded-full bg-slate-100 text-[10px] font-medium tabular-nums text-slate-500 shrink-0">
                            {i + 1}
                        </span>
                        <span className="text-slate-700 leading-snug">{step}</span>
                    </li>
                ))}
            </ol>
        );
    }
    if (variant === 'pipeline') {
        return (
            <div className="rounded-xl bg-slate-50/80 ring-1 ring-slate-900/[0.04] overflow-hidden">
                <ol className="grid grid-cols-2 md:grid-cols-4 divide-x divide-y md:divide-y-0 divide-slate-200/70">
                    {PIPELINE_STEPS.map((step) => (
                        <li key={step.n} className="p-5">
                            <p className="text-[10.5px] font-medium tabular-nums text-slate-400 mb-2">
                                {step.n}
                            </p>
                            <p className="text-[15px] font-medium text-gray-900 tracking-tight">
                                {step.title}
                            </p>
                            <p className="mt-2 text-[10.5px] font-medium uppercase tracking-[0.12em] text-anizai-purple-600">
                                {step.tag}
                            </p>
                        </li>
                    ))}
                </ol>
            </div>
        );
    }
    if (variant === 'sources') {
        return (
            <div className="flex flex-wrap gap-2.5">
                {SOURCES.map((src) => (
                    <span
                        key={src}
                        className="inline-flex items-center gap-2 rounded-full bg-slate-50/80 ring-1 ring-slate-900/[0.04] px-3 py-1.5 text-[12px] text-slate-700"
                    >
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-400" />
                        {src}
                    </span>
                ))}
            </div>
        );
    }
    return null;
}
