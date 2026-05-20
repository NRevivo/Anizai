import { Button } from '../components/ui/button';
import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';

interface AboutPageProps extends Omit<PageShellProps, 'children'> {
    onGetStarted?: () => void;
    onMethodology?: () => void;
}

const POSITIONS = [
    {
        eyebrow: 'A decision instrument',
        title: 'Not a chatbot.',
        body:
            "Anizai answers a single, well-formed question once — with structure. Every forecast is a one-shot analytical artifact, not a back-and-forth conversation.",
    },
    {
        eyebrow: 'Built for bettors',
        title: 'A tool for deciding whether to bet.',
        body:
            "The product is shaped around a Polymarket-style decision: should I bet yes, bet no, or not at all. The whole UI is organized to surface the verdict first.",
    },
    {
        eyebrow: 'No invented confidence',
        title: 'The model declares its own uncertainty.',
        body:
            "Probability and confidence are reported as independent metrics. The agent tells you when it does not have enough evidence — instead of inventing a number.",
    },
    {
        eyebrow: 'Audit, always',
        title: 'Every step is inspectable.',
        body:
            "The parsed query, the retrieved evidence, the per-item ratings, the synthesis prompt and output — all of it persists. You can audit any decision the agent made.",
    },
];

export function AboutPage(props: AboutPageProps) {
    const { onGetStarted, onMethodology, ...shellProps } = props;

    return (
        <PageShell {...shellProps}>
            <PageHeader
                eyebrow="About"
                title={
                    <>
                        We are building a decision instrument,{' '}
                        <span className="text-slate-400 font-light">not a chatbot.</span>
                    </>
                }
                description="Anizai is a RAG-based event forecasting platform. A user asks a future-event question — Anizai returns a structured forecast: probability, confidence, the drivers and headwinds, and the evidence behind every claim."
            />

            <section className="w-full px-6 py-16 lg:py-20">
                <div className="max-w-5xl mx-auto">
                    <div className="mb-10 lg:mb-14">
                        <div className="mb-4 inline-flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" />
                            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                                Where we stand
                            </span>
                        </div>
                        <h2 className="text-3xl lg:text-[2.25rem] font-medium leading-tight tracking-[-0.025em] text-gray-900 max-w-3xl">
                            Four positions that shape the product.
                        </h2>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {POSITIONS.map((position) => (
                            <article
                                key={position.title}
                                className="rounded-2xl bg-white p-7 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)]"
                            >
                                <p className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-anizai-purple-600">
                                    {position.eyebrow}
                                </p>
                                <h3 className="mt-2 text-xl font-medium tracking-tight text-gray-900">
                                    {position.title}
                                </h3>
                                <p className="mt-3 text-[14.5px] leading-[1.7] text-slate-600">
                                    {position.body}
                                </p>
                            </article>
                        ))}
                    </div>
                </div>
            </section>

            <section className="w-full px-6 py-16 lg:py-20">
                <div className="max-w-3xl mx-auto">
                    <div className="mb-8">
                        <div className="mb-4 inline-flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" />
                            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                                The shape of the product
                            </span>
                        </div>
                        <h2 className="text-3xl lg:text-[2.25rem] font-medium leading-tight tracking-[-0.025em] text-gray-900">
                            Three layers.
                        </h2>
                    </div>

                    <div className="space-y-8 text-[15.5px] leading-[1.8] text-slate-700">
                        <p>
                            A <strong className="font-medium text-gray-900">Medallion data pipeline</strong>{' '}
                            (Bronze → Silver → Gold) ingests sources continuously — news,
                            arXiv preprints, federal economic data, public discussion.
                            Each layer cleans and enriches what came before, until raw
                            inputs become embedded, deduplicated evidence in a vector
                            store.
                        </p>
                        <p>
                            A <strong className="font-medium text-gray-900">LangGraph agent</strong>{' '}
                            sits on top. When a user asks a forecast question, the agent
                            parses intent, queries the vault by embedding similarity,
                            rates every retrieved item independently, and synthesizes
                            the final forecast with GPT-4o. Each step writes a
                            structured artifact.
                        </p>
                        <p>
                            A <strong className="font-medium text-gray-900">decision-first UI</strong>{' '}
                            renders the result. The verdict is the headline. Probability,
                            confidence, and consensus appear together as a family.
                            Drivers and headwinds are separated. Evidence is grouped by
                            source and tier — every claim links back to where it came
                            from.
                        </p>
                    </div>

                    <div className="mt-12 rounded-2xl bg-white p-7 lg:p-9 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)]">
                        <h3 className="text-xl font-medium tracking-tight text-gray-900">
                            Currently in private beta.
                        </h3>
                        <p className="mt-2 max-w-xl text-[14.5px] leading-[1.65] text-slate-600">
                            Anizai is shipping under a private beta. Run a forecast or
                            read the methodology in detail.
                        </p>
                        <div className="mt-6 flex flex-wrap gap-3">
                            <Button
                                onClick={onGetStarted}
                                disabled={!onGetStarted}
                                className="h-11 px-6 text-[14.5px] font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-lg shadow-[0_1px_2px_rgba(15,23,42,0.08)]"
                            >
                                Get started
                            </Button>
                            <button
                                onClick={onMethodology}
                                disabled={!onMethodology}
                                className="h-11 px-5 text-[14.5px] font-medium text-slate-700 hover:text-gray-900 transition-colors inline-flex items-center gap-2 disabled:opacity-50"
                            >
                                Read the methodology
                                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth={1.75}>
                                    <path strokeLinecap="round" strokeLinejoin="round" d="M5 12h14M13 5l7 7-7 7" />
                                </svg>
                            </button>
                        </div>
                    </div>
                </div>
            </section>
        </PageShell>
    );
}
