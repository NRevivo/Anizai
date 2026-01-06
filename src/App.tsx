import { useState } from 'react';
import { Sidebar } from './components/Sidebar';
import { Dashboard } from './components/Dashboard';
import { ChatPanel } from './components/ChatPanel';
import {
  mockSessions,
  mockCurrentPrediction,
  mockSentimentData,
  mockTimelineEvents,
  mockSuggestedActions,
  mockChatMessages
} from './data/mockData';
import type { ChatMessage } from './types';

function App() {
  const [activeSessionId, setActiveSessionId] = useState('1');
  const [chatMessages, setChatMessages] = useState<ChatMessage[]>(mockChatMessages);

  const handleSessionSelect = (id: string) => {
    setActiveSessionId(id);
    // In a real app, this would fetch the prediction data for the selected session
  };

  const handleNewPrediction = () => {
    // In a real app, this would navigate to a new prediction form
    console.log('New prediction clicked');
  };

  const handleSendMessage = (message: string) => {
    const newMessage: ChatMessage = {
      id: Date.now().toString(),
      role: 'user',
      content: message,
      timestamp: new Date(),
    };
    setChatMessages([...chatMessages, newMessage]);

    // In a real app, this would send the message to the backend
    // and receive an AI response
  };

  const handleNewPredictionSubmit = (question: string) => {
    // In a real app, this would create a new prediction session
    // and trigger the AI analysis
    console.log('New prediction question:', question);
  };

  const handleActionClick = (actionId: string) => {
    // In a real app, this would trigger specific actions
    console.log('Action clicked:', actionId);
  };

  return (
    <div className="flex h-screen overflow-hidden">
      {/* Left Sidebar */}
      <Sidebar
        sessions={mockSessions}
        activeSessionId={activeSessionId}
        onSessionSelect={handleSessionSelect}
        onNewPrediction={handleNewPrediction}
      />

      {/* Main Dashboard */}
      <Dashboard
        prediction={mockCurrentPrediction}
        sentimentData={mockSentimentData}
        timelineEvents={mockTimelineEvents}
      />

      {/* Right Chat Panel */}
      <ChatPanel
        messages={chatMessages}
        suggestedActions={mockSuggestedActions}
        currentQuestion={mockCurrentPrediction.question}
        currentAnswer="The EU AI regulation has strong momentum. Recent committee votes show broad support, and the legislative timeline aligns with Q2 passage."
        onSendMessage={handleSendMessage}
        onNewPrediction={handleNewPredictionSubmit}
        onActionClick={handleActionClick}
      />
    </div>
  );
}

export default App;
