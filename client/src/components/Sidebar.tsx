import type { PredictionSession } from '../types';
import { Button } from './ui/button';
import { formatProbability, formatRelativeTime } from '../lib/utils';

interface SidebarProps {
    sessions: PredictionSession[];
    activeSessionId: string;
    onSessionSelect: (id: string) => void;
    onNewPrediction: () => void;
}

export function Sidebar({ sessions, activeSessionId, onSessionSelect, onNewPrediction }: SidebarProps) {
    return (
        <div className="w-full h-full bg-white border-r border-gray-200 flex flex-col overflow-hidden">
            {/* Logo */}
            <div className="px-4 py-3 border-b border-gray-100 flex justify-center flex-shrink-0">
                <img
                    src="/logo-with-text.png"
                    alt="Anizai"
                    className="h-20 w-auto"
                />
            </div>

            {/* Header */}
            <div className="px-6 py-4 border-b border-gray-100 flex-shrink-0">
                <h2 className="text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Prediction Sessions
                </h2>
            </div>

            {/* Sessions List - Scrollable */}
            <div className="flex-1 overflow-y-auto">
                <div className="p-4 space-y-3">
                    {sessions.map((session) => (
                        <button
                            key={session.id}
                            onClick={() => onSessionSelect(session.id)}
                            className={`w-full text-left p-4 rounded-lg transition-all duration-200 group relative ${activeSessionId === session.id
                                ? 'bg-gradient-to-r from-anizai-teal-50/50 via-anizai-blue-50/50 to-anizai-purple-50/50 border border-anizai-blue-200/50'
                                : 'hover:bg-gray-50/50 border border-transparent'
                                }`}
                        >
                            {activeSessionId === session.id && (
                                <div className="absolute left-0 top-0 bottom-0 w-1 bg-gradient-to-b from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 rounded-l-lg" />
                            )}

                            <div className="flex items-start justify-between gap-3 mb-3">
                                <p className={`text-sm leading-relaxed line-clamp-2 flex-1 ${session.status === 'volatile' ? 'font-medium text-gray-900' : 'font-normal text-gray-700'
                                    }`}>
                                    {session.question}
                                </p>
                                <div className="flex items-center gap-1.5 shrink-0 mt-0.5">
                                    <div className={`w-1.5 h-1.5 rounded-full ${session.status === 'volatile' ? 'bg-amber-400' : 'bg-gray-300'
                                        }`} />
                                    <span className={`text-xs ${session.status === 'volatile' ? 'text-gray-600 italic' : 'text-gray-400'
                                        }`}>
                                        {session.status}
                                    </span>
                                </div>
                            </div>
                            <div className="flex items-center justify-between">
                                <span className="text-base font-semibold bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                                    {formatProbability(session.probability)}
                                </span>
                                <span className="text-xs text-gray-400">
                                    {formatRelativeTime(session.lastUpdated)}
                                </span>
                            </div>
                        </button>
                    ))}
                </div>
            </div>

            {/* New Prediction Button */}
            <div className="p-4 border-t border-gray-100 flex-shrink-0">
                <Button
                    className="w-full justify-center bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 hover:from-anizai-teal-600 hover:via-anizai-blue-600 hover:to-anizai-purple-600 text-white border-0"
                    onClick={onNewPrediction}
                >
                    <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    New Prediction
                </Button>
            </div>
        </div>
    );
}
