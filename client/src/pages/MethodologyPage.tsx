import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';

interface MethodologyPageProps extends Omit<PageShellProps, 'children'> {}

interface Stage {
    n: string;
    title: string;
    body: string;
    artifact: string;
    tech: string;
}

const STAGES: Stage[] = [
    {
        n: '01',
        title: 'Understand',
        body:
            'The agent parses the question — extracting intent, resolution criteria, and the time window. Ambiguous questions surface clarification candidates instead of being silently answered.',
        artifact: 'Parsed query record',
        tech: 'LangGraph node',
    },
    {
        n: '02',
        title: 'Embed',
        body:
            'The question is embedded into a 1536-dimensional vector. The vault — news, arXiv, market data, public discussion — is searched by cosine similarity for candidate evidence.',
        artifact: 'Candidate evidence set',
        tech: 'text-embedding-3-small · pgvector',
    },
    {
        n: '03',
        title: 'Rate',
        body:
            'Every candidate is scored independently — three objective dimensions (relevance, credibility tier, recency) and then three forecast-specific ones (whether it was used, its directional impact, its magnitude). Each score is written before synthesis.',
        artifact: 'Per-evidence rating records',
        tech: 'GPT-4o evaluator',
    },
    {
        n: '04',
        title: 'Synthesize',
        body:
            'The rated evidence flows into the synthesis prompt. The model produces a structured forecast — probability, confidence, consensus, verdict, drivers, headwinds, reasoning chain, and the gaps it acknowledges.',
        artifact: 'SessionResult document',
        tech: 'GPT-4o',
    },
    {
        n: '05',
        title: 'Persist',
        body:
            'The forecast and every artifact above it land in your workspace. You can audit any step, follow up with a clarifying question, or run a different forecast against the same evidence.',
        artifact: 'Firestore session + evidence subcollection',
        tech: 'Firestore',
    },
];

interface Principle {
    eyebrow: string;
    title: string;
    body: string;
}

const PRINCIPLES: Principle[] = [
    {
        eyebrow: 'Transparency over magic',
        title: 'Every step writes an inspectable artifact.',
        body: 'There is no hidden state. The parsed query, the retrieved evidence, the per-item ratings, the synthesis — all of it persists, all of it is yours to audit.',
    },
    {
        eyebrow: 'Calibrated uncertainty',
        title: 'Probability and confidence are independent.',
        body: 'A 50% probability with high confidence ("we are sure this is a coin flip") is a different answer than a 50% probability with low confidence ("we do not have enough information"). The system reports both.',
    },
    {
        eyebrow: 'Epistemic honesty',
        title: 'The agent declares what it did not find.',
        body: 'Every forecast surfaces the gaps the model is aware of. Missing evidence is a signal — not something to paper over.',
    },
    {
        eyebrow: 'Cite or do not claim',
        title: 'No claim without a source.',
        body: 'Every assertion the synthesis makes is grounded in a specific evidence item with a credibility tier and a publication timestamp.',
    },
];

const EVIDENCE_DIMENSIONS = [
    { key: 'Relevance', range: '0–1', kind: 'Objective', note: 'How directly the item speaks to the question.' },
    { key: 'Credibility tier', range: 'Tier 1–3', kind: 'Objective', note: 'Source authority — peer-reviewed > established outlet > public commentary.' },
    { key: 'Recency', range: '0–1', kind: 'Objective', note: 'How current the item is, weighted against the question’s time window.' },
    { key: 'Used in answer', range: 'Yes / No', kind: 'Forecast-specific', note: 'Whether the synthesis step actually leaned on this item.' },
    { key: 'Impact direction', range: '+ / 0 / −', kind: 'Forecast-specific', note: 'Whether the item pushed probability up, down, or neither.' },
    { key: 'Impact magnitude', range: '0–1', kind: 'Forecast-specific', note: 'How strongly the item moved the forecast.' },
];

export function MethodologyPage(props: MethodologyPageProps) {
    return (
        <PageShell {...props}>
            <PageHeader
                eyebrow="Methodology"
                eyebrowDot="teal"
                title={<>How a question becomes a forecast.</>}
                description="The forecast is the visible artifact. Behind it is a five-stage pipeline that writes a structured record at every step — designed so any claim, score, or judgment can be traced back to its source."
            />

            <section className="w-full px-6 pb-16">
                <div className="max-w-5xl mx-auto">
                    <div className="rounded-2xl bg-white ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)] overflow-hidden">
                        <ol className="divide-y divide-slate-200/70">
                            {STAGES.map((stage) => (
                                <li key={stage.n} className="p-7 lg:p-9">
                                    <div className="grid grid-cols-1 lg:grid-cols-12 gap-6 lg:gap-10">
                                        <div className="lg:col-span-2">
                                            <p className="text-[11px] font-medium tabular-nums text-slate-400">
                                                {stage.n}
                                            </p>
                                            <p className="mt-2 text-2xl font-medium tracking-tight text-gray-900">
                                                {stage.title}
                                            </p>
                                        </div>
                                        <div className="lg:col-span-7">
                                            <p className="text-[15px] leading-[1.75] text-slate-700">
                                                {stage.body}
                                            </p>
                                        </div>
                                        <div className="lg:col-span-3 space-y-3">
                                            <ArtifactCard
                                                label="Writes"
                                                value={stage.artifact}
                                            />
                                            <ArtifactCard label="Powered by" value={stage.tech} />
                                        </div>
                                    </div>
                                </li>
                            ))}
                        </ol>
                    </div>
                </div>
            </section>

            <section className="w-full px-6 py-16 lg:py-24">
                <div className="max-w-5xl mx-auto">
                    <div className="mb-10 lg:mb-14">
                        <div className="mb-4 inline-flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" />
                            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                                The evidence rating
                            </span>
                        </div>
                        <h2 className="text-3xl lg:text-[2.25rem] font-medium leading-tight tracking-[-0.025em] text-gray-900 max-w-3xl">
                            Six dimensions. Three intrinsic, three forecast-specific.
                        </h2>
                        <p className="mt-5 max-w-2xl text-[15px] leading-[1.7] text-slate-600">
                            Each piece of evidence is rated twice — once for what it is,
                            and once for how it was used in this specific forecast.
                        </p>
                    </div>

                    <div className="rounded-2xl bg-white ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)] overflow-hidden">
                        <table className="w-full text-left">
                            <thead className="bg-slate-50/60">
                                <tr className="text-[10.5px] font-medium uppercase tracking-[0.12em] text-slate-500">
                                    <th className="px-6 py-3 font-medium">Dimension</th>
                                    <th className="px-6 py-3 font-medium">Range</th>
                                    <th className="px-6 py-3 font-medium hidden md:table-cell">Kind</th>
                                    <th className="px-6 py-3 font-medium">Meaning</th>
                                </tr>
                            </thead>
                            <tbody className="divide-y divide-slate-200/70">
                                {EVIDENCE_DIMENSIONS.map((dim) => (
                                    <tr key={dim.key} className="text-[13.5px]">
                                        <td className="px-6 py-4 font-medium text-gray-900">
                                            {dim.key}
                                        </td>
                                        <td className="px-6 py-4 text-slate-600 tabular-nums">
                                            {dim.range}
                                        </td>
                                        <td className="px-6 py-4 hidden md:table-cell">
                                            <span
                                                className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-[10.5px] font-medium ${
                                                    dim.kind === 'Objective'
                                                        ? 'bg-anizai-teal-50 text-anizai-teal-800'
                                                        : 'bg-anizai-purple-50 text-anizai-purple-800'
                                                }`}
                                            >
                                                <span
                                                    className={`h-1.5 w-1.5 rounded-full ${
                                                        dim.kind === 'Objective'
                                                            ? 'bg-anizai-teal-500'
                                                            : 'bg-anizai-purple-500'
                                                    }`}
                                                />
                                                {dim.kind}
                                            </span>
                                        </td>
                                        <td className="px-6 py-4 text-slate-600 leading-snug">
                                            {dim.note}
                                        </td>
                                    </tr>
                                ))}
                            </tbody>
                        </table>
                    </div>
                </div>
            </section>

            <section className="w-full px-6 py-16 lg:py-24">
                <div className="max-w-5xl mx-auto">
                    <div className="mb-10 lg:mb-14">
                        <div className="mb-4 inline-flex items-center gap-2">
                            <span className="h-1.5 w-1.5 rounded-full bg-anizai-teal-500" />
                            <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                                Operating principles
                            </span>
                        </div>
                        <h2 className="text-3xl lg:text-[2.25rem] font-medium leading-tight tracking-[-0.025em] text-gray-900 max-w-3xl">
                            Four commitments behind every forecast.
                        </h2>
                    </div>

                    <div className="grid grid-cols-1 md:grid-cols-2 gap-4">
                        {PRINCIPLES.map((p) => (
                            <article
                                key={p.title}
                                className="rounded-2xl bg-white p-7 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)]"
                            >
                                <p className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-anizai-purple-600">
                                    {p.eyebrow}
                                </p>
                                <h3 className="mt-2 text-xl font-medium tracking-tight text-gray-900">
                                    {p.title}
                                </h3>
                                <p className="mt-3 text-[14.5px] leading-[1.7] text-slate-600">
                                    {p.body}
                                </p>
                            </article>
                        ))}
                    </div>

                    <p className="mt-10 text-[12.5px] text-slate-400">
                        Forecasts are probabilistic. They are guidance for human decisions
                        — not certainties.
                    </p>
                </div>
            </section>
        </PageShell>
    );
}

function ArtifactCard({ label, value }: { label: string; value: string }) {
    return (
        <div className="rounded-lg bg-slate-50/80 ring-1 ring-slate-900/[0.04] px-4 py-3">
            <p className="text-[10px] font-medium uppercase tracking-[0.14em] text-slate-400">
                {label}
            </p>
            <p className="mt-1 text-[12.5px] text-slate-800 leading-snug">{value}</p>
        </div>
    );
}
