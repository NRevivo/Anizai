import { useState } from 'react';
import { LandingPage } from './pages/LandingPage';
import { LoginPage } from './pages/LoginPage';
import { PlanSelection } from './pages/PlanSelection';
import { DashboardPage } from './pages/DashboardPage';

type AppState = 'landing' | 'login' | 'plan-selection' | 'dashboard';
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
        return <LandingPage onAuth={handleGoToLogin} />;
    }

    if (appState === 'login') {
        return (
            <LoginPage
                onGoogleAuth={handleAuth}
                onEmailAuth={handleEmailAuth}
                onBack={() => setAppState('landing')}
            />
        );
    }

    if (appState === 'plan-selection') {
        return <PlanSelection onSelectPlan={handleSelectPlan} />;
    }

    return (
        <DashboardPage
            onLogout={handleLogout}
            onSettings={handleSettings}
        />
    );
}

export default App;

