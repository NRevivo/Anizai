import { useCallback, useEffect, useMemo, useRef, useState } from 'react';

import { LandingPage } from './pages/LandingPage';
import { LoginPage } from './pages/LoginPage';
import { SignupPage } from './pages/SignupPage';
import { PlanSelection } from './pages/PlanSelection';
import { DashboardPage, type TrendingQuestionView } from './pages/DashboardPage';
import { ContactPage } from './pages/ContactPage';
import { FeaturesPage } from './pages/FeaturesPage';
import { MethodologyPage } from './pages/MethodologyPage';
import { AboutPage } from './pages/AboutPage';
import { TermsPage } from './pages/TermsPage';
import { PrivacyPage } from './pages/PrivacyPage';
import { CookiesPage } from './pages/CookiesPage';
import { StateMessage } from './components/ui/StateMessage';
import { ApiError } from './lib/api';
import { selectCurrentRunEvents } from './lib/agentEvents';
import { findPendingFollowUp } from './lib/followUpPending';
import {
  describeAuthError,
  signInWithEmail,
  signInWithGoogle,
  signOutUser,
  signUpWithEmail,
  subscribeToAuthState,
} from './services/auth.service';
import {
  fetchCurrentUser,
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
  retrySession,
  subscribeToAgentEvents,
  subscribeToSession,
  subscribeToSessionMessages,
  type SessionDetail,
  type SessionMessage,
  type SessionListItem,
  type SessionStatus,
} from './services/session.service';
import { fetchTrendingForecasts, type TrendingForecast } from './services/trending.service';
import type { AgentEvent, ChatMessage, MarketPricePoint, Prediction, PredictionSession, SentimentDataPoint, TimelineEvent } from './types';

type AppState =
  | 'landing'
  | 'login'
  | 'signup'
  | 'plan-selection'
  | 'dashboard'
  | 'contact'
  | 'features'
  | 'methodology'
  | 'about'
  | 'terms'
  | 'privacy'
  | 'cookies';

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
    bottomLineAnswer: detail.result?.bottomLineAnswer ?? null,
    detailedExplanation: detail.result?.detailedExplanation ?? null,
    summaryMarkdown: detail.result?.summaryMarkdown ?? null,
    confidenceLabel: detail.result?.confidenceLabel ?? null,
    consensusStrength: detail.result?.consensusStrength ?? null,
    marketProbability: detail.result?.marketProbability ?? null,
    marketComparisonInsight: detail.result?.marketComparisonInsight ?? null,
    sentimentAnalysisInsight: detail.result?.sentimentAnalysisInsight ?? null,
    evidenceFeedSummary: detail.result?.evidenceFeedSummary ?? null,
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
  errorCode: string | null;
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
    errorCode: detail.session.errorCode,
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

/**
 * `sessions/{id}/predictionSeries` → market price-history points.
 *
 * The subcollection is written on the Tier-1 (market-resolved) path only, so an
 * empty result is the ordinary outcome for a freeform forecast, not a failure.
 * "Absent" and "empty" are indistinguishable here — both reach us as `[]` — so
 * MarketPriceHistory branches on `tier` to word them differently.
 *
 * `confidence`, `reasonType` and `evidenceIds` are dropped on purpose: the
 * pipeline writes them as fixed constants (1.0 / "market" / []). See the comment
 * on MarketPricePoint.
 *
 * Two defensive steps over documents the BFF already orders by `ts`:
 *  - `ts` arrives as an ISO string the BFF derives from a Firestore Timestamp,
 *    falling back to '' when conversion fails (session.repository.ts:280). '' →
 *    NaN, and a NaN x-value drags the axis to the epoch and flattens the real
 *    range into a vertical line, so those points are dropped.
 *  - the sort re-establishes order for the BFF's missing-index fallback path.
 */
function toMarketPricePoints(detail: SessionDetail | null): MarketPricePoint[] {
  if (!detail) {
    return [];
  }

  return detail.predictionSeries
    .map((point) => ({
      t: new Date(point.ts).getTime(),
      probability: point.probability,
    }))
    .filter((point) => Number.isFinite(point.t) && Number.isFinite(point.probability))
    .sort((a, b) => a.t - b.t);
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
    impactOnForecast: evidence.impactOnForecast as TimelineEvent['impactOnForecast'],
    justification: evidence.justification,
    rank: evidence.rank,
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
    // Preserve 'answered' (hub flips the user message sent -> answered once
    // its reply lands) and 'failed'; everything else reads as 'sent'.
    status:
      message.status === 'failed'
        ? 'failed'
        : message.status === 'answered'
          ? 'answered'
          : 'sent',
  };
}

function toTrendingView(items: TrendingForecast[]): TrendingQuestionView[] {
  return items.map((item) => {
    return {
      id: item.id,
      // The event title — a display label. NOT what gets submitted: the picker
      // resolves a card to one market's real question first.
      question: item.title || 'Untitled forecast',
      probability: item.probability,
      outcomes: item.outcomes,
      markets: item.markets,
      volume24h: item.volume24h,
      marketCount: item.marketCount,
      mutuallyExclusive: item.mutuallyExclusive,
      url: item.url,
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
  // Live currentRunId for the active session, read straight off the session
  // doc snapshot (not the REST aggregate, which only refreshes on status
  // transitions). Rule B filters agentEvents against this.
  const [activeSessionCurrentRunId, setActiveSessionCurrentRunId] = useState<string | null>(null);
  const [isAgentEventsLoading, setIsAgentEventsLoading] = useState(false);
  const [sessionMessages, setSessionMessages] = useState<ChatMessage[] | null>(null);
  const [pendingMessages, setPendingMessages] = useState<ChatMessage[]>([]);
  const [isMessagesLoading, setIsMessagesLoading] = useState(false);
  const [isSendingMessage, setIsSendingMessage] = useState(false);

  // Mirrors the status of the currently loaded SessionDetail. The session
  // listener compares live Firestore snapshots against this to decide when
  // the BFF aggregate is stale and needs a re-fetch — kept in a ref so the
  // listener effect doesn't re-subscribe on every detail change.
  const loadedStatusRef = useRef<{ id: string; status: SessionStatus } | null>(null);

  const loadSession = useCallback(async (sessionId: string) => {
    const detail = await fetchSessionDetail(sessionId);
    setActiveSessionId(sessionId);
    setActiveSessionDetail(detail);

    // Fold the result back into the sidebar row.
    //
    // `sessions[].latestProbability` is NOT stored on the Firestore session doc —
    // the agent never writes it, so the doc holds null forever. Express derives it
    // from sessionResults (`enrichLatestProbability`), but only on GET /sessions,
    // which runs on dashboard entry. A forecast that finishes while the dashboard
    // is open therefore reached 'done' with the row still showing "—": the live
    // listener carries the doc's null, and this function used to refresh only
    // `activeSessionDetail`. Copying the freshly-loaded result across fixes the
    // row without a second list round-trip.
    const finalProbability = detail.result?.finalProbability ?? null;
    const confidence = detail.result?.confidence ?? null;
    if (finalProbability === null && confidence === null) {
      return;
    }
    setSessions((current) =>
      current.map((item) =>
        item.id === sessionId
          ? {
              ...item,
              latestProbability: finalProbability ?? item.latestProbability,
              latestConfidence: confidence ?? item.latestConfidence,
            }
          : item
      )
    );
  }, []);

  useEffect(() => {
    loadedStatusRef.current = activeSessionDetail
      ? { id: activeSessionDetail.session.id, status: activeSessionDetail.session.status }
      : null;
  }, [activeSessionDetail]);

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
      onError: (error) => {
        // An empty agentEvents collection is valid and does NOT surface here
        // (onSnapshot reports it as an empty snapshot). Reaching onError means
        // a real failure — e.g. a missing/incorrect Firestore read rule. Log
        // it so the regression is visible rather than silently empty.
        console.warn('[agentEvents] subscription error:', error);
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

  // Watch the active session document in real time. The agent worker flips
  // sessions/{id}.status as it progresses; without this the UI would sit on
  // the status captured at load time until a manual reload.
  useEffect(() => {
    if (!activeSessionId) {
      setActiveSessionCurrentRunId(null);
      return;
    }

    // Reset until the first snapshot for the newly-selected session arrives,
    // so a previous session's currentRunId never bleeds into the new one.
    setActiveSessionCurrentRunId(null);

    const unsubscribe = subscribeToSession(activeSessionId, {
      onData: (session) => {
        setActiveSessionCurrentRunId(session.currentRunId);

        // Keep the sidebar pill in sync with the live status.
        setSessions((current) =>
          current.map((item) =>
            item.id === session.id
              ? {
                  ...item,
                  status: session.status,
                  // The Firestore session doc does not carry latestProbability
                  // (the Express layer derives it from sessionResults), so a
                  // null here means "not in this snapshot" — keep the value
                  // already on the row rather than erasing it.
                  latestProbability: session.latestProbability ?? item.latestProbability,
                  latestConfidence: session.latestConfidence ?? item.latestConfidence,
                  errorCode: session.errorCode,
                  errorMessage: session.errorMessage,
                  clarificationCandidates: session.clarificationCandidates,
                }
              : item
          )
        );

        // When the live status diverges from the loaded SessionDetail,
        // re-pull the full aggregate from the BFF (the Firestore doc does
        // not carry result/evidence).
        const loaded = loadedStatusRef.current;
        if (loaded && loaded.id === session.id && loaded.status !== session.status) {
          void loadSession(session.id);
        }
      },
    });

    return () => {
      unsubscribe();
    };
  }, [activeSessionId, loadSession]);

  const enterDashboard = useCallback(async () => {
    setIsDashboardLoading(true);
    try {
      const [sessionsData, trendingData] = await Promise.all([
        fetchSessions(),
        fetchTrendingForecasts(20),
      ]);

      setSessions(sessionsData);
      setTrending(trendingData);

      const nextSessionId = sessionsData[0]?.id ?? null;
      if (nextSessionId) {
        await loadSession(nextSessionId);
      } else {
        setActiveSessionId(null);
        setActiveSessionDetail(null);
      }
    } catch (error) {
      setAuthError(error instanceof Error ? error.message : 'Could not open the dashboard.');
    } finally {
      setIsDashboardLoading(false);
    }
  }, [loadSession]);

  // Drive auth state from Firebase. When a user signs in, hydrate their
  // server-side profile and load dashboard data. When they sign out, drop
  // local state so a stale UI never lingers across accounts.
  useEffect(() => {
    const unsubscribe = subscribeToAuthState(async (user) => {
      if (!user) {
        setUserProfile(null);
        setSessions([]);
        setTrending([]);
        setActiveSessionId(null);
        setActiveSessionDetail(null);
        setIsHydratingAuth(false);
        return;
      }

      try {
        const profile = await fetchCurrentUser();
        setUserProfile(profile);
        // Hydrate the dashboard data in the background but do NOT auto-
        // navigate. The landing page is always the root entry — a
        // signed-in user lands there too and clicks "Open workspace" to
        // enter the dashboard. Explicit navigation lives in the login
        // handlers below for the post-sign-in case.
        await enterDashboard();
      } catch (error) {
        setAuthError(error instanceof Error ? error.message : 'Could not load your profile.');
      } finally {
        setIsHydratingAuth(false);
      }
    });

    return () => {
      unsubscribe();
    };
  }, [enterDashboard]);

  const handleGoToLogin = () => {
    if (userProfile) {
      setAppState('dashboard');
    } else {
      setAppState('login');
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
    setAuthError(null);
    try {
      await signInWithGoogle();
      // Explicit post-sign-in navigation. The auth subscriber hydrates the
      // profile in the background, but we navigate from here because the
      // subscriber no longer auto-redirects (landing is the root entry).
      setAppState('dashboard');
    } catch (error) {
      setAuthError(describeAuthError(error));
    }
  };

  const handleEmailAuth = async (email: string, password?: string) => {
    if (!password) {
      setAuthError('Password is required for email sign-in.');
      return;
    }
    setAuthError(null);
    try {
      await signInWithEmail(email, password);
      setAppState('dashboard');
    } catch (error) {
      setAuthError(describeAuthError(error));
    }
  };

  const handleCreateAccount = async (payload: { name: string; email: string; password: string }) => {
    setAuthError(null);
    try {
      await signUpWithEmail(payload);
      // onAuthStateChanged hydrates UserProfile from /me; then route to
      // plan selection so a brand-new account picks a tier before the
      // dashboard.
      setAppState('plan-selection');
    } catch (error) {
      setAuthError(describeAuthError(error));
    }
  };

  const handleGoogleSignup = async () => {
    setAuthError(null);
    try {
      await signInWithGoogle();
      setAppState('plan-selection');
    } catch (error) {
      setAuthError(describeAuthError(error));
    }
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
    about: () => setAppState('about'),
    terms: () => setAppState('terms'),
    privacy: () => setAppState('privacy'),
    cookies: () => setAppState('cookies'),
    pricing: () => setAppState('plan-selection'),
  };

  const handleLogout = async () => {
    setAuthError(null);
    try {
      await signOutUser();
    } catch (error) {
      setAuthError(describeAuthError(error));
    }
    // The auth subscriber clears userProfile/sessions/trending/active
    // session state on the user=null transition. Ephemeral UI state
    // that the subscriber doesn't own is cleared here.
    setAgentEvents([]);
    setActiveSessionCurrentRunId(null);
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
      // Usage was just charged server-side, so refresh the profile to keep the
      // forecast counter in sync. Previously it only updated on the PLAN_LIMIT
      // error, so the meter read a stale 0/3 until the limit was hit. Non-fatal:
      // a counter refresh must never break forecast creation.
      try {
        const profile = await fetchCurrentUser();
        setUserProfile(profile);
      } catch {
        // Leave the counter as-is; it will reconcile on the next /me read.
      }
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

  const handleRetrySession = async (sessionId: string) => {
    try {
      setAuthError(null);
      setIsDashboardLoading(true);

      const created = await retrySession(sessionId);

      // Drop the failed row immediately so the sidebar reflects the
      // replacement before the next fetch settles. The new session lands
      // via fetchSessions below.
      setSessions((current) => current.filter((item) => item.id !== sessionId));

      const sessionsData = await fetchSessions();
      setSessions(sessionsData);
      await loadSession(created.id);
    } catch (error) {
      const message = error instanceof Error ? error.message : 'Could not retry this forecast.';
      setAuthError(message);
      throw error instanceof Error ? error : new Error(message);
    } finally {
      setIsDashboardLoading(false);
    }
  };

  const sidebarSessions = useMemo(() => sessions.map(toSidebarSession), [sessions]);
  // Rule B: render only the events belonging to the session's live run
  // (runId === currentRunId), ordered by sequence. Extracted into a pure,
  // unit-tested helper (lib/agentEvents.ts) since the pipeline emits no events
  // yet — the helper is the one piece of Rule B provable without live data.
  const filteredAgentEvents = useMemo(
    () => selectCurrentRunEvents(agentEvents, activeSessionCurrentRunId),
    [agentEvents, activeSessionCurrentRunId]
  );
  const activeSessionState = useMemo(() => toActiveSessionState(activeSessionDetail), [activeSessionDetail]);
  const prediction = useMemo(() => toPrediction(activeSessionDetail), [activeSessionDetail]);
  // Authoritative "a forecast result exists" signal. `prediction` cannot stand
  // in for this: toPrediction returns a non-null Prediction for any non-null
  // detail, filling probability/confidence with 0 while the run is still in
  // flight. `detail.result` is null whenever sessionResults/{id} is absent
  // (server getSessionResult), independent of session status.
  const hasForecastResult = activeSessionDetail?.result != null;
  const sentimentData = useMemo(() => toSentimentPoints(activeSessionDetail), [activeSessionDetail]);
  const timelineEvents = useMemo(() => toTimelineEvents(activeSessionDetail), [activeSessionDetail]);
  const marketPricePoints = useMemo(() => toMarketPricePoints(activeSessionDetail), [activeSessionDetail]);
  const messages = useMemo(() => {
    const persistedMessages = sessionMessages ?? toChatMessages(activeSessionDetail);
    const unreconciledPending = pendingMessages.filter((pendingMessage) => {
      return !persistedMessages.some((message) => (
        message.role === pendingMessage.role &&
        message.content === pendingMessage.content &&
        Math.abs(message.timestamp.getTime() - pendingMessage.timestamp.getTime()) <= 120000
      ));
    });

    // Persisted messages all carry Firestore's clock, so sorting them against
    // each other is sound. Optimistic pending messages carry the *browser's*
    // clock (handleSendMessage stamps `new Date()`), which is a different clock
    // again — sorting the two together let a browser running even slightly
    // behind place a just-typed message above older history. They are the
    // newest messages by construction, so append them instead of sorting them in.
    const sortedPersisted = [...persistedMessages].sort(
      (a, b) => a.timestamp.getTime() - b.timestamp.getTime()
    );

    return [...sortedPersisted, ...unreconciledPending];
  }, [activeSessionDetail, pendingMessages, sessionMessages]);
  // The trailing user message still waiting on the hub, if any. Extracted into
  // lib/followUpPending.ts so the scan is unit-tested; the timestamp is exposed
  // alongside the boolean because the chat panel needs the message's age to
  // decide between an animated indicator and a stalled notice.
  const pendingFollowUp = useMemo(() => findPendingFollowUp(messages), [messages]);
  const isAwaitingAssistantResponse = pendingFollowUp !== null;
  const awaitingSinceMs = pendingFollowUp?.timestamp.getTime() ?? null;
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

  const shellProps = {
    onHome: handleBackToLanding,
    onSignIn: handleGoToLogin,
    onSignUp: handleGoToSignup,
    onContact: () => setAppState('contact'),
    onNavigation: navigationHandlers,
    isSignedIn: !!userProfile,
    onOpenWorkspace: () => setAppState('dashboard'),
    onSignOut: handleLogout,
  };

  if (appState === 'landing') {
    return <LandingPage {...shellProps} />;
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
          {...shellProps}
          onGoogleAuth={handleGoogleAuth}
          onEmailAuth={handleEmailAuth}
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
          {...shellProps}
          onCreateAccount={handleCreateAccount}
          onGoogleSignup={handleGoogleSignup}
        />
      </>
    );
  }

  if (appState === 'contact') {
    return <ContactPage {...shellProps} activeNav="contact" />;
  }

  if (appState === 'features') {
    return <FeaturesPage {...shellProps} activeNav="features" onGetStarted={handleGoToSignup} />;
  }

  if (appState === 'methodology') {
    return <MethodologyPage {...shellProps} activeNav="methodology" />;
  }

  if (appState === 'about') {
    return (
      <AboutPage
        {...shellProps}
        activeNav="about"
        onGetStarted={handleGoToSignup}
        onMethodology={() => setAppState('methodology')}
      />
    );
  }

  if (appState === 'terms') {
    return <TermsPage {...shellProps} />;
  }

  if (appState === 'privacy') {
    return <PrivacyPage {...shellProps} />;
  }

  if (appState === 'cookies') {
    return <CookiesPage {...shellProps} />;
  }

  if (appState === 'plan-selection') {
    return <PlanSelection {...shellProps} activeNav="pricing" onSelectPlan={handleSelectPlan} />;
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
        hasForecastResult={hasForecastResult}
        sentimentData={sentimentData}
        timelineEvents={timelineEvents}
        marketPricePoints={marketPricePoints}
        agentEvents={filteredAgentEvents}
        messages={messages}
        isMessagesLoading={isMessagesLoading}
        isSendingMessage={isSendingMessage}
        isAwaitingAssistantResponse={isAwaitingAssistantResponse}
        awaitingSinceMs={awaitingSinceMs}
        trendingForecasts={trendingItems}
        userDisplayName={userProfile?.displayName}
        userPlan={userProfile?.plan}
        userProfile={userProfile}
        onSessionSelect={(sessionId) => {
          void handleSessionSelect(sessionId);
        }}
        onCreateSession={handleCreateSession}
        onRetrySession={handleRetrySession}
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
