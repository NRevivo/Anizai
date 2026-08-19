import { useState } from 'react';

interface DashboardPreviewProps {
    id?: string;
}

const IMAGE_SRC = '/landing/dashboard-preview.png';

export function DashboardPreview({ id }: DashboardPreviewProps) {
    const [imageFailed, setImageFailed] = useState(false);

    return (
        <section id={id} className="w-full px-6 py-24 lg:py-32 scroll-mt-12 bg-white border-y border-slate-200/60 relative">
            <div className="max-w-6xl mx-auto">
                <div className="max-w-3xl mb-12 lg:mb-16">
                    <div className="mb-4 inline-flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" aria-hidden />
                        <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                            A real forecast
                        </span>
                    </div>
                    <h2 className="text-3xl sm:text-4xl lg:text-[2.75rem] font-light leading-tight tracking-[-0.02em] text-gray-900">
                        A live geopolitical forecast our system generated, with every driver,
                        headwind, and source visible.
                    </h2>
                </div>

                <figure className="relative">
                    <div className="overflow-hidden rounded-2xl bg-white ring-1 ring-slate-900/[0.06] shadow-[0_24px_60px_-12px_rgba(15,23,42,0.18),0_8px_20px_-8px_rgba(15,23,42,0.08)]">
                        {imageFailed ? (
                            <PlaceholderFrame />
                        ) : (
                            <img
                                src={IMAGE_SRC}
                                alt="Anizai dashboard showing a Strait of Hormuz traffic forecast, with verdict, probability ring, drivers, headwinds, and follow-up analysis."
                                className="block w-full h-auto"
                                loading="lazy"
                                decoding="async"
                                onError={() => setImageFailed(true)}
                            />
                        )}
                    </div>

                    <figcaption className="mt-10 grid grid-cols-1 md:grid-cols-3 gap-x-10 gap-y-6">
                        <div>
                            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-anizai-purple-600">
                                Drivers &amp; headwinds
                            </p>
                            <p className="mt-2 text-[15px] leading-[1.65] text-slate-600">
                                See what's pushing the probability up and what's pushing it
                                down, weighted by impact.
                            </p>
                        </div>
                        <div>
                            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-anizai-purple-600">
                                Reasoning chain
                            </p>
                            <p className="mt-2 text-[15px] leading-[1.65] text-slate-600">
                                The agent's logic, laid out step by step. No black box.
                            </p>
                        </div>
                        <div>
                            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-anizai-purple-600">
                                Every source cited
                            </p>
                            <p className="mt-2 text-[15px] leading-[1.65] text-slate-600">
                                Every claim links back to the article, paper, or data
                                source it came from.
                            </p>
                        </div>
                    </figcaption>
                </figure>
            </div>
        </section>
    );
}

function PlaceholderFrame() {
    return (
        <div
            className="w-full aspect-[16/10] flex flex-col items-center justify-center bg-slate-50"
            style={{
                backgroundImage:
                    'radial-gradient(ellipse 60% 50% at 0% 0%, rgba(168,85,247,0.08), transparent 70%), radial-gradient(ellipse 60% 50% at 100% 100%, rgba(20,184,166,0.08), transparent 70%)',
            }}
        >
            <p className="text-[11px] font-medium uppercase tracking-[0.14em] text-slate-400">
                Dashboard preview
            </p>
            <p className="mt-3 max-w-md text-center text-[13px] text-slate-500">
                Drop a screenshot of the seeded recession forecast into{' '}
                <code className="px-1.5 py-0.5 rounded bg-slate-100 text-slate-700">
                    client/public/landing/dashboard-preview.png
                </code>
                .
            </p>
        </div>
    );
}
