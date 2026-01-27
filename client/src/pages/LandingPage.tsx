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
        home: () => void;
        features: () => void;
        methodology: () => void;
        changelog: () => void;
        about: () => void;
        blog: () => void;
        terms: () => void;
        privacy: () => void;
        cookies: () => void;
        pricing: () => void;
    };
}

export function LandingPage({ onAuth, onContact, onNavigation }: LandingPageProps) {
    return (
        <div className="w-full h-screen overflow-y-auto bg-white">
            <div className="w-full">
                <div className="max-w-6xl mx-auto px-4 py-3 flex items-center justify-between">
                    <div className="flex items-center gap-8">
                        <button
                            onClick={() => onNavigation?.about()}
                            className="text-base text-gray-600 hover:text-gray-900 transition-colors"
                        >
                            About
                        </button>
                        <button
                            onClick={() => onNavigation?.home()}
                            className="text-base text-gray-600 hover:text-gray-900 transition-colors"
                        >
                            Home
                        </button>
                        <button
                            onClick={() => onContact?.()}
                            className="text-base text-gray-600 hover:text-gray-900 transition-colors"
                        >
                            Contact
                        </button>
                    </div>
                    <div className="flex items-center gap-12">
                        <button
                            onClick={onAuth}
                            className="inline-flex items-center gap-2 text-base text-gray-600 hover:text-gray-900 transition-colors"
                            aria-label="Account login"
                        >
                            <svg className="w-6 h-6" viewBox="0 0 24 24" fill="currentColor" aria-hidden="true">
                                <path d="M12 12c2.761 0 5-2.239 5-5s-2.239-5-5-5-5 2.239-5 5 2.239 5 5 5zm0 2c-3.314 0-10 1.657-10 4.971V22h20v-3.029C22 15.657 15.314 14 12 14z" />
                            </svg>
                        </button>
                    </div>
                </div>
            </div>
            <Hero
                onAuth={onAuth}
                onTerms={onNavigation?.terms}
                onPrivacy={onNavigation?.privacy}
            />
            <ProductExplanation />
            <UIShowcase />
            <HowItWorks />
            <WhoItsFor />
            <FinalCTA onAuth={onAuth} />
            <Footer onAuth={onAuth} onContact={onContact} onNavigation={onNavigation} />
        </div>
    );
}

