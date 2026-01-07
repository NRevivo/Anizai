import { useState } from 'react';
import { LandingPage } from './pages/LandingPage';
import { PlanSelection } from './pages/PlanSelection';
import { DashboardPage } from './pages/DashboardPage';

type AppState = 'landing' | 'plan-selection' | 'dashboard';
type UserType = 'new' | 'existing' | null;

function App() {
    const [appState, setAppState] = useState<AppState>('landing');
    const [userType, setUserType] = useState<UserType>(null);

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

    const handleSelectPlan = (plan: 'free' | 'premium') => {
        console.log(`Selected plan: ${plan}`);
        // In production, save plan selection to backend
        setAppState('dashboard');
    };

    // Render appropriate screen based on state
    if (appState === 'landing') {
        return <LandingPage onAuth={handleAuth} />;
    }

    if (appState === 'plan-selection') {
        return <PlanSelection onSelectPlan={handleSelectPlan} />;
    }

    return <DashboardPage />;
}

export default App;
