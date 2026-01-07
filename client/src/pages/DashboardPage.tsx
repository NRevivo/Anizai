import { Sidebar } from '../components/Sidebar';
import { Dashboard } from '../components/Dashboard';
import { ChatPanel } from '../components/ChatPanel';
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

    const handleNewPrediction = () => {
        console.log('New prediction clicked');
    };

    const handleSendMessage = (message: string) => {
        console.log('Send message:', message);
    };

    const handleActionClick = (actionId: string) => {
        console.log('Action clicked:', actionId);
    };

    const handleDeleteSession = (sessionId: string) => {
        console.log('Delete session:', sessionId);
        // TODO: Implement actual delete logic
    };

    return (
        <div className="w-screen h-screen overflow-hidden">
            {/* Mobile Menu Button - Only on small screens */}
            <button
                onClick={() => setIsSidebarOpen(!isSidebarOpen)}
                className={`lg:hidden fixed top-4 left-4 z-50 p-2 bg-white rounded-lg shadow-lg border border-gray-200 transition-opacity ${isChatOpen || isSidebarOpen ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
            >
                <svg className="w-6 h-6 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 6h16M4 12h16M4 18h16" />
                </svg>
            </button>

            {/* Close Sidebar Button - Shows when sidebar is open on mobile */}
            {isSidebarOpen && (
                <button
                    onClick={() => setIsSidebarOpen(false)}
                    className="lg:hidden fixed top-4 right-4 z-50 p-2 bg-white rounded-lg shadow-lg border border-gray-200"
                >
                    <svg className="w-6 h-6 text-gray-700" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                    </svg>
                </button>
            )}
            {/* Floating Chat Button - Shows only on non-desktop screens */}
            <button
                onClick={() => setIsChatOpen(!isChatOpen)}
                className={`xl:hidden fixed bottom-6 right-6 p-4 bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 rounded-full shadow-lg text-white z-50 transition-opacity ${isChatOpen ? 'opacity-0 pointer-events-none' : 'opacity-100'}`}
            >
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 12h.01M12 12h.01M16 12h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                </svg>
            </button>

            {/* Mobile Logo Header - Centered, only on small screens */}
            <div className="lg:hidden fixed top-4 left-1/2 -translate-x-1/2 z-40 flex items-center gap-2">
                <img src="/logo-brain.png" alt="Anizai" className="h-8" />
                <span className="text-xl font-bold text-gray-900">Anizai</span>
            </div>

            {/* Desktop 3-Column Grid */}
            <div className="hidden xl:grid xl:grid-cols-[280px_minmax(0,1fr)_360px] h-full w-full">
                <div className="h-full overflow-hidden">
                    <Sidebar
                        sessions={mockSessions}
                        activeSessionId={activeSessionId}
                        onSessionSelect={setActiveSessionId}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                    />
                </div>
                <div className="h-full overflow-hidden">
                    <Dashboard
                        prediction={mockCurrentPrediction}
                        sentimentData={mockSentimentData}
                        timelineEvents={mockTimelineEvents}
                    />
                </div>
                <div className="h-full overflow-hidden">
                    <ChatPanel
                        messages={messages}
                        suggestedActions={mockSuggestedActions}
                        currentQuestion={mockCurrentPrediction.question}
                        currentAnswer={mockCurrentPrediction.explanation}
                        onSendMessage={handleSendMessage}
                        onNewPrediction={handleNewPrediction}
                        onActionClick={handleActionClick}
                    />
                </div>
            </div>

            {/* Tablet 2-Column Grid */}
            <div className="hidden lg:grid xl:hidden lg:grid-cols-[280px_minmax(0,1fr)] h-full w-full">
                <div className="h-full overflow-hidden">
                    <Sidebar
                        sessions={mockSessions}
                        activeSessionId={activeSessionId}
                        onSessionSelect={setActiveSessionId}
                        onNewPrediction={handleNewPrediction}
                        onDeleteSession={handleDeleteSession}
                    />
                </div>
                <div className="h-full overflow-hidden relative">
                    <Dashboard
                        prediction={mockCurrentPrediction}
                        sentimentData={mockSentimentData}
                        timelineEvents={mockTimelineEvents}
                    />
                    <div className={`fixed inset-y-0 right-0 w-96 z-40 transform transition-transform duration-300 ease-in-out ${isChatOpen ? 'translate-x-0' : 'translate-x-full'}`}>
                        <ChatPanel
                            messages={messages}
                            suggestedActions={mockSuggestedActions}
                            currentQuestion={mockCurrentPrediction.question}
                            currentAnswer={mockCurrentPrediction.explanation}
                            onSendMessage={handleSendMessage}
                            onNewPrediction={handleNewPrediction}
                            onActionClick={handleActionClick}
                        />
                    </div>
                    {isChatOpen && (
                        <div className="fixed inset-0 bg-black bg-opacity-50 z-30" onClick={() => setIsChatOpen(false)} />
                    )}
                </div>
            </div>

            {/* Mobile Single Column */}
            <div className="lg:hidden h-full w-full">
                <Dashboard
                    prediction={mockCurrentPrediction}
                    sentimentData={mockSentimentData}
                    timelineEvents={mockTimelineEvents}
                />
                <div className={`fixed inset-y-0 left-0 w-80 z-40 transform transition-transform duration-300 ease-in-out ${isSidebarOpen ? 'translate-x-0' : '-translate-x-full'}`}>
                    <Sidebar
                        sessions={mockSessions}
                        activeSessionId={activeSessionId}
                        onSessionSelect={(id) => {
                            setActiveSessionId(id);
                            setIsSidebarOpen(false);
                        }}
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
                        onNewPrediction={handleNewPrediction}
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
        </div>
    );
}
