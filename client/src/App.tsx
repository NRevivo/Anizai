import { useState } from 'react';
import { LandingPage } from './pages/LandingPage';
import { LoginPage } from './pages/LoginPage';
import { PlanSelection } from './pages/PlanSelection';
import { DashboardPage } from './pages/DashboardPage';
import { ContactPage } from './pages/ContactPage';
import { FeaturesPage } from './pages/FeaturesPage';
import { MethodologyPage } from './pages/MethodologyPage';
import { ChangelogPage } from './pages/ChangelogPage';
import { AboutPage } from './pages/AboutPage';
import { BlogPage } from './pages/BlogPage';
import { TermsPage } from './pages/TermsPage';
import { PrivacyPage } from './pages/PrivacyPage';
import { CookiesPage } from './pages/CookiesPage';

type AppState = 
    | 'landing' 
    | 'plan-selection' 
    | 'dashboard' 
    | 'contact'
    | 'features'
    | 'methodology'
    | 'changelog'
    | 'about'
    | 'blog'
    | 'terms'
    | 'privacy'
    | 'cookies';
type UserType = 'new' | 'existing' | null;

function App() {
    const [appState, setAppState] = useState<AppState>('landing');
    const [userType, setUserType] = useState<UserType>(null);

    const handleGoToLogin = () => {
        setAppState('login');
    };

    const handleAuth = () => {
        // Mock authentication - in production, this would call Google OAuth
        // For now, randomly decide if user is new or existing
        const isNewUser = Math.random() > 0.5; // 50% chance

        setUserType(isNewUser ? 'new' : 'existing');

        if (isNewUser) {
            // New users go to plan selection
            setAppState('plan-selection');
        } else {
            // Existing users go straight to dashboard
            setAppState('dashboard');
        }
    };

    const handleEmailAuth = (email: string) => {
        console.log(`Email auth for: ${email}`);
        // In production, this would send a magic link or start email auth flow
        handleAuth(); // For now, just trigger the same flow
    };

    const handleSelectPlan = (plan: 'free' | 'premium') => {
        console.log(`Selected plan: ${plan}`);
        // In production, save plan selection to backend
        setAppState('dashboard');
    };

    const handleContact = () => {
        setAppState('contact');
    };

    const handleBackFromContact = () => {
        setAppState('landing');
    };

    const handleBackToLanding = () => {
        setAppState('landing');
    };

    const navigationHandlers = {
        features: () => setAppState('features'),
        methodology: () => setAppState('methodology'),
        changelog: () => setAppState('changelog'),
        about: () => setAppState('about'),
        blog: () => setAppState('blog'),
        terms: () => setAppState('terms'),
        privacy: () => setAppState('privacy'),
        cookies: () => setAppState('cookies'),
    };

    const handleLogout = () => {
        // Clear user state and go back to landing
        setUserType(null);
        setAppState('landing');
    };

    const handleSettings = () => {
        // TODO: Open settings modal or navigate to settings page
        console.log('Open settings');
    };

    // Render appropriate screen based on state
    if (appState === 'landing') {
        return <LandingPage onAuth={handleAuth} onContact={handleContact} onNavigation={navigationHandlers} />;
    }

    if (appState === 'contact') {
        return <ContactPage onBack={handleBackFromContact} />;
    }

    if (appState === 'features') {
        return <FeaturesPage onBack={handleBackToLanding} />;
    }

    if (appState === 'methodology') {
        return <MethodologyPage onBack={handleBackToLanding} />;
    }

    if (appState === 'changelog') {
        return <ChangelogPage onBack={handleBackToLanding} />;
    }

    if (appState === 'about') {
        return <AboutPage onBack={handleBackToLanding} />;
    }

    if (appState === 'blog') {
        return <BlogPage onBack={handleBackToLanding} />;
    }

    if (appState === 'terms') {
        return <TermsPage onBack={handleBackToLanding} />;
    }

    if (appState === 'privacy') {
        return <PrivacyPage onBack={handleBackToLanding} />;
    }

    if (appState === 'cookies') {
        return <CookiesPage onBack={handleBackToLanding} />;
    }

    if (appState === 'plan-selection') {
        return <PlanSelection onSelectPlan={handleSelectPlan} onBack={handleBackToLanding} />;
    }

    return (
        <DashboardPage
            onLogout={handleLogout}
            onSettings={handleSettings}
        />
    );
}

export default App;

