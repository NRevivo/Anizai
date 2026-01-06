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
    onActionClick
}: ChatPanelProps) {
    const [inputValue, setInputValue] = useState('');

    const handleSend = () => {
        if (inputValue.trim()) {
            onSendMessage(inputValue);
            setInputValue('');
        }
    };

    return (
        <div className="w-full h-full bg-white border-l border-gray-200 flex flex-col overflow-hidden">
            {/* Current Question & Answer Summary */}
            {currentQuestion && (
                <div className="p-6 border-b border-gray-100 bg-gradient-to-br from-anizai-teal-50/30 via-anizai-blue-50/30 to-anizai-purple-50/30 flex-shrink-0">
                    <div className="mb-3">
                        <h3 className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">Current Analysis</h3>
                        <p className="text-sm font-medium text-gray-900 leading-relaxed">
                            {currentQuestion}
                        </p>
                    </div>
                    {currentAnswer && (
                        <div className="p-3 bg-white/80 rounded-lg border border-gray-200">
                            <p className="text-xs text-gray-600 leading-relaxed">
                                {currentAnswer}
                            </p>
                        </div>
                    )}
                </div>
            )}

            {/* Chat Messages - Scrollable */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
                {messages.map((message) => (
                    <div
                        key={message.id}
                        className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    >
                        <div
                            className={`max-w-[85%] rounded-lg p-3 ${message.role === 'user'
                                    ? 'bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 text-white'
                                    : 'bg-gray-100 text-gray-900 border border-gray-200'
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
                <div className="px-6 py-4 border-t border-gray-100 bg-gray-50 flex-shrink-0">
                    <p className="text-xs font-medium text-gray-500 uppercase tracking-wider mb-2">
                        Suggested Actions
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {suggestedActions.map((action) => (
                            <Button
                                key={action.id}
                                variant="outline"
                                size="sm"
                                onClick={() => onActionClick(action.id)}
                                className="text-xs hover:border-anizai-teal-400 hover:text-anizai-teal-600"
                            >
                                {action.label}
                            </Button>
                        ))}
                    </div>
                </div>
            )}

            {/* Chat Input */}
            <div className="p-4 border-t border-gray-100 flex-shrink-0">
                <div className="flex gap-2">
                    <Input
                        placeholder="Ask a follow-up question..."
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                        className="focus:border-anizai-teal-400 focus:ring-anizai-teal-400"
                    />
                    <Button
                        onClick={handleSend}
                        disabled={!inputValue.trim()}
                        className="bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 hover:from-anizai-teal-600 hover:via-anizai-blue-600 hover:to-anizai-purple-600 text-white border-0"
                    >
                        <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                        </svg>
                    </Button>
                </div>
            </div>
        </div>
    );
}
