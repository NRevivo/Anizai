import { useCallback, useEffect, useMemo, useState } from 'react';

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
import { StateMessage } from './components/ui/StateMessage';
import { ApiError } from './lib/api';
import {
  getDemoUserProfile,
  updateUserPlan,
  type UserPlan,
  type UserProfile,
} from './services/user.service';
import {
  addSessionMessage,
  clarifySession,
  type ClarificationCandidate,
  createSession,
  deleteSession,
  fetchSessionDetail,
  fetchSessions,
  subscribeToAgentEvents,
  subscribeToSessionMessages,
  type SessionDetail,
  type SessionMessage,
  type SessionListItem,
  type SessionStatus,
} from './services/session.service';
import { fetchTrendingForecasts, type TrendingForecast } from './services/trending.service';
import type { AgentEvent, ChatMessage, Prediction, PredictionSession, SentimentDataPoint, TimelineEvent } from './types';

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

function mapSessionStatus(status: SessionStatus, confidence: number | null): 'stable' | 'volatile' {
  if (status === 'failed') {
    return 'volatile';
  }
  if (status === 'queued' || status === 'claimed' || status === 'running') {
    return 'volatile';
  }
  if (status === 'awaiting_clarification') {
    return 'volatile';
  }
  // confidence is a 0–1 float; treat < 0.4 as volatile
  if (confidence !== null && confidence !== 0 && confidence < 0.4) {
    return 'volatile';
  }
  return 'stable';
}
void mapSessionStatus;

function toSidebarSession(session: SessionListItem): PredictionSession {
  return {
    id: session.id,
    question: session.title ?? session.question,
    // Pass 0–1 float directly — display conversion happens in formatProbability
    probability: session.latestProbability,
    status: session.status,
    errorMessage: session.errorMessage,
    clarificationCandidates: session.clarificationCandidates,
    lastUpdated: new Date(session.lastActivityAt || session.updatedAt || session.createdAt),
  };
}

function toPrediction(detail: SessionDetail | null): Prediction | null {
  if (!detail) {
    return null;
  }

  // All values are stored as 0–1 floats from the backend
  const probability = detail.result?.finalProbability ?? detail.session.latestProbability ?? 0;
  const confidence = detail.result?.confidence ?? detail.session.latestConfidence ?? 0;
  const explanation =
    detail.result?.detailedExplanation ??
    detail.result?.bottomLineAnswer ??
    detail.result?.summaryMarkdown ??
    'Forecast is still being prepared.';

  return {
    id: detail.session.id,
    question: detail.session.question,
    probability,
    confidenceIndex: confidence,
    status: detail.session.status,
    explanation,
    marketProbability: detail.result?.marketProbability ?? null,
    errorMessage: detail.session.errorMessage,
    clarificationCandidates: detail.session.clarificationCandidates,
    keyFactors: detail.result?.keyFactors ?? [],
    whatIDidntFind: detail.result?.whatIDidntFind ?? [],
    reasoningChain: detail.result?.reasoningChain ?? [],
    suggestedActions: detail.result?.suggestedActions ?? [],
    generatedAt: detail.result?.generatedAt ? new Date(detail.result.generatedAt) : null,
    agentVersion: detail.result?.agentVersion ?? null,
    tier: detail.result?.tier ?? null,
    createdAt: new Date(detail.session.createdAt),
    updatedAt: new Date(detail.session.updatedAt),
  };
}

interface ActiveSessionState {
  id: string;
  question: string;
  status: SessionStatus;
  errorMessage: string | null;
  clarificationCandidates: ClarificationCandidate[] | null;
}

function toActiveSessionState(detail: SessionDetail | null): ActiveSessionState | null {
  if (!detail) {
    return null;
  }

  return {
    id: detail.session.id,
    question: detail.session.question,
    status: detail.session.status,
    errorMessage: detail.session.errorMessage,
    clarificationCandidates: detail.session.clarificationCandidates,
  };
}

function toSentimentPoints(detail: SessionDetail | null): SentimentDataPoint[] {
  if (!detail) {
    return [];
  }

  // expertSentiment and publicSentiment are stored as 0–1 floats
  return detail.sentimentTimeSeries.map((point) => ({
    date: point.date || new Date(point.ts).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
    expertSentiment: point.expertSentiment,
    expertUpper: point.expertUpper ?? undefined,
    expertLower: point.expertLower ?? undefined,
    publicSentiment: point.publicSentiment,
  }));
}

function toTimelineEvents(detail: SessionDetail | null): TimelineEvent[] {
  if (!detail) {
    return [];
  }

  const mapEvidenceSourceType = (evidence: SessionDetail['evidence'][number]): TimelineEvent['sourceType'] => {
    switch (evidence.sourceType) {
      case 'vault_news':
      case 'online_news':
        return 'news';
      case 'vault_telegram':
      case 'online_blog':
      case 'vault_hackernews':
        return 'social';
      case 'vault_arxiv':
        return 'expert';
      case 'vault_market':
      case 'vault_fred':
        return 'market';
      default:
        return evidence.type === 'market' ? 'market' : evidence.type;
    }
  };

  return detail.evidence.map((evidence) => ({
    id: evidence.id,
    evidenceId: evidence.evidenceId,
    date: new Date(evidence.publishedAt ?? evidence.createdAt).toLocaleDateString('en-US', {
      month: 'short',
      day: 'numeric',
    }),
    timestamp: evidence.publishedAt ? new Date(evidence.publishedAt) : new Date(evidence.createdAt),
    title: evidence.title,
    sourceType: mapEvidenceSourceType(evidence),
    source: evidence.source ?? evidence.sourceId,
    origin: evidence.origin,
    sourceDomain: evidence.sourceDomain,
    snippet: evidence.snippet,
    url: evidence.url,
    fetchedAt: evidence.fetchedAt ? new Date(evidence.fetchedAt) : null,
    relevanceScore: evidence.relevanceScore,
    credibilityTier: evidence.credibilityTier,
    recencyWeight: evidence.recencyWeight,
    usedInAnswer: evidence.usedInAnswer,
    impactOnForecast: evidence.impactOnForecast,
    justification: evidence.justification,
    rank: evidence.rank,
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

  return detail.messages.map(toChatMessage);
}

function toChatMessage(message: SessionMessage): ChatMessage {
  return {
    id: message.id,
    role: message.role === 'system' ? 'assistant' : message.role,
    content: message.content,
    timestamp: new Date(message.createdAt),
    status: message.status === 'failed' ? 'failed' : 'sent',
  };
}

function toTrendingView(items: TrendingForecast[]): TrendingQuestionView[] {
  return items.map((item) => {
    // probability from backend is a 0–1 float; default to 0.5 if missing
    const probability = item.probability ?? 0.5;

    // Trend thresholds in 0–1 space: >0.6 up, <0.4 down
    const trend: 'up' | 'down' | 'stable' =
      probability >= 0.6 ? 'up' : probability <= 0.4 ? 'down' : 'stable';

    return {
      id: item.id,
      question: item.question ?? item.title ?? 'Untitled forecast',
      probability,
      trend,
      context: `Popularity: ${item.popularityScore}`,
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
  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const [isAgentEventsLoading, setIsAgentEventsLoading] = useState(false);
  const [sessionMessages, setSessionMessages] = useState<ChatMessage[] | null>(null);
  const [pendingMessages, setPendingMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [isSendingMessage, setIsSendingMessage] = useState(false);

  const loadSession = useCallback(async (sessionId: string) => {
    const detail = await fetchSessionDetail(sessionId);
    setActiveSessionId(sessionId);
    setActiveSessionDetail(detail);
  }, []);

  useEffect(() => {
    if (!activeSessionId) {
      setAgentEvents([]);
      setIsAgentEventsLoading(false);
      return;
    }

    setIsAgentEventsLoading(true);

    const unsubscribe = subscribeToAgentEvents(activeSessionId, {
      onData: (events) => {
        setAgentEvents(events);
        setIsAgentEventsLoading(false);
      },
      onError: () => {
        setAgentEvents([]);
        setIsAgentEventsLoading(false);
      },
    });

    return () => {
      unsubscribe();
    };
  }, [activeSessionId]);

  useEffect(() => {
    if (!activeSessionId) {
      setSessionMessages(null);
      setPendingMessages([]);
      setIsMessagesLoading(false);
      return;
    }

    setSessionMessages(null);
    setPendingMessages([]);
    setIsMessagesLoading(true);

    const unsubscribe = subscribeToSessionMessages(activeSessionId, {
      onData: (messages) => {
        setSessionMessages(messages.map(toChatMessage));
        setIsMessagesLoading(false);
      },
      onError: () => {
        setSessionMessages(null);
        setIsMessagesLoading(false);
      },
    });

    return () => {
      unsubscribe();
    };
  }, [activeSessionId]);

  useEffect(() => {
    setIsHydratingAuth(false);
  }, []);

  const enterDemoDashboard = useCallback(async () => {
    setAuthError(null);
    setIsDashboardLoading(true);

    try {
      const [sessionsData, trendingData] = await Promise.all([
        fetchSessions(),
        fetchTrendingForecasts(20),
      ]);

      setUserProfile(getDemoUserProfile());
      setSessions(sessionsData);
      setTrending(trendingData);

      const nextSessionId = sessionsData[0]?.id ?? null;
      if (nextSessionId) {
        await loadSession(nextSessionId);
      } else {
        setActiveSessionId(null);
        setActiveSessionDetail(null);
      }

      setAppState('dashboard');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Could not open the dashboard.');
    } finally {
      setIsDashboardLoading(false);
    }
  }, [loadSession]);

  const handleGoToLogin = () => {
    if (userProfile) {
      setAppState('dashboard');
    } else {
      void enterDemoDashboard();
    }
  };

  const handleGoToSignup = () => {
    if (userProfile) {
      setAppState('dashboard');
    } else {
      setAppState('signup');
    }
  };

  const handleBackToLanding = () => {
    setAppState('landing');
  };

  const handleGoogleAuth = async () => {
    await enterDemoDashboard();
  };

  const handleEmailAuth = async (_email: string, password?: string) => {
    if (!password) {
      setAuthError('Password is required for email sign-in.');
      return;
    }
    await enterDemoDashboard();
  };

  const handleCreateAccount = async (payload: { name: string; email: string; password: string }) => {
    setUserProfile({ ...getDemoUserProfile(), displayName: payload.name, email: payload.email });
    setAppState('plan-selection');
  };

  const handleGoogleSignup = async () => {
    setUserProfile(getDemoUserProfile());
    setAppState('plan-selection');
  };

  const handleSelectPlan = async (plan: UserPlan) => {
    try {
      setAuthError(null);
      const updatedProfile = await updateUserPlan(plan);
      setUserProfile(updatedProfile);
      setAppState('dashboard');
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Could not update your plan.');
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
    setUserProfile(null);
    setSessions([]);
    setTrending([]);
    setActiveSessionId(null);
    setActiveSessionDetail(null);
    setAgentEvents([]);
    setIsAgentEventsLoading(false);
    setSessionMessages(null);
    setPendingMessages([]);
    setIsMessagesLoading(false);
    setIsSendingMessage(false);
    setAppState('landing');
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
      setAuthError(error instanceof Error ? error.message : 'Could not load this forecast.');
    } finally {
      setIsDashboardLoading(false);
    }
  };

  const handleCreateSession = async (question: string, idempotencyKey: string) => {
    try {
      setAuthError(null);
      setIsDashboardLoading(true);
      const created = await createSession({ question, idempotencyKey });
      const sessionsData = await fetchSessions();
      setSessions(sessionsData);
      await loadSession(created.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not create the forecast.';
      if (error instanceof ApiError && error.code === 'PLAN_LIMIT_EXCEEDED') {
        const details = error.details as { used?: number } | undefined;
        if (typeof details?.used === 'number') {
          const used = details.used;
          setUserProfile((current) => current ? { ...current, monthlyForecastsUsed: used } : current);
        }
      } else {
        setAuthError(message);
      }
      throw error instanceof Error ? error : new Error(message);
    } finally {
      setIsDashboardLoading(false);
    }
  };

  const handleSendMessage = async (message: string) => {
    const sessionId = activeSessionId;
    if (!sessionId) {
      setAuthError('Select a forecast before sending a follow-up.');
      return;
    }

    const content = message.trim();
    if (!content) {
      return;
    }

    const optimisticMessage: ChatMessage = {
      id: `pending-${Date.now()}`,
      role: 'user',
      content,
      timestamp: new Date(),
      status: 'pending',
    };

    try {
      setAuthError(null);
      setPendingMessages((current) => [...current, optimisticMessage]);
      setIsSendingMessage(true);

      const createdMessage = await addSessionMessage(sessionId, {
        role: 'user',
        content,
      });

      setActiveSessionDetail((current) => {
        if (!current || current.session.id !== sessionId) {
          return current;
        }

        const nextMessages = current.messages.some((existing) => existing.id === createdMessage.id)
          ? current.messages
          : [...current.messages, createdMessage];

        return {
          ...current,
          messages: nextMessages,
        };
      });
    } catch (error) {
      setPendingMessages((current) => current.filter((item) => item.id !== optimisticMessage.id));
      setAuthError(error instanceof Error ? error.message : 'Could not save your follow-up.');
    } finally {
      setIsSendingMessage(false);
    }
  };

  const handleClarifySession = async (sessionId: string, chosenCandidateId: string | null) => {
    try {
      setAuthError(null);
      setIsDashboardLoading(true);
      await clarifySession(sessionId, { chosenCandidateId });
      const sessionsData = await fetchSessions();
      setSessions(sessionsData);
      await loadSession(sessionId);
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Could not update the clarification choice.');
      throw error instanceof Error ? error : new Error('Could not update the clarification choice.');
    } finally {
      setIsDashboardLoading(false);
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
      setAuthError(error instanceof Error ? error.message : 'Could not delete the forecast.');
    } finally {
      setIsDashboardLoading(false);
    }
  };

  const sidebarSessions = useMemo(() => sessions.map(toSidebarSession), [sessions]);
  const activeSessionState = useMemo(() => toActiveSessionState(activeSessionDetail), [activeSessionDetail]);
  const prediction = useMemo(() => toPrediction(activeSessionDetail), [activeSessionDetail]);
  const sentimentData = useMemo(() => toSentimentPoints(activeSessionDetail), [activeSessionDetail]);
  const timelineEvents = useMemo(() => toTimelineEvents(activeSessionDetail), [activeSessionDetail]);
  const messages = useMemo(() => {
    const persistedMessages = sessionMessages ?? toChatMessages(activeSessionDetail);
    const unreconciledPending = pendingMessages.filter((pendingMessage) => {
      return !persistedMessages.some((message) => (
        message.role === pendingMessage.role &&
        message.content === pendingMessage.content &&
        Math.abs(message.timestamp.getTime() - pendingMessage.timestamp.getTime()) <= 120000
      ));
    });

    return [...persistedMessages, ...unreconciledPending].sort(
      (a, b) => a.timestamp.getTime() - b.timestamp.getTime()
    );
  }, [activeSessionDetail, pendingMessages, sessionMessages]);
  const isAwaitingAssistantResponse = useMemo(() => {
    let lastUserTimestamp: number | null = null;
    let lastAssistantTimestamp: number | null = null;

    for (const message of messages) {
      const timestamp = message.timestamp.getTime();
      if (message.role === 'user') {
        lastUserTimestamp = timestamp;
      } else if (message.role === 'assistant') {
        lastAssistantTimestamp = timestamp;
      }
    }

    return lastUserTimestamp !== null && (lastAssistantTimestamp === null || lastUserTimestamp > lastAssistantTimestamp);
  }, [messages]);
  const trendingItems = useMemo(() => toTrendingView(trending), [trending]);

  if (isHydratingAuth) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-slate-50 p-6">
        <div className="w-full max-w-sm">
          <StateMessage
            variant="loading"
            align="center"
            title="Preparing workspace"
            description="Checking your session and loading forecasts."
          />
        </div>
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
          <div className="fixed top-4 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow">
            <p className="font-semibold text-red-800">Action failed</p>
            <p className="mt-0.5">{authError}</p>
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
          <div className="fixed top-4 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow">
            <p className="font-semibold text-red-800">Action failed</p>
            <p className="mt-0.5">{authError}</p>
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
        <div className="fixed top-4 left-1/2 z-50 w-[calc(100vw-2rem)] max-w-md -translate-x-1/2 rounded-md border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700 shadow">
          <p className="font-semibold text-red-800">Action failed</p>
          <p className="mt-0.5">{authError}</p>
        </div>
      ) : null}
      <DashboardPage
        sessions={sidebarSessions}
        activeSessionId={activeSessionId}
        activeSessionState={activeSessionState}
        prediction={prediction}
        sentimentData={sentimentData}
        timelineEvents={timelineEvents}
        agentEvents={agentEvents}
        messages={messages}
        isMessagesLoading={isMessagesLoading}
        isSendingMessage={isSendingMessage}
        isAwaitingAssistantResponse={isAwaitingAssistantResponse}
        trendingForecasts={trendingItems}
        userDisplayName={userProfile?.displayName}
        userPlan={userProfile?.plan}
        userProfile={userProfile}
        onSessionSelect={(sessionId) => {
          void handleSessionSelect(sessionId);
        }}
        onCreateSession={handleCreateSession}
        onClarifySession={handleClarifySession}
        onSendMessage={handleSendMessage}
        onDeleteSession={handleDeleteSession}
        onLogout={handleLogout}
        onGoHome={handleBackToLanding}
        isLoading={isDashboardLoading}
        isAgentEventsLoading={isAgentEventsLoading}
        onPlanChange={setUserProfile}
      />
    </>
  );
}

export default App;
