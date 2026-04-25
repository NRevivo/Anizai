import { useState } from 'react';
import type { PredictionSession } from '../types';
import { Button } from './ui/button';
import { formatProbability, formatRelativeTime } from '../lib/utils';

interface SidebarProps {
    sessions: PredictionSession[];
    activeSessionId: string;
    userDisplayName?: string | null;
    userPlan?: 'free' | 'premium';
    onSessionSelect: (id: string) => void;
    onNewPrediction: () => void;
    onDeleteSession?: (id: string) => void;
    onLogout?: () => void;
    onSettings?: () => void;
    onGoHome?: () => void;
}

export function Sidebar({
    sessions,
    activeSessionId,
    userDisplayName,
    userPlan = 'free',
    onSessionSelect,
    onNewPrediction,
    onDeleteSession,
    onLogout,
    onSettings,
    onGoHome,
}: SidebarProps) {
    const [isProfileMenuOpen, setIsProfileMenuOpen] = useState(false);
    const name = userDisplayName?.trim() || 'User';
    const initials = name
        .split(' ')
        .filter(Boolean)
        .slice(0, 2)
        .map((part) => part[0]?.toUpperCase() ?? '')
        .join('') || 'U';
    const membershipLabel = userPlan === 'premium' ? 'Premium Plan' : 'Free Plan';

    return (
        <div className="w-full h-full bg-slate-50 border-r border-gray-200 flex flex-col overflow-hidden font-sans">
            {/* Logo area */}
            <button onClick={onGoHome} className="w-full text-left px-5 py-5 flex items-center gap-3 border-b border-gray-100 bg-white hover:bg-gray-50 transition-colors cursor-pointer decoration-transparent focus:outline-none">
                <img
                    src="/logo-brain.png"
                    alt="Anizai"
                    className="h-8 w-auto"
                />
                <span className="text-xl font-semibold text-gray-900 tracking-tight">Anizai</span>
            </button>

            {/* Actions & Search */}
            <div className="p-4 bg-white border-b border-gray-100 space-y-3">
                <Button
                    className="w-full justify-start bg-gray-900 text-white hover:bg-gray-800 shadow-sm transition-all"
                    onClick={onNewPrediction}
                >
                    <svg className="w-4 h-4 mr-2" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 4v16m8-8H4" />
                    </svg>
                    New Forecast
                </Button>

                <div className="relative">
                    <svg className="absolute left-3 top-2.5 w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M21 21l-6-6m2-5a7 7 0 11-14 0 7 7 0 0114 0z" />
                    </svg>
                    <input
                        type="text"
                        placeholder="Search predictions..."
                        className="w-full pl-9 pr-3 py-2 text-sm bg-gray-50 border border-gray-200 rounded-md focus:outline-none focus:ring-2 focus:ring-anizai-blue-500/20 focus:border-anizai-blue-500 transition-all placeholder-gray-400 text-gray-700"
                    />
                </div>
            </div>

            {/* Sessions List - Scrollable */}
            <div className="flex-1 overflow-y-auto bg-white">
                <div className="px-4 py-2">
                    <h3 className="text-[11px] font-bold text-gray-400 uppercase tracking-wider mb-3 mt-4 px-2">Active Forecasts</h3>
                    <div className="space-y-1">
                        {sessions.map((session) => (
                            <div
                                key={session.id}
                                className={`relative w-full text-left px-3 py-3 rounded-md transition-all duration-200 group border ${activeSessionId === session.id
                                    ? 'bg-anizai-blue-50 border-anizai-blue-100 shadow-sm'
                                    : 'hover:bg-gray-50 border-transparent hover:border-gray-100'
                                    }`}
                            >
                                <button
                                    onClick={() => onSessionSelect(session.id)}
                                    className="w-full text-left"
                                >
                                    <div className="flex items-start justify-between gap-2">
                                        <p className={`text-sm leading-snug line-clamp-2 ${activeSessionId === session.id ? 'font-medium text-gray-900' : 'text-gray-600'}`}>
                                            {session.question}
                                        </p>
                                        <span className={`text-xs font-semibold shrink-0 ${activeSessionId === session.id ? 'text-anizai-blue-600' : 'text-gray-400'}`}>
                                            {formatProbability(session.probability)}
                                        </span>
                                    </div>
                                    <div className="flex items-center gap-2 mt-2">
                                        <div className="flex items-center gap-1.5">
                                            <div className={`w-1.5 h-1.5 rounded-full ${session.status === 'volatile' ? 'bg-amber-500' : 'bg-green-500'}`} />
                                            <span className="text-[10px] text-gray-400 font-medium uppercase tracking-wide">{session.status}</span>
                                        </div>
                                        <span className="text-[10px] text-gray-300">•</span>
                                        <span className="text-[10px] text-gray-400">{formatRelativeTime(session.lastUpdated)}</span>
                                    </div>
                                </button>
                                {onDeleteSession && (
                                    <button
                                        onClick={(e) => {
                                            e.stopPropagation();
                                            onDeleteSession(session.id);
                                        }}
                                        className="absolute bottom-2 right-2 p-1 rounded-md bg-white/90 border border-gray-200 opacity-0 group-hover:opacity-100 hover:bg-red-50 hover:border-red-300 transition-all shadow-sm"
                                        title="Delete forecast"
                                    >
                                        <svg className="w-3 h-3 text-gray-400 hover:text-red-600" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 7l-.867 12.142A2 2 0 0116.138 21H7.862a2 2 0 01-1.995-1.858L5 7m5 4v6m4-6v6m1-10V4a1 1 0 00-1-1h-4a1 1 0 00-1 1v3M4 7h16" />
                                        </svg>
                                    </button>
                                )}
                            </div>
                        ))}
                    </div>
                </div>
            </div>

            {/* User Profile Footer with Dropdown */}
            <div className="p-4 border-t border-gray-200 bg-gray-50 relative">
                <button
                    onClick={() => setIsProfileMenuOpen(!isProfileMenuOpen)}
                    className="w-full flex items-center gap-3 hover:bg-gray-100 p-2 rounded-lg transition-colors"
                >
                    <div className="w-8 h-8 rounded-full bg-gradient-to-br from-gray-700 to-gray-900 flex items-center justify-center text-white font-medium text-xs shadow-sm">
                        {initials}
                    </div>
                    <div className="flex-1 min-w-0 text-left">
                        <p className="text-sm font-medium text-gray-900 truncate">{name}</p>
                        <p className="text-xs text-gray-500 truncate">{membershipLabel}</p>
                    </div>
                    <svg className={`w-4 h-4 text-gray-400 transition-transform ${isProfileMenuOpen ? 'rotate-180' : ''}`} fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M19 9l-7 7-7-7" />
                    </svg>
                </button>

                {/* Dropdown Menu */}
                {isProfileMenuOpen && (
                    <div className="absolute bottom-full left-4 right-4 mb-2 bg-white border border-gray-200 rounded-lg shadow-lg overflow-hidden z-50">
                        <button
                            onClick={() => {
                                setIsProfileMenuOpen(false);
                                onSettings?.();
                            }}
                            className="w-full flex items-center gap-3 px-4 py-3 text-left text-sm text-gray-700 hover:bg-gray-50 transition-colors"
                        >
                            <svg className="w-4 h-4 text-gray-500" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                            </svg>
                            Settings
                        </button>
                        <div className="border-t border-gray-100" />
                        <button
                            onClick={() => {
                                setIsProfileMenuOpen(false);
                                onLogout?.();
                            }}
                            className="w-full flex items-center gap-3 px-4 py-3 text-left text-sm text-red-600 hover:bg-red-50 transition-colors"
                        >
                            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M17 16l4-4m0 0l-4-4m4 4H7m6 4v1a3 3 0 01-3 3H6a3 3 0 01-3-3V7a3 3 0 013-3h4a3 3 0 013 3v1" />
                            </svg>
                            Log out
                        </button>
                    </div>
                )}
            </div>
        </div>
    );
}
