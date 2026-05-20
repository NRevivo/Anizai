export function WhatYouGet() {
    return (
        <section className="w-full px-6 py-24 lg:py-32">
            <div className="max-w-6xl mx-auto">
                <div className="max-w-2xl mb-14 lg:mb-16">
                    <div className="mb-4 inline-flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" aria-hidden />
                        <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                            What you get
                        </span>
                    </div>
                    <h2 className="text-3xl sm:text-4xl lg:text-[2.5rem] font-medium leading-tight tracking-[-0.025em] text-gray-900">
                        Four artifacts in your workspace — for every forecast.
                    </h2>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-6 gap-4">
                    <BentoCard className="md:col-span-4">
                        <CardHeader index="01" eyebrow="The call" title="A verdict." />
                        <p className="text-[14px] leading-[1.7] text-slate-600 max-w-md mb-6">
                            Strong Yes, Lean No, Coin Flip — Avoid. Every forecast lands
                            on a clear call, so you know what to do with it.
                        </p>
                        <VerdictPreview />
                    </BentoCard>

                    <BentoCard className="md:col-span-2">
                        <CardHeader index="02" eyebrow="What's moving it" title="Drivers & headwinds." />
                        <p className="text-[14px] leading-[1.7] text-slate-600 mb-6">
                            What's pushing the probability up vs. down, weighted by
                            impact.
                        </p>
                        <DriversPreview />
                    </BentoCard>

                    <BentoCard className="md:col-span-3">
                        <CardHeader index="03" eyebrow="The receipts" title="Cited evidence." />
                        <p className="text-[14px] leading-[1.7] text-slate-600 mb-6">
                            Every claim links back to the article, paper, market, or
                            dataset it came from — with credibility tier, recency, and
                            relevance scored individually.
                        </p>
                        <EvidencePreview />
                    </BentoCard>

                    <BentoCard className="md:col-span-3">
                        <CardHeader index="04" eyebrow="The agent's work" title="Reasoning chain." />
                        <p className="text-[14px] leading-[1.7] text-slate-600 mb-6">
                            The agent's logic step by step, so you can audit how it got
                            from question to call.
                        </p>
                        <ReasoningPreview />
                    </BentoCard>
                </div>
            </div>
        </section>
    );
}

function BentoCard({ children, className = '' }: { children: React.ReactNode; className?: string }) {
    return (
        <div
            className={`rounded-2xl bg-white p-7 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05),0_1px_3px_rgba(15,23,42,0.04)] flex flex-col ${className}`}
        >
            {children}
        </div>
    );
}

function CardHeader({ index, eyebrow, title }: { index: string; eyebrow: string; title: string }) {
    return (
        <div className="mb-4">
            <div className="flex items-center gap-2.5 mb-3">
                <span className="text-[10.5px] font-medium tabular-nums text-slate-400">{index}</span>
                <span className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-anizai-purple-600">
                    {eyebrow}
                </span>
            </div>
            <h3 className="text-xl font-medium text-gray-900 tracking-tight">{title}</h3>
        </div>
    );
}

function VerdictPreview() {
    return (
        <div className="mt-auto rounded-xl bg-slate-50/80 ring-1 ring-slate-900/[0.04] p-4 sm:p-5 flex items-center gap-5">
            <div className="relative w-[68px] h-[68px] shrink-0">
                <svg viewBox="0 0 68 68" className="w-full h-full -rotate-90">
                    <defs>
                        <linearGradient id="ringGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                            <stop offset="0%" stopColor="#c084fc" />
                            <stop offset="100%" stopColor="#2dd4bf" />
                        </linearGradient>
                    </defs>
                    <circle cx="34" cy="34" r="28" fill="none" stroke="#eef0f4" strokeWidth="6" />
                    <circle
                        cx="34"
                        cy="34"
                        r="28"
                        fill="none"
                        stroke="url(#ringGrad)"
                        strokeWidth="6"
                        strokeLinecap="round"
                        strokeDasharray={2 * Math.PI * 28}
                        strokeDashoffset={2 * Math.PI * 28 * (1 - 0.72)}
                    />
                </svg>
                <div className="absolute inset-0 flex items-center justify-center">
                    <span className="text-base font-light text-gray-900 tabular-nums">72%</span>
                </div>
            </div>
            <div className="min-w-0 flex-1">
                <div className="inline-flex items-center gap-1.5 rounded-full bg-anizai-teal-50 px-2.5 py-0.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" />
                    <span className="text-[11px] font-medium text-anizai-teal-800">Lean Yes</span>
                </div>
                <p className="mt-2 text-[13px] leading-snug text-slate-700">
                    Recession by end of 2026 — High Confidence.
                </p>
                <p className="mt-1 text-[11px] text-slate-400">Resolves Dec 31, 2026</p>
            </div>
        </div>
    );
}

function DriversPreview() {
    const items = [
        { label: 'Yield curve inversion', dir: 'up' as const, weight: 0.85 },
        { label: 'Labor market softening', dir: 'up' as const, weight: 0.55 },
        { label: 'Consumer spending resilient', dir: 'down' as const, weight: 0.65 },
    ];
    return (
        <div className="mt-auto space-y-2.5">
            {items.map((item) => (
                <div key={item.label}>
                    <div className="flex items-center justify-between text-[12px] mb-1">
                        <span className="text-slate-700 truncate">{item.label}</span>
                        <span
                            className={`text-[10.5px] font-medium uppercase tracking-[0.08em] ${
                                item.dir === 'up' ? 'text-anizai-teal-700' : 'text-rose-700'
                            }`}
                        >
                            {item.dir === 'up' ? '↑ Driver' : '↓ Headwind'}
                        </span>
                    </div>
                    <div className="h-1.5 rounded-full bg-slate-100 overflow-hidden">
                        <div
                            className={`h-full rounded-full ${
                                item.dir === 'up' ? 'bg-anizai-teal-400' : 'bg-rose-400'
                            }`}
                            style={{ width: `${item.weight * 100}%` }}
                        />
                    </div>
                </div>
            ))}
        </div>
    );
}

function EvidencePreview() {
    const items = [
        { src: 'reuters.com', age: '11h ago', tier: 'Tier 1', body: 'Fed signals slower cut path' },
        { src: 'arxiv.org', age: '2d ago', tier: 'Tier 1', body: 'Yield-curve recession indicator paper' },
        { src: 'fred.stlouisfed.org', age: '4h ago', tier: 'Tier 1', body: 'Unemployment claims tick up' },
    ];
    return (
        <ul className="mt-auto space-y-2">
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
                            {item.age}
                            <span className="mx-1.5 text-slate-300">·</span>
                            {item.tier}
                        </p>
                    </div>
                </li>
            ))}
        </ul>
    );
}

function ReasoningPreview() {
    const steps = [
        'Parse question intent and time window',
        'Retrieve 47 candidate evidence items from vault',
        'Score each item: relevance, tier, recency',
        'Weight drivers vs. headwinds by impact',
        'Synthesize verdict, probability, confidence',
    ];
    return (
        <ol className="mt-auto space-y-2">
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
