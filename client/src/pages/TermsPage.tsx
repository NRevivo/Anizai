import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';

interface TermsPageProps extends Omit<PageShellProps, 'children'> {}

interface Section {
    title: string;
    body: string;
    list?: string[];
}

const SECTIONS: Section[] = [
    {
        title: '1. Acceptance of Terms',
        body:
            'By accessing and using the Anizai platform, you accept and agree to be bound by these terms. If you do not agree, please do not use this service.',
    },
    {
        title: '2. Use License',
        body:
            'Permission is granted to temporarily download one copy of the materials on Anizai for personal, non-commercial viewing only. This is a license, not a transfer of title.',
        list: [
            'Modify or copy the materials',
            'Use the materials for any commercial purpose or public display',
            'Attempt to decompile or reverse-engineer any software on the platform',
            'Remove copyright or other proprietary notations',
            'Transmit the materials to another person or mirror them on any other server',
        ],
    },
    {
        title: '3. Disclaimer',
        body:
            'The materials on Anizai are provided on an "as is" basis. Forecasts are probabilistic and provided for informational purposes only. Anizai makes no warranties, expressed or implied, and disclaims all other warranties.',
    },
    {
        title: '4. Limitations',
        body:
            'In no event shall Anizai or its suppliers be liable for any damages arising from the use or inability to use the materials on Anizai.',
    },
    {
        title: '5. Accuracy of Materials',
        body:
            'Materials may include errors. Anizai does not warrant that the materials are accurate, complete, or current, and may update them at any time without notice.',
    },
    {
        title: '6. Links',
        body:
            'Anizai is not responsible for the contents of linked sites. Use of any linked website is at the user\'s own risk.',
    },
    {
        title: '7. Modifications',
        body:
            'Anizai may revise these terms at any time without notice. By using this platform, you agree to be bound by the then-current version.',
    },
    {
        title: '8. Governing Law',
        body:
            'These terms are governed by the applicable laws of the jurisdiction in which Anizai operates.',
    },
];

export function TermsPage(props: TermsPageProps) {
    return (
        <PageShell {...props}>
            <PageHeader
                eyebrow="Legal · Terms"
                title={<>Terms of service.</>}
                description="Last updated January 2026."
            />
            <LegalArticle sections={SECTIONS} />
        </PageShell>
    );
}

export function LegalArticle({ sections }: { sections: Section[] }) {
    return (
        <section className="w-full px-6 pb-20">
            <div className="max-w-3xl mx-auto">
                <div className="rounded-2xl bg-white p-7 lg:p-10 ring-1 ring-slate-900/[0.05] shadow-[0_4px_24px_rgba(15,23,42,0.05)]">
                    <div className="space-y-10">
                        {sections.map((sec) => (
                            <article key={sec.title}>
                                <h2 className="text-lg font-medium tracking-tight text-gray-900">
                                    {sec.title}
                                </h2>
                                <p className="mt-3 text-[14.5px] leading-[1.75] text-slate-700">
                                    {sec.body}
                                </p>
                                {sec.list ? (
                                    <ul className="mt-4 space-y-2 text-[14px] leading-[1.65] text-slate-700">
                                        {sec.list.map((item) => (
                                            <li key={item} className="flex items-start gap-3">
                                                <span className="mt-2 h-1 w-1 rounded-full bg-slate-400 shrink-0" />
                                                <span>{item}</span>
                                            </li>
                                        ))}
                                    </ul>
                                ) : null}
                            </article>
                        ))}
                    </div>
                </div>
                <p className="mt-6 text-[12.5px] text-slate-500">
                    Questions? Reach us via the Contact page.
                </p>
            </div>
        </section>
    );
}
