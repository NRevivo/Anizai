import { Hero } from '../components/landing/Hero';
import { DashboardPreview } from '../components/landing/DashboardPreview';
import { HowItWorks } from '../components/landing/HowItWorks';
import { WhatYouGet } from '../components/landing/WhatYouGet';
import { QuestionsWeTrack } from '../components/landing/QuestionsWeTrack';
import { ClosingCTA } from '../components/landing/ClosingCTA';
import { PageShell, type PageShellProps } from '../components/site/PageShell';

interface LandingPageProps extends Omit<PageShellProps, 'children'> {}

const PREVIEW_ANCHOR = 'example-forecast';

export function LandingPage(props: LandingPageProps) {
    const scrollToPreview = () => {
        const target = document.getElementById(PREVIEW_ANCHOR);
        if (target) {
            target.scrollIntoView({ behavior: 'smooth', block: 'start' });
        }
    };

    return (
        <PageShell {...props}>
            <Hero onPrimary={props.onSignUp} onSecondary={scrollToPreview} />
            <DashboardPreview id={PREVIEW_ANCHOR} />
            <HowItWorks />
            <WhatYouGet />
            <QuestionsWeTrack />
            <ClosingCTA onPrimary={props.onSignUp} />
        </PageShell>
    );
}
