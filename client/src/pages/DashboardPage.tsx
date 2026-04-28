import { useEffect, useMemo, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Dashboard } from '../components/Dashboard';
import { ChatPanel } from '../components/ChatPanel';
import { AgentEventsTimeline } from '../components/cards/AgentEventsTimeline';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { StateMessage } from '../components/ui/StateMessage';
import { CreateForecastView } from '../components/CreateForecastView';
import { TrendingContext } from '../components/CreateForecastContext';
import { SettingsModal, type SettingsSection } from '../components/SettingsModal';
import type { UserProfile } from '../services/user.service';
import type {
    ChatMessage,
    ClarificationCandidate,
    AgentEvent,
    Prediction,
    PredictionSession,
    SessionStatus,
    SentimentDataPoint,
    SuggestedAction,
    TimelineEvent,
} from '../types';

interface TrendingQuestionView {
    id: string;
    question: string;
    probability: number;
    trend: 'up' | 'down' | 'stable';
    context: string;
}

interface DashboardPageProps {
    sessions: PredictionSession[];
    activeSessionId: string | null;
    activeSessionState: {
        id: string;
        question: string;
        status: SessionStatus;
        errorMessage: string | null;
        clarificationCandidates: ClarificationCandidate[] | null;
    } | null;
    prediction: Prediction | null;
    sentimentData: SentimentDataPoint[];
    timelineEvents: TimelineEvent[];
    agentEvents: AgentEvent[];
    messages: ChatMessage[];
    trendingForecasts: TrendingQuestionView[];
    onSessionSelect: (sessionId: string) => void;
    onCreateSession: (question: string, idempotencyKey: string) => Promise<void>;
    onClarifySession: (sessionId: string, chosenCandidateId: string | null) => Promise<void>;
    onSendMessage: (message: string) => Promise<void>;
    onDeleteSession: (sessionId: string) => Promise<void>;
    userDisplayName?: string | null;
    userPlan?: 'free' | 'premium';
    onLogout?: () => void;
    onGoHome?: () => void;
    isLoading?: boolean;
    isAgentEventsLoading?: boolean;
    userProfile: UserProfile | null;
    onPlanChange?: (updated: UserProfile) => void;
}

export function DashboardPage({
    sessions,
    activeSessionId,
    activeSessionState,
    prediction,
    sentimentData,
    timelineEvents,
    agentEvents,
    messages,
    trendingForecasts,
    onSessionSelect,
    onCreateSession,
    onClarifySession,
    onSendMessage,
    onDeleteSession,
    userDisplayName,
    userPlan = 'free',
    onLogout,
    onGoHome,
    isLoading = false,
    isAgentEventsLoading = false,
    userProfile,
    onPlanChange,
}: DashboardPageProps) {
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);
    const [isChatOpen, setIsChatOpen] = useState(false);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [settingsSection, setSettingsSection] = useState<SettingsSection>('profile');
    const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
    const [sessionToDelete, setSessionToDelete] = useState<string | null>(null);
    const [isDeletingSession, setIsDeletingSession] = useState(false);
    const [isCreatingForecast, setIsCreatingForecast] = useState(false);
    const [selectedClarificationId, setSelectedClarificationId] = useState<string>('none');
    const [isSubmittingClarification, setIsSubmittingClarification] = useState(false);
    const [clarificationError, setClarificationError] = useState<string | null>(null);
    const [currentView, setCurrentView] = useState<'dashboard' | 'new-forecast'>('dashboard');

    const suggestedActions = useMemo<SuggestedAction[]>(
        () => (prediction?.suggestedActions ?? []).slice(0, 3),
        [prediction]
    );

    useEffect(() => {
        setSelectedClarificationId('none');
        setClarificationError(null);
        setIsSubmittingClarification(false);
    }, [activeSessionState?.id, activeSessionState?.status]);

    const handleNewPrediction = () => {
        setCurrentView('new-forecast');
        setIsSidebarOpen(false);
        setIsChatOpen(false);
    };

    const openSettingsSection = (section: SettingsSection) => {
        setSettingsSection(section);
        setIsSettingsOpen(true);
    };

    const handleSessionSelect = (sessionId: string) => {
        onSessionSelect(sessionId);
        setCurrentView('dashboard');
        setIsSidebarOpen(false);
    };

    const handleSubmitForecast = async (question: string, idempotencyKey: string) => {
        if (isCreatingForecast) {
            return;
        }

        try {
            setIsCreatingForecast(true);
            await onCreateSession(question, idempotencyKey);
            setCurrentView('dashboard');
        } finally {
            setIsCreatingForecast(false);
        }
    };

    const handleSendMessage = (message: string) => {
        void onSendMessage(message);
    };

    const handleClarificationSubmit = async () => {
        if (!activeSessionState || activeSessionState.status !== 'awaiting_clarification' || isSubmittingClarification) {
            return;
        }

        try {
            setClarificationError(null);
            setIsSubmittingClarification(true);
            await onClarifySession(
                activeSessionState.id,
                selectedClarificationId === 'none' ? null : selectedClarificationId
            );
        } catch (error) {
            setClarificationError(error instanceof Error ? error.message : 'Could not submit the clarification choice.');
        } finally {
            setIsSubmittingClarification(false);
        }
    };

    const handleActionClick = (action: SuggestedAction) => {
        if (!action.prompt) {
            return;
        }

        void onSendMessage(action.prompt);
    };

    const handleDeleteSession = (sessionId: string) => {
        setSessionToDelete(sessionId);
        setDeleteConfirmOpen(true);
    };

    const confirmDelete = async () => {
        if (!sessionToDelete) {
            return;
        }

        try {
            setIsDeletingSession(true);
            await onDeleteSession(sessionToDelete);
            setDeleteConfirmOpen(false);
            setSessionToDelete(null);
        } finally {
            setIsDeletingSession(false);
        }
    };

    const cancelDelete = () => {
        setDeleteConfirmOpen(false);
        setSessionToDelete(null);
    };

    const renderStatusPanel = () => {
        if (!activeSessionState) {
            return null;
        }

        if (activeSessionState.status === 'queued') {
            return (
                <StateMessage
                    variant="loading"
                    align="center"
                    title="Forecast queued"
                    description="Your request was accepted and is waiting to be picked up for analysis."
                />
            );
        }

        if (activeSessionState.status === 'claimed') {
            return (
                <StateMessage
                    variant="loading"
                    align="center"
                    title="Analysis is starting"
                    description="The forecasting pipeline has claimed this session and is preparing the first pass."
                />
            );
        }

        if (activeSessionState.status === 'running') {
            return (
                <StateMessage
                    variant="loading"
                    align="center"
                    title="Analysis in progress"
                    description="We are gathering evidence and updating the forecast now."
                />
            );
        }

        if (activeSessionState.status === 'failed') {
            return (
                <StateMessage
                    variant="error"
                    align="center"
                    title="Forecast could not be completed"
                    description={activeSessionState.errorMessage ?? 'This session ended with an error before a final result was produced.'}
                />
            );
        }

        if (activeSessionState.status === 'awaiting_clarification') {
            const candidates = [...(activeSessionState.clarificationCandidates ?? [])]
                .sort((a, b) => b.matchConfidence - a.matchConfidence);

            return (
                <div className="rounded-lg border border-violet-200 bg-white p-4 sm:p-5 shadow-sm space-y-4">
                    <div className="space-y-2">
                        <p className="text-[11px] font-semibold uppercase tracking-wider text-violet-600">Clarification needed</p>
                        <h2 className="text-lg font-semibold text-gray-900">Which market or intent did you mean?</h2>
                        <p className="text-sm text-gray-600">
                            Choose the closest match so we can queue the right forecast. If none fit, we can re-queue the request without a market selection.
                        </p>
                    </div>

                    <div className="space-y-3">
                        {candidates.map((candidate) => (
                            <label
                                key={candidate.id}
                                className={`flex gap-3 rounded-lg border p-3 sm:p-4 cursor-pointer transition-colors ${
                                    selectedClarificationId === candidate.id
                                        ? 'border-violet-300 bg-violet-50'
                                        : 'border-gray-200 hover:border-violet-200 hover:bg-gray-50'
                                }`}
                            >
                                <input
                                    type="radio"
                                    name="clarification-candidate"
                                    value={candidate.id}
                                    checked={selectedClarificationId === candidate.id}
                                    onChange={() => setSelectedClarificationId(candidate.id)}
                                    className="mt-1 h-4 w-4 text-violet-600"
                                />
                                <div className="min-w-0 space-y-1">
                                    <div className="flex flex-wrap items-center gap-2">
                                        <p className="text-sm font-semibold text-gray-900 break-words">{candidate.label}</p>
                                        <span className="inline-flex items-center rounded-full bg-gray-100 px-2 py-0.5 text-[11px] font-medium text-gray-600">
                                            {candidate.source}
                                        </span>
                                        <span className="inline-flex items-center rounded-full bg-violet-100 px-2 py-0.5 text-[11px] font-medium text-violet-700">
                                            {Math.round(candidate.matchConfidence * 100)}% match
                                        </span>
                                    </div>
                                    <p className="text-sm text-gray-600 break-words">{candidate.description}</p>
                                </div>
                            </label>
                        ))}

                        <label
                            className={`flex gap-3 rounded-lg border p-3 sm:p-4 cursor-pointer transition-colors ${
                                selectedClarificationId === 'none'
                                    ? 'border-violet-300 bg-violet-50'
                                    : 'border-gray-200 hover:border-violet-200 hover:bg-gray-50'
                            }`}
                        >
                            <input
                                type="radio"
                                name="clarification-candidate"
                                value="none"
                                checked={selectedClarificationId === 'none'}
                                onChange={() => setSelectedClarificationId('none')}
                                className="mt-1 h-4 w-4 text-violet-600"
                            />
                            <div className="min-w-0 space-y-1">
                                <p className="text-sm font-semibold text-gray-900">None of these</p>
                                <p className="text-sm text-gray-600">
                                    Queue the forecast again without selecting one of the suggested markets.
                                </p>
                            </div>
                        </label>
                    </div>

                    {clarificationError ? (
                        <StateMessage
                            compact
                            variant="error"
                            title="Clarification was not submitted"
                            description={clarificationError}
                        />
                    ) : null}

                    <div className="flex flex-col sm:flex-row sm:items-center gap-3">
                        <button
                            type="button"
                            onClick={() => void handleClarificationSubmit()}
                            disabled={isSubmittingClarification}
                            className="inline-flex min-h-11 items-center justify-center rounded-md bg-gray-900 px-4 py-2 text-sm font-semibold text-white hover:bg-gray-800 disabled:opacity-60"
                        >
                            {isSubmittingClarification ? 'Submitting clarification...' : 'Submit clarification'}
                        </button>
                        <p className="text-xs text-gray-500">
                            The session will return to the queue after you confirm a choice.
                        </p>
                    </div>
                </div>
            );
        }

        return null;
    };

    const renderCenterPanel = () => {
        if (currentView === 'new-forecast') {
            return <CreateForecastView onSubmit={handleSubmitForecast} onOpenSubscription={() => openSettingsSection('subscription')} />;
        }

        if (isLoading) {
            return (
                <div className="h-full flex items-center justify-center p-4 sm:p-6 overflow-x-hidden">
                    <div className="w-full max-w-sm">
                        <StateMessage
                            variant="loading"
                            align="center"
                            title="Loading workspace"
                            description="Refreshing forecasts, evidence, and follow-ups."
                        />
                    </div>
                </div>
            );
        }

        if (activeSessionState && activeSessionState.status !== 'done') {
            const statusPanel = renderStatusPanel();

            return (
                <div className="h-full flex items-center justify-center p-4 sm:p-8 overflow-x-hidden">
                    <div className="w-full max-w-2xl space-y-4">
                        <div className="rounded-lg border border-gray-200 bg-white p-4 sm:p-5 shadow-sm">
                            <p className="text-[11px] font-semibold uppercase tracking-wider text-gray-500">Active forecast</p>
                            <h1 className="mt-2 text-lg sm:text-xl font-semibold text-gray-900 break-words">
                                {activeSessionState.question}
                            </h1>
                        </div>
                        {statusPanel}
                        <AgentEventsTimeline events={agentEvents} isLoading={isAgentEventsLoading} />
                    </div>
                </div>
            );
        }

        if (!prediction) {
            return (
                <div className="h-full flex items-center justify-center p-4 sm:p-8 overflow-x-hidden">
                    <div className="w-full max-w-md">
                        <StateMessage
                            align="center"
                            title={sessions.length === 0 ? 'No forecasts yet' : 'Select a forecast'}
                            description={sessions.length === 0
                                ? 'Create a forecast to see probability, confidence, and evidence.'
                                : 'Choose a forecast from the sidebar to view its result and evidence.'}
                            action={sessions.length === 0 ? (
                                <button
                                    type="button"
                                    onClick={handleNewPrediction}
                                    className="inline-flex min-h-10 items-center justify-center rounded-md bg-gray-900 px-4 text-sm font-semibold text-white hover:bg-gray-800"
                                >
                                    Create forecast
                                </button>
                            ) : null}
                        />
                    </div>
                </div>
            );
        }

        return (
            <Dashboard
                prediction={prediction}
                sentimentData={sentimentData}
                timelineEvents={timelineEvents}
                agentEvents={agentEvents}
                isAgentEventsLoading={isAgentEventsLoading}
            />
        );
    };

    const renderRightPanel = () => {
        if (currentView === 'new-forecast') {
            return (
                <TrendingContext
                    forecasts={trendingForecasts}
                    onAnalyze={(question) => {
                        void handleSubmitForecast(question, crypto.randomUUID()).catch(() => undefined);
                    }}
                />
            );
        }

        return (
            <ChatPanel
                messages={messages}
                onSendMessage={handleSendMessage}
                suggestedActions={suggestedActions}
                currentQuestion={prediction?.question}
                currentAnswer={prediction?.explanation}
                onNewPrediction={() => {
                    handleNewPrediction();
                }}
                onActionClick={handleActionClick}
            />
        );
    };

    return (
        <div className="w-full h-screen max-w-full overflow-hidden bg-slate-50">
            <div className="lg:hidden fixed top-0 left-0 right-0 z-40 bg-white border-b border-gray-200 shadow-sm">
                <div className="flex items-center justify-between px-4 py-3">
                    <button
                        onClick={() => setIsSidebarOpen(!isSidebarOpen)}
                        className={`p-2 rounded-lg hover:bg-gray-100 transition-colors ${isChatOpen || isSidebarOpen ? 'opacity-50' : 'opacity-100'}`}
                    >
                        <svg className="w-6 h-6 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                        </svg>
                    </button>

                    <button onClick={onGoHome} className="flex items-center gap-2 hover:opacity-80 transition-opacity focus:outline-none">
                        <img src="/logo-brain.png" alt="Anizai" className="h-7" />
                        <span className="text-xl font-bold text-gray-900">Anizai</span>
                    </button>

                    <div className="w-10"></div>
                </div>
            </div>

            {currentView !== 'new-forecast' && (
                <button
                    onClick={() => setIsChatOpen(!isChatOpen)}
                    className={`xl:hidden fixed bottom-4 right-4 sm:bottom-5 sm:right-5 p-3.5 bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 rounded-full shadow-lg text-white z-50 transition-opacity ${isChatOpen ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
                >
                    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                    </svg>
                </button>
            )}

            <div className="hidden xl:grid xl:grid-cols-[252px_minmax(0,1fr)_304px] 2xl:grid-cols-[272px_minmax(0,1fr)_340px] h-full w-full min-w-0">
                <div className="h-full overflow-hidden">
                    <Sidebar
                        activeSessionId={activeSessionId ?? ''}
                        sessions={sessions}
                        userDisplayName={userDisplayName}
                        userPlan={userPlan}
                        onSessionSelect={handleSessionSelect}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                        onLogout={onLogout}
                        onSettings={() => openSettingsSection('profile')}
                        onGoHome={onGoHome}
                    />
                </div>
                <div className="h-full min-w-0 overflow-hidden border-x border-gray-200 bg-slate-50 relative">
                    {renderCenterPanel()}
                </div>
                <div className="h-full min-w-0 overflow-hidden bg-white">
                    {renderRightPanel()}
                </div>
            </div>

            <div className="hidden lg:grid xl:hidden lg:grid-cols-[264px_minmax(0,1fr)] h-full w-full min-w-0">
                <div className="h-full overflow-hidden">
                    <Sidebar
                        activeSessionId={activeSessionId ?? ''}
                        sessions={sessions}
                        userDisplayName={userDisplayName}
                        userPlan={userPlan}
                        onSessionSelect={handleSessionSelect}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                        onLogout={onLogout}
                        onSettings={() => openSettingsSection('profile')}
                        onGoHome={onGoHome}
                    />
                </div>
                <div className="h-full min-w-0 overflow-hidden relative flex flex-col bg-slate-50">
                    <div className="flex-1 overflow-y-auto">
                        {renderCenterPanel()}
                        {currentView === 'new-forecast' && (
                            <div className="border-t border-gray-200">
                                <TrendingContext forecasts={trendingForecasts} onAnalyze={(q) => void handleSubmitForecast(q, crypto.randomUUID()).catch(() => undefined)} />
                            </div>
                        )}
                    </div>

                    <div className={`fixed inset-y-0 right-0 w-full max-w-[min(24rem,100vw)] z-40 transform transition-transform duration-300 ease-in-out ${isChatOpen ? 'translate-x-0' : 'translate-x-full'}`}>
                        <ChatPanel
                            messages={messages}
                            suggestedActions={suggestedActions}
                            currentQuestion={prediction?.question}
                            currentAnswer={prediction?.explanation}
                            onSendMessage={handleSendMessage}
                            onNewPrediction={() => {
                                handleNewPrediction();
                            }}
                            onActionClick={handleActionClick}
                        />
                    </div>
                    {isChatOpen && (
                        <div className="fixed inset-0 bg-black bg-opacity-50 z-30" onClick={() => setIsChatOpen(false)} />
                    )}
                </div>
            </div>

            <div className="lg:hidden h-full w-full max-w-full flex flex-col overflow-x-hidden">
                <div className="flex-1 overflow-y-auto pt-16">
                    {renderCenterPanel()}
                    {currentView === 'new-forecast' && (
                        <div className="border-t border-gray-200">
                            <TrendingContext forecasts={trendingForecasts} onAnalyze={(q) => void handleSubmitForecast(q, crypto.randomUUID()).catch(() => undefined)} />
                        </div>
                    )}
                </div>

                <div className={`fixed inset-y-0 left-0 w-[min(20rem,calc(100vw-1rem))] z-40 transform transition-transform duration-300 ease-in-out ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
                    <Sidebar
                        activeSessionId={activeSessionId ?? ''}
                        sessions={sessions}
                        userDisplayName={userDisplayName}
                        userPlan={userPlan}
                        onSessionSelect={handleSessionSelect}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                        onLogout={onLogout}
                        onSettings={() => openSettingsSection('profile')}
                        onGoHome={onGoHome}
                    />
                </div>
                <div className={`fixed inset-y-0 right-0 w-full max-w-[min(24rem,100vw)] z-40 transform transition-transform duration-300 ease-in-out ${isChatOpen ? 'translate-x-0' : 'translate-x-full'}`}>
                    <ChatPanel
                        messages={messages}
                        suggestedActions={suggestedActions}
                        currentQuestion={prediction?.question}
                        currentAnswer={prediction?.explanation}
                        onSendMessage={handleSendMessage}
                        onNewPrediction={() => {
                            handleNewPrediction();
                        }}
                        onActionClick={handleActionClick}
                    />
                </div>
                {isSidebarOpen && (
                    <div className="fixed inset-0 bg-black bg-opacity-50 z-30" onClick={() => setIsSidebarOpen(false)} />
                )}
                {isChatOpen && (
                    <div className="fixed inset-0 bg-black bg-opacity-50 z-30" onClick={() => setIsChatOpen(false)} />
                )}
            </div>

            <ConfirmDialog
                isOpen={deleteConfirmOpen}
                title="Delete forecast"
                message="Delete this forecast? This cannot be undone."
                confirmText="Delete"
                cancelText="Cancel"
                onConfirm={() => {
                    if (isDeletingSession) {
                        return;
                    }
                    void confirmDelete();
                }}
                onCancel={() => {
                    if (isDeletingSession) {
                        return;
                    }
                    cancelDelete();
                }}
            />

            <SettingsModal 
                isOpen={isSettingsOpen} 
                onClose={() => setIsSettingsOpen(false)} 
                initialSection={settingsSection}
                userProfile={userProfile}
                onLogout={onLogout}
                onPlanChange={onPlanChange}
            />
        </div>
    );
}
