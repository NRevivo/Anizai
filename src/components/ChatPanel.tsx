import { useState } from 'react';
import type { ChatMessage, SuggestedAction } from '../types';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { formatRelativeTime } from '../lib/utils';
import ReactMarkdown from 'react-markdown';

interface ChatPanelProps {
    messages: ChatMessage[];
    suggestedActions: SuggestedAction[];
    currentQuestion?: string;
    currentAnswer?: string;
    onSendMessage: (message: string) => void;
    onNewPrediction: (question: string) => void;
    onActionClick: (actionId: string) => void;
}

export function ChatPanel({
    messages,
    suggestedActions,
    currentQuestion,
    currentAnswer,
    onSendMessage,
    onNewPrediction,
    onActionClick
}: ChatPanelProps) {
    const [inputValue, setInputValue] = useState('');
    const [predictionInput, setPredictionInput] = useState('');

    const handleSend = () => {
        if (inputValue.trim()) {
            onSendMessage(inputValue);
            setInputValue('');
        }
    };

    const handleNewPrediction = () => {
        if (predictionInput.trim()) {
            onNewPrediction(predictionInput);
            setPredictionInput('');
        }
    };

    return (
        <div className="w-96 bg-white border-l border-gray-200 flex flex-col h-screen">
            {/* New Prediction Input - Prominent at top */}
            <div className="p-6 border-b border-gray-100 bg-gradient-to-br from-anizai-teal-50 via-anizai-blue-50 to-anizai-purple-50">
                <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide mb-3">
                    New Prediction
                </h2>
                <div className="space-y-3">
                    <Input
                        placeholder="What event would you like to forecast?"
                        value={predictionInput}
                        onChange={(e) => setPredictionInput(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleNewPrediction()}
                        className="bg-white"
                    />
                    <Button
                        variant="primary"
                        className="w-full"
                        onClick={handleNewPrediction}
                        disabled={!predictionInput.trim()}
                    >
                        Analyze Event
                    </Button>
                </div>
            </div>

            {/* Current Question & Answer Summary */}
            {currentQuestion && (
                <div className="p-6 border-b border-gray-100 bg-white">
                    <div className="mb-3">
                        <h3 className="text-sm font-semibold text-gray-700 mb-2">Current Analysis</h3>
                        <p className="text-sm font-medium text-gray-900">
                            {currentQuestion}
                        </p>
                    </div>
                    {currentAnswer && (
                        <div className="p-3 bg-gray-50 rounded-lg">
                            <p className="text-xs text-gray-600 leading-relaxed">
                                {currentAnswer}
                            </p>
                        </div>
                    )}
                </div>
            )}

            {/* Chat Messages */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
                {messages.map((message) => (
                    <div
                        key={message.id}
                        className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    >
                        <div
                            className={`max-w-[85%] rounded-lg p-3 ${message.role === 'user'
                                ? 'bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 text-white'
                                : 'bg-gray-100 text-gray-900'
                                }`}
                        >
                            <div className={`text-sm prose prose-sm max-w-none ${message.role === 'user' ? 'prose-invert' : ''
                                }`}>
                                <ReactMarkdown>{message.content}</ReactMarkdown>
                            </div>
                            <p className={`text-xs mt-2 ${message.role === 'user' ? 'text-white/70' : 'text-gray-500'
                                }`}>
                                {formatRelativeTime(message.timestamp)}
                            </p>
                        </div>
                    </div>
                ))}
            </div>

            {/* Suggested Actions */}
            {suggestedActions.length > 0 && (
                <div className="px-6 py-4 border-t border-gray-100 bg-gray-50">
                    <p className="text-xs font-semibold text-gray-700 uppercase tracking-wide mb-2">
                        Suggested Actions
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {suggestedActions.map((action) => (
                            <Button
                                key={action.id}
                                variant="outline"
                                size="sm"
                                onClick={() => onActionClick(action.id)}
                                className="text-xs"
                            >
                                {action.label}
                            </Button>
                        ))}
                    </div>
                </div>
            )}

            {/* Chat Input */}
            <div className="p-4 border-t border-gray-100">
                <div className="flex gap-2">
                    <Input
                        placeholder="Ask a follow-up question..."
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                    />
                    <Button onClick={handleSend} disabled={!inputValue.trim()}>
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                        </svg>
                    </Button>
                </div>
            </div>
        </div>
    );
}
