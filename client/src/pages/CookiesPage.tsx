import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';
import { LegalArticle } from './TermsPage';

interface CookiesPageProps extends Omit<PageShellProps, 'children'> {}

const SECTIONS = [
    {
        title: '1. What are cookies?',
        body:
            'Cookies are small pieces of text stored on your device when you visit a website. They help remember preferences and provide analytics that improve reliability and performance.',
    },
    {
        title: '2. Essential cookies',
        body:
            'These cookies are required for the platform to function — authentication tokens, session state, and security. They cannot be disabled while you are signed in.',
    },
    {
        title: '3. Functional cookies',
        body:
            'These remember your workspace preferences — which forecast you had open, sidebar state, and similar UI choices.',
    },
    {
        title: '4. Analytics cookies',
        body:
            'We may use analytics cookies to understand how the product is used in aggregate. These are anonymized and used only to improve the platform.',
    },
    {
        title: '5. Third-party cookies',
        body:
            'We may allow third-party providers (analytics, embedded services) to place cookies. Those providers follow their own privacy policies.',
    },
    {
        title: '6. Your choices',
        body:
            'You can accept or reject cookies using your browser settings. Disabling some cookies may affect parts of the site.',
    },
    {
        title: '7. How long do cookies last?',
        body:
            'Cookies are either session-based (deleted when you close the browser) or persistent (stored until they expire). Duration depends on the cookie’s purpose.',
    },
];

export function CookiesPage(props: CookiesPageProps) {
    return (
        <PageShell {...props}>
            <PageHeader
                eyebrow="Legal · Cookies"
                title={<>Cookie policy.</>}
                description="Last updated January 2026."
            />
            <LegalArticle sections={SECTIONS} />
        </PageShell>
    );
}
