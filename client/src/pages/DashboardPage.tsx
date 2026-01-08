import { Sidebar } from '../components/Sidebar';
import { Dashboard } from '../components/Dashboard';
import { ChatPanel } from '../components/ChatPanel';
import { ConfirmDialog } from '../components/ui/ConfirmDialog';
import { CreateForecastView } from '../components/CreateForecastView';
import { TrendingContext } from '../components/CreateForecastContext';
import {
    mockSessions,
    mockCurrentPrediction,
    mockSentimentData,
    mockTimelineEvents,
    mockSuggestedActions,
    mockChatMessages
} from '../data/mockData';
import type { ChatMessage } from '../types';
import { useState } from 'react';

export function DashboardPage() {
    const [activeSessionId, setActiveSessionId] = useState('1');
    const [messages] = useState<ChatMessage[]>(mockChatMessages);
    const [isSidebarOpen, setIsSidebarOpen] = useState(false);
    const [isChatOpen, setIsChatOpen] = useState(false);
    const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false);
    const [sessionToDelete, setSessionToDelete] = useState<string | null>(null);
    const [currentView, setCurrentView] = useState<'dashboard' | 'new-forecast'>('dashboard');

    const handleNewPrediction = () => {
        setCurrentView('new-forecast');
        setIsSidebarOpen(false); // Close sidebar on mobile
        setIsChatOpen(false); // Ensure chat is closed
    };

    const handleSessionSelect = (sessionId: string) => {
        setActiveSessionId(sessionId);
        setCurrentView('dashboard');
        setIsSidebarOpen(false);
    };

    const handleSubmitForecast = (question: string) => {
        console.log('New forecast question:', question);
        // TODO: Create new forecast session with this question
        // For now, just return to dashboard
        setCurrentView('dashboard');
    };

    const handleSendMessage = (message: string) => {
        console.log('Send message:', message);
    };

    const handleActionClick = (actionId: string) => {
        console.log('Action clicked:', actionId);
    };

    const handleDeleteSession = (sessionId: string) => {
        setSessionToDelete(sessionId);
        setDeleteConfirmOpen(true);
    };

    const confirmDelete = () => {
        if (sessionToDelete) {
            console.log('Deleting session:', sessionToDelete);
            // TODO: Implement actual delete logic
            // e.g., remove from sessions array, update state, call API
        }
        setDeleteConfirmOpen(false);
        setSessionToDelete(null);
    };

    const cancelDelete = () => {
        setDeleteConfirmOpen(false);
        setSessionToDelete(null);
    };

    // Unified 3-column layout rendering logic
    const renderCenterPanel = () => {
        if (currentView === 'new-forecast') {
            return (
                <CreateForecastView
                    onSubmit={handleSubmitForecast}
                />
            );
        }
        return (
            <Dashboard
                prediction={mockCurrentPrediction}
                sentimentData={mockSentimentData}
                timelineEvents={mockTimelineEvents}
            />
        );
    };

    const renderRightPanel = () => {
        if (currentView === 'new-forecast') {
            return (
                <TrendingContext
                    onAnalyze={(question) => {
                        console.log('Analyze trend:', question);
                        // In a real app, this would pre-fill the input
                    }}
                />
            );
        }
        return (
            <ChatPanel
                messages={messages}
                onSendMessage={handleSendMessage}
                suggestedActions={mockSuggestedActions}
                onActionClick={handleActionClick}
                onNewPrediction={() => {
                    // This prop is required by ChatPanel but maybe not used or needed here?
                    // Just passing a dummy or the actual handler if relevant.
                    // ChatPanel definition has onNewPrediction: (question: string) => void;
                    // But handleNewPrediction in DashboardPage is () => void;
                    // Let's check ChatPanel usage.
                    // Actually ChatPanel line 14: onNewPrediction: (question: string) => void;
                    // It seems it might used to start a new prediction from chat?
                    // For now I will pass a function that logs or calls handleNewPrediction ignoring arg?
                    console.log("New prediction from chat");
                    handleNewPrediction();
                }}
            />
        );
    };

    // Show Dashboard
    return (
        <div className="w-screen h-screen overflow-hidden">
            {/* Mobile Sticky Header - Only on small screens */}
            <div className="lg:hidden fixed top-0 left-0 right-0 z-40 bg-white border-b border-gray-200 shadow-sm">
                <div className="flex items-center justify-between px-4 py-3">
                    {/* Hamburger Menu Button */}
                    <button
                        onClick={() => setIsSidebarOpen(!isSidebarOpen)}
                        className={`p-2 rounded-lg hover:bg-gray-100 transition-colors ${isChatOpen || isSidebarOpen ? 'opacity-50' : 'opacity-100'}`}
                    >
                        <svg className="w-6 h-6 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                        </svg>
                    </button>

                    {/* Logo */}
                    <div className="flex items-center gap-2">
                        <img src="/logo-brain.png" alt="Anizai" className="h-7" />
                        <span className="text-xl font-bold text-gray-900">Anizai</span>
                    </div>

                    {/* Placeholder for symmetry */}
                    <div className="w-10"></div>
                </div>
            </div>

            {/* Floating Chat Button - Shows only on non-desktop screens */}
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

            {/* Desktop 3-Column Grid */}
            <div className="hidden xl:grid xl:grid-cols-[280px_minmax(0,1fr)_360px] h-full w-full">
                <div className="h-full overflow-hidden">
                    <Sidebar
                        activeSessionId={activeSessionId}
                        sessions={mockSessions}
                        onSessionSelect={handleSessionSelect}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                    />
                </div>
                <div className="h-full overflow-hidden border-x border-gray-200 bg-slate-50 relative">
                    {renderCenterPanel()}
                </div>
                <div className="h-full overflow-hidden bg-white">
                    {renderRightPanel()}
                </div>
            </div>

            {/* Tablet 2-Column Grid */}
            <div className="hidden lg:grid xl:hidden lg:grid-cols-[280px_minmax(0,1fr)] h-full w-full">
                <div className="h-full overflow-hidden">
                    <Sidebar
                        activeSessionId={activeSessionId}
                        sessions={mockSessions}
                        onSessionSelect={handleSessionSelect}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                    />
                </div>
                <div className="h-full overflow-hidden relative flex flex-col">
                    <div className="flex-1 overflow-y-auto">
                        {renderCenterPanel()}
                        {currentView === 'new-forecast' && (
                            <div className="border-t border-gray-200">
                                <TrendingContext onAnalyze={(q) => console.log(q)} />
                            </div>
                        )}
                    </div>

                    {/* Floating Chat Panel for Tablet */}
                    <div className={`fixed inset-y-0 right-0 w-96 z-40 transform transition-transform duration-300 ease-in-out ${isChatOpen ? 'translate-x-0' : 'translate-x-full'}`}>
                        <ChatPanel
                            messages={messages}
                            suggestedActions={mockSuggestedActions}
                            currentQuestion={mockCurrentPrediction.question}
                            currentAnswer={mockCurrentPrediction.explanation}
                            onSendMessage={handleSendMessage}
                            onNewPrediction={() => {
                                console.log("New prediction from chat");
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

            {/* Mobile Single Column */}
            <div className="lg:hidden h-full w-full flex flex-col">
                <div className="flex-1 overflow-y-auto pt-16">
                    {renderCenterPanel()}
                    {currentView === 'new-forecast' && (
                        <div className="border-t border-gray-200">
                            <TrendingContext onAnalyze={(q) => console.log(q)} />
                        </div>
                    )}
                </div>

                <div className={`fixed inset-y-0 left-0 w-80 z-40 transform transition-transform duration-300 ease-in-out ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
                    <Sidebar
                        activeSessionId={activeSessionId}
                        sessions={mockSessions}
                        onSessionSelect={handleSessionSelect}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                    />
                </div>
                <div className={`fixed inset-y-0 right-0 w-96 z-40 transform transition-transform duration-300 ease-in-out ${isChatOpen ? 'translate-x-0' : 'translate-x-full'}`}>
                    <ChatPanel
                        messages={messages}
                        suggestedActions={mockSuggestedActions}
                        currentQuestion={mockCurrentPrediction.question}
                        currentAnswer={mockCurrentPrediction.explanation}
                        onSendMessage={handleSendMessage}
                        onNewPrediction={() => {
                            console.log("New prediction from chat");
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

            {/* Delete Confirmation Dialog */}
            <ConfirmDialog
                isOpen={deleteConfirmOpen}
                title="Delete Forecast"
                message="Are you sure you want to delete this forecast? This action cannot be undone."
                confirmText="Delete"
                cancelText="Cancel"
                onConfirm={confirmDelete}
                onCancel={cancelDelete}
            />
        </div>
    );
}
