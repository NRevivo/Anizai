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
  | 'login'
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

  // ---------- Navigation helpers ----------

  const handleGoToLogin = () => {
    setAppState('login');
  };

  const handleBackToLanding = () => {
    setAppState('landing');
  };

  // ---------- Auth logic ----------

  const handleAuth = () => {
    // Mock authentication
    const isNewUser = Math.random() > 0.5;

    setUserType(isNewUser ? 'new' : 'existing');

    if (isNewUser) {
      setAppState('plan-selection');
    } else {
      setAppState('dashboard');
    }
  };

  const handleEmailAuth = (email: string) => {
    console.log(`Email auth for: ${email}`);
    handleAuth();
  };

  // ---------- Plans ----------

  const handleSelectPlan = (plan: 'free' | 'premium') => {
    console.log(`Selected plan: ${plan}`);
    setAppState('dashboard');
  };

  // ---------- Footer / pages navigation ----------

  const navigationHandlers = {
    features: () => setAppState('features'),
    methodology: () => setAppState('methodology'),
    changelog: () => setAppState('changelog'),
    about: () => setAppState('about'),
    blog: () => setAppState('blog'),
    terms: () => setAppState('terms'),
    privacy: () => setAppState('privacy'),
    cookies: () => setAppState('cookies'),
    pricing: handleGoToLogin, // אם יש Pricing בפוטר
  };

  // ---------- Dashboard actions ----------

  const handleLogout = () => {
    setUserType(null);
    setAppState('landing');
  };

  const handleSettings = () => {
    console.log('Open settings');
  };

  // ---------- Render ----------

  if (appState === 'landing') {
    return (
      <LandingPage
        onAuth={handleGoToLogin}
        onContact={() => setAppState('contact')}
        onNavigation={navigationHandlers}
      />
    );
  }

  if (appState === 'login') {
    return (
      <LoginPage
        onGoogleAuth={handleAuth}
        onEmailAuth={handleEmailAuth}
        onBack={handleBackToLanding}
      />
    );
  }

  if (appState === 'contact') {
    return <ContactPage onBack={handleBackToLanding} />;
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
    return (
      <PlanSelection
        onSelectPlan={handleSelectPlan}
        onBack={handleBackToLanding}
      />
    );
  }

  return (
    <DashboardPage
      onLogout={handleLogout}
      onSettings={handleSettings}
    />
  );
}

export default App;
