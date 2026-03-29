import { useMemo, useState } from 'react';
import { Sidebar } from '../components/Sidebar';
import { Dashboard } from '../components/Dashboard';
import { ChatPanel } from '../components/ChatPanel';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { CreateForecastView } from '../components/CreateForecastView';
import { TrendingContext } from '../components/CreateForecastContext';
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
    onCreateSession: (question: string) => Promise<void>;
    onSendMessage: (message: string) => Promise<void>;
    onDeleteSession: (sessionId: string) => Promise<void>;
    userDisplayName?: string | null;
    userPlan?: 'free' | 'premium';
    onLogout?: () => void;
    onSettings?: () => void;
    isLoading?: boolean;
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
    onSettings,
    isLoading = false,
}: DashboardPageProps) {
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);
    const [isChatOpen, setIsChatOpen] = useState(false);
    const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
    const [sessionToDelete, setSessionToDelete] = useState<string | null>(null);
    const [isDeletingSession, setIsDeletingSession] = useState(false);
    const [currentView, setCurrentView] = useState<'dashboard' | 'new-forecast'>('dashboard');

    const suggestedActions = useMemo<SuggestedAction[]>(
        () => [
            { id: 'drivers', label: 'Uncertainty drivers' },
            { id: 'historical', label: 'Similar historical events' },
            { id: 'track', label: 'Track this forecast' },
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

    const handleSubmitForecast = async (question: string) => {
        await onCreateSession(question);
        setCurrentView('dashboard');
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
                <div className="h-full flex items-center justify-center text-gray-500">
                    Loading forecasts...
                </div>
            );
        }

        if (!prediction) {
            return (
                <div className="h-full flex items-center justify-center p-8">
                    <div className="max-w-md rounded-lg border border-dashed border-gray-300 bg-white p-6 text-center">
                        <h2 className="text-lg font-semibold text-gray-900">No forecasts yet</h2>
                        <p className="mt-2 text-sm text-gray-500">
                            Create your first forecast question to start a new session.
                        </p>
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
                        void handleSubmitForecast(question);
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
        <div className="w-screen h-screen overflow-hidden">
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

                    <div className="flex items-center gap-2">
                        <img src="/logo-brain.png" alt="Anizai" className="h-7" />
                        <span className="text-xl font-bold text-gray-900">Anizai</span>
                    </div>

                    <div className="w-10"></div>
                </div>
            </div>

            {currentView !== 'new-forecast' && (
                <button
                    onClick={() => setIsChatOpen(!isChatOpen)}
                    className={`xl:hidden fixed bottom-6 right-6 p-4 bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 rounded-full shadow-lg text-white z-50 transition-opacity ${isChatOpen ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
                >
                    <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                    </svg>
                </button>
            )}

            <div className="hidden xl:grid xl:grid-cols-[280px_minmax(0,1fr)_360px] h-full w-full">
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
                        onSettings={onSettings}
                    />
                </div>
                <div className="h-full overflow-hidden border-x border-gray-200 bg-slate-50 relative">
                    {renderCenterPanel()}
                </div>
                <div className="h-full overflow-hidden bg-white">
                    {renderRightPanel()}
                </div>
            </div>

            <div className="hidden lg:grid xl:hidden lg:grid-cols-[280px_minmax(0,1fr)] h-full w-full">
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
                        onSettings={onSettings}
                    />
                </div>
                <div className="h-full overflow-hidden relative flex flex-col">
                    <div className="flex-1 overflow-y-auto">
                        {renderCenterPanel()}
                        {currentView === 'new-forecast' && (
                            <div className="border-t border-gray-200">
                                <TrendingContext forecasts={trendingForecasts} onAnalyze={(q) => void handleSubmitForecast(q)} />
                            </div>
                        )}
                    </div>

                    <div className={`fixed inset-y-0 right-0 w-96 z-40 transform transition-transform duration-300 ease-in-out ${isChatOpen ? 'translate-x-0' : 'translate-x-full'}`}>
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

            <div className="lg:hidden h-full w-full flex flex-col">
                <div className="flex-1 overflow-y-auto pt-16">
                    {renderCenterPanel()}
                    {currentView === 'new-forecast' && (
                        <div className="border-t border-gray-200">
                            <TrendingContext forecasts={trendingForecasts} onAnalyze={(q) => void handleSubmitForecast(q)} />
                        </div>
                    )}
                </div>

                <div className={`fixed inset-y-0 left-0 w-80 z-40 transform transition-transform duration-300 ease-in-out ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
                    <Sidebar
                        activeSessionId={activeSessionId ?? ''}
                        sessions={sessions}
                        userDisplayName={userDisplayName}
                        userPlan={userPlan}
                        onSessionSelect={handleSessionSelect}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                        onLogout={onLogout}
                        onSettings={onSettings}
                    />
                </div>
                <div className={`fixed inset-y-0 right-0 w-96 z-40 transform transition-transform duration-300 ease-in-out ${isChatOpen ? 'translate-x-0' : 'translate-x-full'}`}>
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
                title="Delete Forecast"
                message="Are you sure you want to delete this forecast? This action cannot be undone."
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
        </div>
    );
}
