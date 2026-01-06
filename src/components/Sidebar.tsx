import type { PredictionSession } from '../types';
import { Badge } from './ui/badge';
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
        <div className="w-80 bg-white border-r border-gray-200 flex flex-col h-screen">
            {/* Logo */}
            <div className="p-6 border-b border-gray-100">
                <img
                    src="/logo-with-text.png"
                    alt="Anizai"
                    className="h-12 w-auto"
                />
            </div>

            {/* Header */}
            <div className="px-6 py-4 border-b border-gray-100">
                <h2 className="text-sm font-semibold text-gray-900 uppercase tracking-wide">
                    Prediction Sessions
                </h2>
            </div>

            {/* Sessions List */}
            <div className="flex-1 overflow-y-auto">
                <div className="p-3 space-y-2">
                    {sessions.map((session) => (
                        <button
                            key={session.id}
                            onClick={() => onSessionSelect(session.id)}
                            className={`w-full text-left p-3 rounded-lg transition-colors ${activeSessionId === session.id
                                ? 'bg-gradient-to-r from-anizai-teal-50 via-anizai-blue-50 to-anizai-purple-50 border border-anizai-blue-200'
                                : 'hover:bg-gray-50 border border-transparent'
                                }`}
                        >
                            <div className="flex items-start justify-between gap-2 mb-2">
                                <p className="text-sm font-medium text-gray-900 line-clamp-2 flex-1">
                                    {session.question}
                                </p>
                                <Badge variant={session.status} className="shrink-0">
                                    {session.status}
                                </Badge>
                            </div>
                            <div className="flex items-center justify-between">
                                <span className="text-lg font-semibold gradient-text">
                                    {formatProbability(session.probability)}
                                </span>
                                <span className="text-xs text-gray-500">
                                    {formatRelativeTime(session.lastUpdated)}
                                </span>
                            </div>
                        </button>
                    ))}
                </div>
            </div>

            {/* New Prediction Button */}
            <div className="p-4 border-t border-gray-100">
                <Button
                    variant="primary"
                    className="w-full"
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
