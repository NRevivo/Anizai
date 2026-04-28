import { useMemo, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Dashboard } from '../components/Dashboard';
import { ChatPanel } from '../components/ChatPanel';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { StateMessage } from '../components/ui/StateMessage';
import { CreateForecastView } from '../components/CreateForecastView';
import { TrendingContext } from '../components/CreateForecastContext';
import { SettingsModal } from '../components/SettingsModal';
import type { UserProfile } from '../services/user.service';
import type {
    ChatMessage,
    Prediction,
    PredictionSession,
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
    prediction: Prediction | null;
    sentimentData: SentimentDataPoint[];
    timelineEvents: TimelineEvent[];
    messages: ChatMessage[];
    trendingForecasts: TrendingQuestionView[];
    onSessionSelect: (sessionId: string) => void;
    onCreateSession: (question: string, idempotencyKey: string) => Promise<void>;
    onSendMessage: (message: string) => Promise<void>;
    onDeleteSession: (sessionId: string) => Promise<void>;
    userDisplayName?: string | null;
    userPlan?: 'free' | 'premium';
    onLogout?: () => void;
    onGoHome?: () => void;
    isLoading?: boolean;
    userProfile: UserProfile | null;
    onPlanChange?: (updated: UserProfile) => void;
}

export function DashboardPage({
    sessions,
    activeSessionId,
    prediction,
    sentimentData,
    timelineEvents,
    messages,
    trendingForecasts,
    onSessionSelect,
    onCreateSession,
    onSendMessage,
    onDeleteSession,
    userDisplayName,
    userPlan = 'free',
    onLogout,
    onGoHome,
    isLoading = false,
    userProfile,
    onPlanChange,
}: DashboardPageProps) {
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);
    const [isChatOpen, setIsChatOpen] = useState(false);
    const [isSettingsOpen, setIsSettingsOpen] = useState(false);
    const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
    const [sessionToDelete, setSessionToDelete] = useState<string | null>(null);
    const [isDeletingSession, setIsDeletingSession] = useState(false);
    const [isCreatingForecast, setIsCreatingForecast] = useState(false);
    const [currentView, setCurrentView] = useState<'dashboard' | 'new-forecast'>('dashboard');

    const suggestedActions = useMemo<SuggestedAction[]>(
        () => [
            { id: 'drivers', label: 'What drives uncertainty?' },
            { id: 'historical', label: 'Find similar events' },
            { id: 'track', label: 'How should I track this?' },
        ],
        []
    );

    const handleNewPrediction = () => {
        setCurrentView('new-forecast');
        setIsSidebarOpen(false);
        setIsChatOpen(false);
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

    const handleActionClick = (actionId: string) => {
        console.log('Action clicked:', actionId);
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

    const renderCenterPanel = () => {
        if (currentView === 'new-forecast') {
            return <CreateForecastView onSubmit={handleSubmitForecast} />;
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
                        onSettings={() => setIsSettingsOpen(true)}
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
                        onSettings={() => setIsSettingsOpen(true)}
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
                        onSettings={() => setIsSettingsOpen(true)}
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
                userProfile={userProfile}
                onLogout={onLogout}
                onPlanChange={onPlanChange}
            />
        </div>
    );
}
