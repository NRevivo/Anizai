interface Step {
    n: string;
    title: string;
    body: string;
    tag: string;
}

const STEPS: Step[] = [
    {
        n: '01',
        title: 'Ask',
        body: 'Submit a future-event question. The agent parses intent, time bounds, and resolution criteria.',
        tag: 'LangGraph',
    },
    {
        n: '02',
        title: 'Retrieve',
        body: 'Vault retrieval across indexed news, arXiv, market data and live sources, ranked by embedding similarity.',
        tag: 'pgvector · text-embedding-3-small',
    },
    {
        n: '03',
        title: 'Rate',
        body: 'Every piece of evidence scored independently for relevance, credibility tier, and recency before it can be used.',
        tag: 'Per-evidence scoring',
    },
    {
        n: '04',
        title: 'Synthesize',
        body: 'GPT-4o produces a structured forecast: verdict, probability, confidence, drivers, headwinds, reasoning chain.',
        tag: 'GPT-4o',
    },
];

export function HowItWorks() {
    return (
        <section className="w-full px-6 py-24 lg:py-32 border-b border-slate-200/60 relative">
            <div className="max-w-6xl mx-auto">
                <div className="max-w-2xl mb-14 lg:mb-20">
                    <div className="mb-4 inline-flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" aria-hidden />
                        <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                            How it works
                        </span>
                    </div>
                    <h2 className="text-3xl sm:text-4xl lg:text-[2.5rem] font-medium leading-tight tracking-[-0.025em] text-gray-900">
                        A four-step pipeline. Every step inspectable.
                    </h2>
                </div>

                <ol className="relative grid grid-cols-1 md:grid-cols-2 lg:grid-cols-4 gap-px bg-slate-200/70 rounded-2xl overflow-hidden ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)]">
                    {STEPS.map((step, i) => (
                        <li key={step.n} className="relative bg-white p-7 lg:p-8 flex flex-col">
                            <div className="flex items-center gap-3 mb-5">
                                <span className="text-[11px] font-medium tabular-nums text-slate-400">
                                    {step.n}
                                </span>
                                <span className="h-px flex-1 bg-slate-200" />
                                {i < STEPS.length - 1 && (
                                    <svg
                                        className="hidden lg:block w-3.5 h-3.5 text-slate-300 -mr-1"
                                        viewBox="0 0 24 24"
                                        fill="none"
                                        stroke="currentColor"
                                        strokeWidth={2}
                                        aria-hidden
                                    >
                                        <path strokeLinecap="round" strokeLinejoin="round" d="M9 5l7 7-7 7" />
                                    </svg>
                                )}
                            </div>
                            <h3 className="text-xl font-medium text-gray-900 tracking-tight">
                                {step.title}
                            </h3>
                            <p className="mt-3 text-[14px] leading-[1.65] text-slate-600 flex-1">
                                {step.body}
                            </p>
                            <p className="mt-6 text-[11px] font-medium uppercase tracking-[0.12em] text-anizai-purple-600">
                                {step.tag}
                            </p>
                        </li>
                    ))}
                </ol>

                <p className="mt-8 text-[13px] text-slate-500 max-w-3xl">
                    No black box. Each step writes a structured artifact you can audit —
                    the parsed query, the retrieved evidence, the per-item ratings, the
                    final synthesis.
                </p>
            </div>
        </section>
    );
}
