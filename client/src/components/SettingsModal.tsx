import { useState } from 'react';
import type { UserProfile } from '../services/user.service';
import { ProfileSettings } from './settings/ProfileSettings';
import { AccountSettings } from './settings/AccountSettings';
import { PreferenceSettings } from './settings/PreferenceSettings';
import { NotificationSettings } from './settings/NotificationSettings';
import { SecuritySettings } from './settings/SecuritySettings';
import { SubscriptionSettings } from './settings/SubscriptionSettings';

type Section = 'profile' | 'account' | 'subscription' | 'preferences' | 'notifications' | 'security';

interface NavItem {
    id: Section;
    label: string;
    icon: React.ReactNode;
}

const NAV_ITEMS: NavItem[] = [
    {
        id: 'profile',
        label: 'Profile',
        icon: (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M16 7a4 4 0 11-8 0 4 4 0 018 0zM12 14a7 7 0 00-7 7h14a7 7 0 00-7-7z" />
            </svg>
        ),
    },
    {
        id: 'account',
        label: 'Account',
        icon: (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M5.121 17.804A13.937 13.937 0 0112 16c2.5 0 4.847.655 6.879 1.804M15 10a3 3 0 11-6 0 3 3 0 016 0zm6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
            </svg>
        ),
    },
    {
        id: 'preferences',
        label: 'Preferences',
        icon: (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M12 6V4m0 2a2 2 0 100 4m0-4a2 2 0 110 4m-6 8a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4m6 6v10m6-2a2 2 0 100-4m0 4a2 2 0 110-4m0 4v2m0-6V4" />
            </svg>
        ),
    },
    {
        id: 'notifications',
        label: 'Notifications',
        icon: (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M15 17h5l-1.405-1.405A2.032 2.032 0 0118 14.158V11a6.002 6.002 0 00-4-5.659V5a2 2 0 10-4 0v.341C7.67 6.165 6 8.388 6 11v3.159c0 .538-.214 1.055-.595 1.436L4 17h5m6 0v1a3 3 0 11-6 0v-1m6 0H9" />
            </svg>
        ),
    },
    {
        id: 'security',
        label: 'Security',
        icon: (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M9 12l2 2 4-4m5.618-4.016A11.955 11.955 0 0112 2.944a11.955 11.955 0 01-8.618 3.04A12.02 12.02 0 003 9c0 5.591 3.824 10.29 9 11.622 5.176-1.332 9-6.03 9-11.622 0-1.042-.133-2.052-.382-3.016z" />
            </svg>
        ),
    },
    {
        id: 'subscription' as const,
        label: 'Subscription',
        icon: (
            <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.75} d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
            </svg>
        ),
    },
];

interface SettingsModalProps {
    isOpen: boolean;
    onClose: () => void;
    userProfile: UserProfile | null;
    onLogout?: () => void;
    onPlanChange?: (updated: UserProfile) => void;
}

export function SettingsModal({ isOpen, onClose, userProfile, onLogout, onPlanChange }: SettingsModalProps) {
    const [activeSection, setActiveSection] = useState<Section>('profile');

    if (!isOpen) return null;

    const handleLogout = () => {
        onClose();
        onLogout?.();
    };

    const renderSection = () => {
        switch (activeSection) {
            case 'profile':
                return <ProfileSettings userProfile={userProfile} />;
            case 'account':
                return <AccountSettings userProfile={userProfile} onLogout={handleLogout} />;
            case 'subscription':
                return (
                    <SubscriptionSettings
                        userProfile={userProfile}
                        onPlanChange={updated => onPlanChange?.(updated)}
                    />
                );
            case 'preferences':
                return <PreferenceSettings />;
            case 'notifications':
                return <NotificationSettings />;
            case 'security':
                return <SecuritySettings />;
        }
    };

    return (
        <div
            className="fixed inset-0 z-[100] flex items-center justify-center bg-black/50 p-2 sm:p-4"
            onClick={e => { if (e.target === e.currentTarget) onClose(); }}
        >
            <div className="bg-white rounded-xl sm:rounded-2xl shadow-2xl w-full max-w-3xl overflow-hidden flex flex-col max-h-[95dvh] sm:max-h-[90vh]">
                {/* Header */}
                <div className="px-4 sm:px-6 py-3 sm:py-4 border-b border-gray-100 flex items-center justify-between shrink-0">
                    <div className="flex items-center gap-2.5">
                        <svg className="w-5 h-5 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10.325 4.317c.426-1.756 2.924-1.756 3.35 0a1.724 1.724 0 002.573 1.066c1.543-.94 3.31.826 2.37 2.37a1.724 1.724 0 001.065 2.572c1.756.426 1.756 2.924 0 3.35a1.724 1.724 0 00-1.066 2.573c.94 1.543-.826 3.31-2.37 2.37a1.724 1.724 0 00-2.572 1.065c-.426 1.756-2.924 1.756-3.35 0a1.724 1.724 0 00-2.573-1.066c-1.543.94-3.31-.826-2.37-2.37a1.724 1.724 0 00-1.065-2.572c-1.756-.426-1.756-2.924 0-3.35a1.724 1.724 0 001.066-2.573c-.94-1.543.826-3.31 2.37-2.37.996.608 2.296.07 2.572-1.065z" />
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 12a3 3 0 11-6 0 3 3 0 016 0z" />
                        </svg>
                        <h2 className="text-base font-semibold text-gray-900">Settings</h2>
                    </div>
                    <button
                        onClick={onClose}
                        className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 transition-colors"
                        aria-label="Close settings"
                    >
                        <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>

                {/* Body: sidebar + content */}
                <div className="flex flex-col sm:flex-row flex-1 min-h-0">
                    {/* Sidebar nav */}
                    <nav className="w-full sm:w-48 shrink-0 border-b sm:border-b-0 sm:border-r border-gray-100 bg-gray-50 py-2 sm:py-4 flex flex-row sm:flex-col gap-1 sm:gap-0.5 px-2 overflow-x-auto sm:overflow-x-hidden sm:overflow-y-auto">
                        {NAV_ITEMS.map(item => (
                            <button
                                key={item.id}
                                onClick={() => setActiveSection(item.id)}
                                className={`flex shrink-0 items-center gap-2.5 sm:w-full text-left px-3 py-2.5 rounded-lg text-sm font-medium transition-colors ${
                                    activeSection === item.id
                                        ? 'bg-white text-gray-900 shadow-sm border border-gray-200'
                                        : 'text-gray-500 hover:bg-white hover:text-gray-700 hover:border hover:border-gray-100'
                                }`}
                            >
                                <span className={activeSection === item.id ? 'text-anizai-blue-500' : 'text-gray-400'}>
                                    {item.icon}
                                </span>
                                <span className="whitespace-nowrap">{item.label}</span>
                            </button>
                        ))}
                    </nav>

                    {/* Content area */}
                    <div className="flex-1 min-h-0 overflow-y-auto p-4 sm:p-6 lg:p-8">
                        {renderSection()}
                    </div>
                </div>
            </div>
        </div>
    );
}
