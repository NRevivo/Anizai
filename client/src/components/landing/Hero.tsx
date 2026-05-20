import { Button } from '../ui/button';

interface HeroProps {
    onPrimary: () => void;
    onSecondary?: () => void;
}

const CAPABILITIES = [
    'GPT-4o synthesis',
    'Multi-source vault',
    'Every claim cited',
];

export function Hero({ onPrimary, onSecondary }: HeroProps) {
    return (
        <section className="relative w-full px-6 py-16 lg:py-20 min-h-[calc(100vh-4rem)] flex items-center">
            <div className="w-full max-w-6xl mx-auto">
                <div className="grid grid-cols-1 lg:grid-cols-12 gap-12 lg:gap-10 items-center">
                    <div className="lg:col-span-7">
                        <div className="mb-6 inline-flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" aria-hidden />
                            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                                For prediction markets and decision-makers
                            </span>
                        </div>

                        <h1
                            className="text-5xl sm:text-6xl lg:text-[4.5rem] font-medium leading-[1.02] tracking-[-0.035em] text-gray-900"
                            style={{ textWrap: 'balance' } as React.CSSProperties}
                        >
                            Decision-grade forecasts.
                            <br />
                            <span className="text-slate-400 font-light">With receipts.</span>
                        </h1>

                        <p className="mt-7 max-w-xl text-[17px] leading-[1.6] text-slate-600">
                            Anizai turns future-event questions into structured
                            forecasts — probability, confidence, drivers and headwinds,
                            with every piece of evidence cited.
                        </p>

                        <div className="mt-9 flex flex-wrap items-center gap-3">
                            <Button
                                onClick={onPrimary}
                                className="h-12 px-7 text-[15px] font-medium bg-gray-900 text-white hover:bg-gray-800 shadow-[0_1px_2px_rgba(15,23,42,0.08)] rounded-lg"
                            >
                                Get started
                            </Button>
                            <button
                                onClick={onSecondary}
                                className="h-12 px-5 text-[15px] font-medium text-slate-700 hover:text-gray-900 transition-colors inline-flex items-center gap-2"
                            >
                                See an example forecast
                                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M19 9l-7 7-7-7" />
                                </svg>
                            </button>
                        </div>

                        <ul className="mt-9 flex flex-wrap items-center gap-x-6 gap-y-3 text-[12.5px] text-slate-500">
                            {CAPABILITIES.map((capability) => (
                                <li key={capability} className="inline-flex items-center gap-2">
                                    <CheckMark />
                                    <span>{capability}</span>
                                </li>
                            ))}
                        </ul>
                    </div>

                    <div className="lg:col-span-5">
                        <HeroPreview />
                    </div>
                </div>
            </div>
        </section>
    );
}

function CheckMark() {
    return (
        <svg className="w-3.5 h-3.5 text-anizai-teal-500" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={2.5} aria-hidden>
            <path strokeLinecap="round" strokeLinejoin="round" d="M5 13l4 4L19 7" />
        </svg>
    );
}

function HeroPreview() {
    return (
        <div className="relative">
            {/* soft gradient halo */}
            <div
                className="absolute -inset-8 -z-10 opacity-60 blur-3xl pointer-events-none"
                style={{
                    background:
                        'radial-gradient(ellipse 60% 60% at 30% 30%, rgba(168,85,247,0.18), transparent 60%), radial-gradient(ellipse 60% 60% at 70% 70%, rgba(20,184,166,0.18), transparent 60%)',
                }}
                aria-hidden
            />

            <div className="rounded-2xl bg-white ring-1 ring-slate-900/[0.06] shadow-[0_20px_50px_-12px_rgba(15,23,42,0.18),0_4px_12px_-4px_rgba(15,23,42,0.06)] overflow-hidden">
                <div className="px-5 py-3 border-b border-slate-100 flex items-center gap-2.5">
                    <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" />
                    <span className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-slate-500">
                        Active forecast
                    </span>
                    <span className="ml-auto text-[10.5px] tabular-nums text-slate-400">
                        #f3a-2026
                    </span>
                </div>

                <div className="p-5 sm:p-6">
                    <p className="text-[13px] font-medium leading-snug text-gray-900">
                        Will the US enter a recession by end of 2026?
                    </p>

                    <div className="mt-5 flex items-center gap-5">
                        <div className="relative w-[88px] h-[88px] shrink-0">
                            <svg viewBox="0 0 88 88" className="w-full h-full -rotate-90">
                                <defs>
                                    <linearGradient id="heroRingGrad" x1="0%" y1="0%" x2="100%" y2="100%">
                                        <stop offset="0%" stopColor="#c084fc" />
                                        <stop offset="100%" stopColor="#2dd4bf" />
                                    </linearGradient>
                                </defs>
                                <circle cx="44" cy="44" r="36" fill="none" stroke="#eef0f4" strokeWidth="8" />
                                <circle
                                    cx="44"
                                    cy="44"
                                    r="36"
                                    fill="none"
                                    stroke="url(#heroRingGrad)"
                                    strokeWidth="8"
                                    strokeLinecap="round"
                                    strokeDasharray={2 * Math.PI * 36}
                                    strokeDashoffset={2 * Math.PI * 36 * (1 - 0.62)}
                                />
                            </svg>
                            <div className="absolute inset-0 flex flex-col items-center justify-center">
                                <span className="text-2xl font-light text-gray-900 tabular-nums leading-none">
                                    62<span className="text-base text-slate-400">%</span>
                                </span>
                                <span className="mt-1 text-[8px] font-medium uppercase tracking-[0.14em] text-slate-400">
                                    Probability
                                </span>
                            </div>
                        </div>
                        <div className="min-w-0 flex-1">
                            <div className="inline-flex items-center gap-1.5 rounded-full bg-anizai-teal-50 px-2.5 py-0.5">
                                <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" />
                                <span className="text-[10.5px] font-medium text-anizai-teal-800">
                                    Lean Yes · High confidence
                                </span>
                            </div>
                            <p className="mt-2.5 text-[12px] leading-snug text-slate-700">
                                Yield-curve inversion and weakening labor data outweigh
                                resilient consumer spending.
                            </p>
                        </div>
                    </div>

                    <div className="mt-5 pt-4 border-t border-slate-100">
                        <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-slate-400 mb-2.5">
                            Top drivers
                        </p>
                        <ul className="space-y-1.5">
                            {[
                                { label: 'Yield curve inversion', dir: 'up' as const, weight: 0.85 },
                                { label: 'Labor market softening', dir: 'up' as const, weight: 0.55 },
                                { label: 'Consumer spending resilient', dir: 'down' as const, weight: 0.65 },
                            ].map((driver) => (
                                <li key={driver.label} className="flex items-center gap-3 text-[11.5px]">
                                    <span className="text-slate-700 w-44 truncate">
                                        {driver.label}
                                    </span>
                                    <div className="flex-1 h-1 rounded-full bg-slate-100 overflow-hidden">
                                        <div
                                            className={`h-full rounded-full ${
                                                driver.dir === 'up'
                                                    ? 'bg-anizai-teal-400'
                                                    : 'bg-rose-400'
                                            }`}
                                            style={{ width: `${driver.weight * 100}%` }}
                                        />
                                    </div>
                                </li>
                            ))}
                        </ul>
                    </div>
                </div>
            </div>

            {/* small floating evidence pill */}
            <div className="hidden lg:flex absolute -bottom-4 -right-3 items-center gap-2 rounded-full bg-white ring-1 ring-slate-900/[0.06] shadow-[0_6px_20px_-6px_rgba(15,23,42,0.15)] px-3.5 py-2">
                <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-500" />
                <span className="text-[11px] text-slate-700">
                    47 evidence items cited
                </span>
            </div>
        </div>
    );
}
