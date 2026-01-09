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
            {/* Chat Messages - Scrollable */}
            <div className="flex-1 overflow-y-auto p-6 space-y-4">
                {messages.map((message) => (
                    <div
                        key={message.id}
                        className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                    >
                        <div
                            className={`max-w-[85%] rounded-lg px-4 py-3 ${message.role === 'user'
                                ? 'bg-slate-100 text-gray-900'
                                : 'bg-white text-gray-900 border border-gray-100 shadow-sm'
                                }`}
                        >
                            <div className={`text-sm prose prose-sm max-w-none ${message.role === 'user' ? '' : 'prose-slate'}`}>
                                <ReactMarkdown>{message.content}</ReactMarkdown>
                            </div>
                            <p className="text-[10px] mt-1.5 text-gray-400">
                                {formatRelativeTime(message.timestamp)}
                            </p>
                        </div>
                    </div>
                ))}
            </div>

            {/* Suggested Actions */}
            {suggestedActions.length > 0 && (
                <div className="px-6 py-4 border-t border-gray-100 bg-gray-50/50 flex-shrink-0">
                    <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
                        Next Steps
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

            <div className="p-4 border-t border-gray-100 flex-shrink-0 bg-white">
                <div className="flex gap-2">
                    <Input
                        placeholder="Ask a follow-up (drivers, risks, what could change)..."
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                        className="bg-gray-50 border-gray-200 focus:bg-white focus:border-anizai-teal-500 focus:ring-1 focus:ring-anizai-teal-500 transition-all text-sm"
                    />
                    <Button
                        onClick={handleSend}
                        disabled={!inputValue.trim()}
                        className="bg-anizai-teal-600 hover:bg-anizai-teal-700 text-white border-0 shadow-sm"
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
