import { Hero } from '../components/landing/Hero';
import { ProductExplanation } from '../components/landing/ProductExplanation';
import { UIShowcase } from '../components/landing/UIShowcase';
import { HowItWorks } from '../components/landing/HowItWorks';
import { WhoItsFor } from '../components/landing/WhoItsFor';
import { FinalCTA } from '../components/landing/FinalCTA';
import { Footer } from '../components/landing/Footer';

interface LandingPageProps {
    onAuth: () => void;
    onContact?: () => void;
    onNavigation?: {
        features: () => void;
        methodology: () => void;
        changelog: () => void;
        about: () => void;
        blog: () => void;
        terms: () => void;
        privacy: () => void;
        cookies: () => void;
    };
}

export function LandingPage({ onAuth, onContact, onNavigation }: LandingPageProps) {
    return (
        <div className="w-full h-screen overflow-y-auto bg-white">
            <Hero onAuth={onAuth} />
            <ProductExplanation />
            <UIShowcase />
            <HowItWorks />
            <WhoItsFor />
            <FinalCTA onAuth={onAuth} />
            <Footer onAuth={onAuth} onContact={onContact} onNavigation={onNavigation} />
        </div>
    );
}
 