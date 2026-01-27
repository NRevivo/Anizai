import { useState } from 'react';

import { LandingPage } from './pages/LandingPage';
import { LoginPage } from './pages/LoginPage';
import { SignupPage } from './pages/SignupPage';
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
  | 'signup'
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

  const handleGoToSignup = () => {
    setAppState('signup');
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

  const handleGoogleAuth = async () => {
    try {
      await signInWithPopup(auth, googleProvider);
      setUserType('existing');
      setAppState('dashboard');
    } catch (error) {
      console.error('Google sign-in failed', error);
    }
  };

  const handleEmailAuth = (email: string) => {
    console.log(`Email auth for: ${email}`);
    handleAuth();
  };

  const handleCreateAccount = (payload: { name: string; email: string; password: string }) => {
    console.log(`Create account for: ${payload.email}`);
    setUserType('new');
    setAppState('plan-selection');
  };

  const handleGoogleSignup = async () => {
    try {
      await signInWithPopup(auth, googleProvider);
      setUserType('new');
      setAppState('plan-selection');
    } catch (error) {
      console.error('Google sign-up failed', error);
    }
  };

  // ---------- Plans ----------

  const handleSelectPlan = (plan: 'free' | 'premium') => {
    console.log(`Selected plan: ${plan}`);
    setAppState('dashboard');
  };

  // ---------- Footer / pages navigation ----------

  const navigationHandlers = {
    home: () => setAppState('landing'),
    features: () => setAppState('features'),
    methodology: () => setAppState('methodology'),
    changelog: () => setAppState('changelog'),
    about: () => setAppState('about'),
    blog: () => setAppState('blog'),
    terms: () => setAppState('terms'),
    privacy: () => setAppState('privacy'),
    cookies: () => setAppState('cookies'),
    pricing: () => setAppState('plan-selection'),
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
        onGoogleAuth={handleGoogleAuth}
        onEmailAuth={handleEmailAuth}
        onBack={handleBackToLanding}
        onSignUp={handleGoToSignup}
      />
    );
  }

  if (appState === 'signup') {
    return (
      <SignupPage
        onCreateAccount={handleCreateAccount}
        onGoogleSignup={handleGoogleSignup}
        onBack={handleGoToLogin}
        onSignIn={handleGoToLogin}
      />
    );
  }

  if (appState === 'contact') {
    return <ContactPage onBack={handleBackToLanding} />;
  }
if (appState === 'features') {
  return <FeaturesPage onBack={handleBackToLanding} onGetStarted={handleGoToLogin} />;
}


  if (appState === 'methodology') {
    return <MethodologyPage onBack={handleBackToLanding} />;
  }

  if (appState === 'changelog') {
    return <ChangelogPage onBack={handleBackToLanding} />;
  } 

    if (appState === 'about') {
    return (
        <AboutPage
        onBack={handleBackToLanding}
        onGetStarted={handleGoToLogin} // או plan-selection אם זו הכניסה אצלך
        onMethodology={() => setAppState('methodology')}
        />
    );
    }

  if (appState === 'blog') {
    return <BlogPage onBack={handleBackToLanding} />;
  }
if (appState === 'terms') return <TermsPage onBack={handleBackToLanding} />;
if (appState === 'privacy') return <PrivacyPage onBack={handleBackToLanding} />;
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
