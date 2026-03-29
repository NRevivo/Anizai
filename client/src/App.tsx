import { useCallback, useEffect, useMemo, useState } from 'react';
import { onAuthStateChanged, signInWithPopup, signOut, createUserWithEmailAndPassword, signInWithEmailAndPassword, updateProfile } from 'firebase/auth';

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
import { auth, googleProvider } from './lib/firebase';
import { ApiAuthError } from './lib/api';
import {
  fetchCurrentUser,
  updateUserPlan,
  type UserPlan,
  type UserProfile,
} from './services/user.service';
import {
  addSessionMessage,
  createSession,
  deleteSession,
  fetchSessionDetail,
  fetchSessions,
  type SessionDetail,
  type SessionListItem,
  type SessionStatus,
} from './services/session.service';
import { fetchTrendingForecasts, type TrendingForecast } from './services/trending.service';
import type { ChatMessage, Prediction, PredictionSession, SentimentDataPoint, TimelineEvent } from './types';

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

interface TrendingQuestionView {
  id: string;
  question: string;
  probability: number;
  trend: 'up' | 'down' | 'stable';
  context: string;
}

function normalizePercent(val: number | null | undefined): number {
  if (val == null) return 0;
  if (Math.abs(val) <= 1 && val !== 0) return val * 100;
  return val;
}

function mapSessionStatus(status: SessionStatus, confidence: number | null): 'stable' | 'volatile' {
  if (status === 'failed') {
    return 'volatile';
  }
  if (status === 'running') {
    return 'volatile';
  }
  const normalizedConfidence = normalizePercent(confidence);
  if (normalizedConfidence !== 0 && normalizedConfidence < 40) {
    return 'volatile';
  }
  return 'stable';
}

function toSidebarSession(session: SessionListItem): PredictionSession {
  return {
    id: session.id,
    question: session.title ?? session.question,
    probability: normalizePercent(session.latestProbability),
    status: mapSessionStatus(session.status, session.latestConfidence),
    lastUpdated: new Date(session.lastActivityAt || session.updatedAt || session.createdAt),
  };
}

function toPrediction(detail: SessionDetail | null): Prediction | null {
  if (!detail) {
    return null;
  }

  const probability = detail.result?.finalProbability ?? detail.session.latestProbability ?? 0;
  const confidence = detail.result?.confidence ?? detail.session.latestConfidence ?? 0;
  const explanation =
    detail.result?.detailedExplanation ??
    detail.result?.bottomLineAnswer ??
    detail.result?.summaryMarkdown ??
    'Analysis in progress.';

  return {
    id: detail.session.id,
    question: detail.session.question,
    probability: normalizePercent(probability),
    confidenceIndex: normalizePercent(confidence),
    status: mapSessionStatus(detail.session.status, detail.session.latestConfidence),
    explanation,
    marketProbability: detail.result?.marketProbability != null ? normalizePercent(detail.result.marketProbability) : undefined,
    createdAt: new Date(detail.session.createdAt),
    updatedAt: new Date(detail.session.updatedAt),
  };
}

function toSentimentPoints(detail: SessionDetail | null): SentimentDataPoint[] {
  if (!detail) {
    return [];
  }

  return detail.sentimentTimeSeries.map((point) => ({
    date: point.date || new Date(point.ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    expertSentiment: normalizePercent(point.expertSentiment),
    expertUpper: point.expertUpper != null ? normalizePercent(point.expertUpper) : undefined,
    expertLower: point.expertLower != null ? normalizePercent(point.expertLower) : undefined,
    publicSentiment: normalizePercent(point.publicSentiment),
  }));
}

function toTimelineEvents(detail: SessionDetail | null): TimelineEvent[] {
  if (!detail) {
    return [];
  }

  return detail.evidence.map((evidence) => ({
    id: evidence.id,
    date: new Date(evidence.publishedAt ?? evidence.createdAt).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    }),
    title: evidence.title,
    sourceType: evidence.type === 'market' ? 'expert' : evidence.type,
    impact: evidence.impact ?? 'neutral',
    impactLabel: evidence.impactLabel ?? undefined,
    isKeyEvidence: evidence.isKeyEvidence,
    description: evidence.snippet,
  }));
}

function toChatMessages(detail: SessionDetail | null): ChatMessage[] {
  if (!detail) {
    return [];
  }

  return detail.messages.map((message) => ({
    id: message.id,
    role: message.role === 'system' ? 'assistant' : message.role,
    content: message.content,
    timestamp: new Date(message.createdAt),
  }));
}

function toTrendingView(items: TrendingForecast[]): TrendingQuestionView[] {
  return items.map((item) => {
    let probability = item.probability ?? 0.5;
    probability = normalizePercent(probability);
    
    // Handle the case where probability was missing or 0
    if (probability === 0 && item.probability == null) {
      probability = 50;
    }

    const trend: 'up' | 'down' | 'stable' =
      probability >= 60 ? 'up' : probability <= 40 ? 'down' : 'stable';

    return {
      id: item.id,
      question: item.question ?? item.title ?? 'Untitled forecast',
      probability,
      trend,
      context: `Popularity score: ${item.popularityScore}`,
    };
  });
}

function App() {
  const [appState, setAppState] = useState<AppState>('landing');
  const [authError, setAuthError] = useState<string | null>(null);
  const [isHydratingAuth, setIsHydratingAuth] = useState(true);
  const [isDashboardLoading, setIsDashboardLoading] = useState(false);

  const [userProfile, setUserProfile] = useState<UserProfile | null>(null);
  const [sessions, setSessions] = useState<SessionListItem[]>([]);
  const [trending, setTrending] = useState<TrendingForecast[]>([]);
  const [activeSessionId, setActiveSessionId] = useState<string | null>(null);
  const [activeSessionDetail, setActiveSessionDetail] = useState<SessionDetail | null>(null);

  const loadSession = useCallback(async (sessionId: string) => {
    const detail = await fetchSessionDetail(sessionId);
    setActiveSessionId(sessionId);
    setActiveSessionDetail(detail);
  }, []);

  const loadDashboardData = useCallback(async (preferredSessionId?: string | null) => {
    setIsDashboardLoading(true);

    try {
      const [profile, sessionsData, trendingData] = await Promise.all([
        fetchCurrentUser(),
        fetchSessions(),
        fetchTrendingForecasts(20),
      ]);

      setUserProfile(profile);
      setSessions(sessionsData);
      setTrending(trendingData);

      const nextSessionId =
        preferredSessionId && sessionsData.some((session) => session.id === preferredSessionId)
          ? preferredSessionId
          : sessionsData[0]?.id ?? null;

      if (nextSessionId) {
        await loadSession(nextSessionId);
      } else {
        setActiveSessionId(null);
        setActiveSessionDetail(null);
      }
    } finally {
      setIsDashboardLoading(false);
    }
  }, [loadSession]);

  useEffect(() => {
    const unsubscribe = onAuthStateChanged(auth, async (firebaseUser) => {
      if (!firebaseUser) {
        setUserProfile(null);
        setSessions([]);
        setTrending([]);
        setActiveSessionId(null);
        setActiveSessionDetail(null);
        setAppState('landing');
        setIsHydratingAuth(false);
        return;
      }

      try {
        setAuthError(null);
        await loadDashboardData();
        setAppState('dashboard');
      } catch (error) {
        if (error instanceof ApiAuthError) {
          await signOut(auth);
        }
        setAuthError(error instanceof Error ? error.message : 'Failed to initialize session');
      } finally {
        setIsHydratingAuth(false);
      }
    });

    return () => unsubscribe();
  }, [loadDashboardData]);

  const handleGoToLogin = () => {
    setAppState('login');
  };

  const handleGoToSignup = () => {
    setAppState('signup');
  };

  const handleBackToLanding = () => {
    setAppState('landing');
  };

  const handleGoogleAuth = async () => {
    try {
      setAuthError(null);
      await signInWithPopup(auth, googleProvider);
      await loadDashboardData();
      setAppState('dashboard');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Google sign-in failed');
    }
  };

  const handleEmailAuth = async (email: string, password?: string) => {
    if (!password) {
      setAuthError('Password is required for email sign-in.');
      return;
    }
    try {
      setAuthError(null);
      await signInWithEmailAndPassword(auth, email, password);
      await loadDashboardData();
      setAppState('dashboard');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Email sign-in failed');
    }
  };

  const handleCreateAccount = async (payload: { name: string; email: string; password: string }) => {
    try {
      setAuthError(null);
      const userCredential = await createUserWithEmailAndPassword(auth, payload.email, payload.password);
      if (userCredential.user) {
        await updateProfile(userCredential.user, { displayName: payload.name });
        await userCredential.user.getIdToken(true);
      }
      await loadDashboardData();
      setAppState('plan-selection');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Email sign-up failed');
    }
  };

  const handleGoogleSignup = async () => {
    try {
      setAuthError(null);
      await signInWithPopup(auth, googleProvider);
      await loadDashboardData();
      setAppState('plan-selection');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Google sign-up failed');
    }
  };

  const handleSelectPlan = async (plan: UserPlan) => {
    try {
      setAuthError(null);
      const updatedProfile = await updateUserPlan(plan);
      setUserProfile(updatedProfile);
      setAppState('dashboard');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Failed to update membership');
    }
  };

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

  const handleLogout = async () => {
    setAuthError(null);
    await signOut(auth);
    setAppState('landing');
  };

  const handleSettings = () => {
    console.log('Open settings');
  };

  const handleSessionSelect = async (sessionId: string) => {
    if (sessionId === activeSessionId) {
      return;
    }

    try {
      setAuthError(null);
      setIsDashboardLoading(true);
      await loadSession(sessionId);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Failed to load session');
    } finally {
      setIsDashboardLoading(false);
    }
  };

  const handleCreateSession = async (question: string) => {
    try {
      setAuthError(null);
      setIsDashboardLoading(true);
      const created = await createSession({ question });
      const sessionsData = await fetchSessions();
      setSessions(sessionsData);
      await loadSession(created.id);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Failed to create session');
    } finally {
      setIsDashboardLoading(false);
    }
  };

  const handleSendMessage = async (message: string) => {
    const sessionId = activeSessionId;
    if (!sessionId) {
      setAuthError('No active session selected.');
      return;
    }

    const content = message.trim();
    if (!content) {
      return;
    }

    try {
      setAuthError(null);
      await addSessionMessage(sessionId, {
        role: 'user',
        content,
      });
      await loadSession(sessionId);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Failed to save message');
    }
  };

  const handleDeleteSession = async (sessionId: string) => {
    try {
      setAuthError(null);
      setIsDashboardLoading(true);

      await deleteSession(sessionId);

      const sessionsData = await fetchSessions();
      setSessions(sessionsData);

      const shouldSwitchActive = activeSessionId === sessionId;
      if (!shouldSwitchActive) {
        return;
      }

      const nextSessionId = sessionsData[0]?.id ?? null;
      if (nextSessionId) {
        await loadSession(nextSessionId);
      } else {
        setActiveSessionId(null);
        setActiveSessionDetail(null);
      }
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Failed to delete session');
    } finally {
      setIsDashboardLoading(false);
    }
  };

  const sidebarSessions = useMemo(() => sessions.map(toSidebarSession), [sessions]);
  const prediction = useMemo(() => toPrediction(activeSessionDetail), [activeSessionDetail]);
  const sentimentData = useMemo(() => toSentimentPoints(activeSessionDetail), [activeSessionDetail]);
  const timelineEvents = useMemo(() => toTimelineEvents(activeSessionDetail), [activeSessionDetail]);
  const messages = useMemo(() => toChatMessages(activeSessionDetail), [activeSessionDetail]);
  const trendingItems = useMemo(() => toTrendingView(trending), [trending]);

  if (isHydratingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 text-gray-600">
        Initializing authentication...
      </div>
    );
  }

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
      <>
        {authError ? (
          <div className="fixed top-4 left-1/2 z-50 -translate-x-1/2 rounded-md bg-red-50 px-4 py-2 text-sm text-red-700 shadow">
            {authError}
          </div>
        ) : null}
        <LoginPage
          onGoogleAuth={handleGoogleAuth}
          onEmailAuth={handleEmailAuth}
          onBack={handleBackToLanding}
          onSignUp={handleGoToSignup}
        />
      </>
    );
  }

  if (appState === 'signup') {
    return (
      <>
        {authError ? (
          <div className="fixed top-4 left-1/2 z-50 -translate-x-1/2 rounded-md bg-red-50 px-4 py-2 text-sm text-red-700 shadow">
            {authError}
          </div>
        ) : null}
        <SignupPage
          onCreateAccount={handleCreateAccount}
          onGoogleSignup={handleGoogleSignup}
          onBack={handleGoToLogin}
          onSignIn={handleGoToLogin}
        />
      </>
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
        onGetStarted={handleGoToLogin}
        onMethodology={() => setAppState('methodology')}
      />
    );
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
    <>
      {authError ? (
        <div className="fixed top-4 left-1/2 z-50 -translate-x-1/2 rounded-md bg-red-50 px-4 py-2 text-sm text-red-700 shadow">
          {authError}
        </div>
      ) : null}
      <DashboardPage
        sessions={sidebarSessions}
        activeSessionId={activeSessionId}
        prediction={prediction}
        sentimentData={sentimentData}
        timelineEvents={timelineEvents}
        messages={messages}
        trendingForecasts={trendingItems}
        userDisplayName={userProfile?.displayName}
        userPlan={userProfile?.plan}
        onSessionSelect={(sessionId) => {
          void handleSessionSelect(sessionId);
        }}
        onCreateSession={handleCreateSession}
        onSendMessage={handleSendMessage}
        onDeleteSession={handleDeleteSession}
        onLogout={handleLogout}
        onSettings={handleSettings}
        isLoading={isDashboardLoading}
      />
    </>
  );
}

export default App;
