import { useState } from 'react';
import type { ChatMessage, SuggestedAction } from '../types';
import { Button } from './ui/button';
import { Input } from './ui/input';
import { StateMessage } from './ui/StateMessage';
import { formatRelativeTime } from '../lib/utils';
import ReactMarkdown from 'react-markdown';

interface ChatPanelProps {
    messages: ChatMessage[];
    suggestedActions: SuggestedAction[];
    isLoading?: boolean;
    isSendingMessage?: boolean;
    // T3 send-lock: true while the session is still producing an answer
    // (initial forecast processing, or a prior follow-up not yet answered).
    // Blocks sending a new message; the input stays editable so the user can
    // draft while they wait.
    isSendLocked?: boolean;
    isAwaitingAssistantResponse?: boolean;
    currentQuestion?: string;
    currentAnswer?: string;
    onSendMessage: (message: string) => void;
    onNewPrediction: (question: string) => void;
    onActionClick: (action: SuggestedAction) => void;
}

export function ChatPanel({
    messages,
    suggestedActions,
    isLoading = false,
    isSendingMessage = false,
    isSendLocked = false,
    isAwaitingAssistantResponse = false,
    onSendMessage,
    onActionClick
}: ChatPanelProps) {
    const [inputValue, setInputValue] = useState('');

    // The send path is closed while a message is mid-flight (isSendingMessage)
    // or while the session is still answering (isSendLocked).
    const isSendDisabled = isSendingMessage || isSendLocked;

    const handleSend = () => {
        if (isSendDisabled) {
            return;
        }

        if (inputValue.trim()) {
            onSendMessage(inputValue);
            setInputValue('');
        }
    };

    return (
        <div className="w-full h-full max-w-full bg-white border-l border-gray-200 flex flex-col overflow-hidden">
            <div className="px-4 py-3 border-b border-gray-100 bg-white">
                <h2 className="text-sm font-semibold text-gray-900">Follow-up</h2>
                <p className="mt-0.5 text-xs text-gray-500">Ask about drivers, assumptions, or evidence.</p>
            </div>

            <div className="flex-1 min-h-0 overflow-y-auto p-3 sm:p-4 space-y-3">
                {isLoading && messages.length === 0 ? (
                    <StateMessage
                        compact
                        variant="loading"
                        title="Loading follow-ups"
                        description="Pulling the latest conversation for this forecast."
                    />
                ) : messages.length === 0 ? (
                    <StateMessage
                        compact
                        title="No follow-ups yet"
                        description="Ask a question about the forecast, evidence, or key assumptions."
                    />
                ) : (
                    messages.map((message) => (
                        <div
                            key={message.id}
                            className={`flex ${message.role === 'user' ? 'justify-end' : 'justify-start'}`}
                        >
                            <div
                                className={`max-w-[92%] min-w-0 rounded-lg px-3.5 py-2.5 ${message.role === 'user'
                                    ? 'bg-slate-100 text-gray-900'
                                    : 'bg-white text-gray-900 border border-gray-100 shadow-sm'
                                    }`}
                            >
                                <div className={`text-sm prose prose-sm max-w-none break-words [&_*]:break-words ${message.role === 'user' ? '' : 'prose-slate'}`}>
                                    <ReactMarkdown>{message.content}</ReactMarkdown>
                                </div>
                                <p className="text-[10px] mt-1.5 text-gray-400">
                                    {formatRelativeTime(message.timestamp)}
                                </p>
                            </div>
                        </div>
                    ))
                )}

                {isAwaitingAssistantResponse ? (
                    <div className="flex justify-start">
                        <div className="max-w-[92%] min-w-0 rounded-lg border border-dashed border-anizai-teal-200 bg-anizai-teal-50/60 px-3.5 py-2.5 text-sm text-anizai-teal-900">
                            <p className="font-medium">Waiting for response</p>
                            <p className="mt-1 text-xs text-anizai-teal-700">
                                The follow-up was sent and the assistant reply will appear here when it is ready.
                            </p>
                        </div>
                    </div>
                ) : null}
            </div>

            {suggestedActions.length > 0 && (
                <div className="px-4 py-3 border-t border-gray-100 bg-gray-50/50 flex-shrink-0">
                    <p className="text-[10px] font-bold text-gray-400 uppercase tracking-wider mb-2">
                        Suggested follow-ups
                    </p>
                    <div className="flex flex-wrap gap-2">
                        {suggestedActions.map((action) => (
                            <Button
                                key={action.id}
                                variant="outline"
                                size="sm"
                                onClick={() => onActionClick(action)}
                                disabled={isSendDisabled}
                                className="h-auto min-h-8 max-w-full whitespace-normal text-xs hover:border-anizai-teal-400 hover:text-anizai-teal-600"
                            >
                                <svg className="mr-1.5 h-3.5 w-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M8 10h.01M12 10h.01M16 10h.01M21 12c0 4.418-4.03 8-9 8a9.863 9.863 0 01-4.255-.949L3 20l1.395-3.72C3.512 15.042 3 13.574 3 12c0-4.418 4.03-8 9-8s9 3.582 9 8z" />
                                </svg>
                                {action.label}
                            </Button>
                        ))}
                    </div>
                </div>
            )}

            <div className="p-3 border-t border-gray-100 flex-shrink-0 bg-white">
                <div className="flex min-w-0 gap-2">
                    <Input
                        placeholder="Ask a follow-up about the forecast or evidence"
                        value={inputValue}
                        onChange={(e) => setInputValue(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && handleSend()}
                        disabled={isSendingMessage}
                        className="min-w-0 bg-gray-50 border-gray-200 focus:bg-white focus:border-anizai-teal-500 focus:ring-1 focus:ring-anizai-teal-500 transition-all text-sm"
                    />
                    <Button
                        onClick={handleSend}
                        disabled={!inputValue.trim() || isSendDisabled}
                        className="h-10 w-10 shrink-0 bg-anizai-teal-600 hover:bg-anizai-teal-700 text-white border-0 shadow-sm"
                    >
                        {isSendingMessage ? (
                            <svg className="h-4 w-4 animate-spin" viewBox="0 0 24 24" fill="none">
                                <circle cx="12" cy="12" r="9" stroke="currentColor" strokeWidth="3" className="opacity-30" />
                                <path d="M21 12a9 9 0 0 0-9-9" stroke="currentColor" strokeWidth="3" strokeLinecap="round" />
                            </svg>
                        ) : (
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 19l9 2-9-18-9 18 9-2zm0 0v-8" />
                            </svg>
                        )}
                    </Button>
                </div>
            </div>
        </div>
    );
}
