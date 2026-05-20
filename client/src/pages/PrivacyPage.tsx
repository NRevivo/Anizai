import { PageShell, PageHeader, type PageShellProps } from '../components/site/PageShell';
import { LegalArticle } from './TermsPage';

interface PrivacyPageProps extends Omit<PageShellProps, 'children'> {}

const SECTIONS = [
    {
        title: '1. Information We Collect',
        body:
            'We may collect account details (such as your email), usage data (feature interactions), and content you submit during forecasting sessions — the questions you ask, the messages you send, and the choices you make in the workspace.',
    },
    {
        title: '2. How We Use Information',
        body:
            'Data is used to operate the platform, improve forecasting quality, troubleshoot issues, and enhance the overall user experience.',
    },
    {
        title: '3. Data Sharing',
        body:
            'We do not sell personal information. During the private beta, data is used only to support core functionality and improve the product.',
    },
    {
        title: '4. Security',
        body:
            'We apply reasonable security practices — encryption in transit, access controls, and audit logging — but no system can guarantee complete security.',
    },
    {
        title: '5. Data Retention',
        body:
            'Data is kept only as long as necessary for functionality or improvement, then deleted or anonymized when possible.',
    },
    {
        title: '6. Your Choices',
        body:
            'You may request deletion of your account data by contacting us via the Contact page.',
    },
];

export function PrivacyPage(props: PrivacyPageProps) {
    return (
        <PageShell {...props}>
            <PageHeader
                eyebrow="Legal · Privacy"
                title={<>Privacy policy.</>}
                description="Last updated January 2026."
            />
            <LegalArticle sections={SECTIONS} />
        </PageShell>
    );
}
