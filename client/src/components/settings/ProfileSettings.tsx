import { useState, useEffect } from 'react';
import { updateProfile } from 'firebase/auth';
import { auth } from '../../lib/firebase';
import type { UserProfile } from '../../services/user.service';

interface ProfileSettingsProps {
    userProfile: UserProfile | null;
}

export function ProfileSettings({ userProfile }: ProfileSettingsProps) {
    const [displayName, setDisplayName] = useState('');
    const [isSaving, setIsSaving] = useState(false);
    const [message, setMessage] = useState<{ type: 'success' | 'error'; text: string } | null>(null);

    const isGoogleAuth = auth.currentUser?.providerData.some(p => p.providerId === 'google.com');
    const email = auth.currentUser?.email ?? userProfile?.email ?? '';

    useEffect(() => {
        setDisplayName(auth.currentUser?.displayName ?? userProfile?.displayName ?? '');
    }, [userProfile]);

    const initials = (displayName || email || '?')
        .split(' ')
        .map(w => w[0]?.toUpperCase() ?? '')
        .slice(0, 2)
        .join('');

    const handleSave = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!auth.currentUser) return;
        setIsSaving(true);
        setMessage(null);
        try {
            if (displayName !== auth.currentUser.displayName) {
                await updateProfile(auth.currentUser, { displayName });
                setMessage({ type: 'success', text: 'Display name updated.' });
            } else {
                setMessage({ type: 'success', text: 'No profile changes to save.' });
            }
        } catch (err: any) {
            setMessage({ type: 'error', text: err.message ?? 'Could not update your profile.' });
        } finally {
            setIsSaving(false);
        }
    };

    return (
        <div className="space-y-8">
            <div>
                <h2 className="text-xl font-semibold text-gray-900">Profile</h2>
                <p className="mt-1 text-sm text-gray-500">Update the profile details shown in Anizai.</p>
            </div>

            {/* Avatar */}
            <div className="flex items-center gap-4">
                <div className="w-16 h-16 rounded-full bg-gradient-to-br from-anizai-teal-400 via-anizai-blue-500 to-anizai-purple-500 flex items-center justify-center text-white text-xl font-bold shadow-md select-none">
                    {initials || '?'}
                </div>
                <div>
                    <p className="text-sm font-medium text-gray-900">{displayName || 'No name set'}</p>
                    <p className="text-xs text-gray-500">{email}</p>
                </div>
            </div>

            <form onSubmit={handleSave} className="space-y-5">
                {message && (
                    <div className={`text-sm px-4 py-3 rounded-lg ${message.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
                        {message.text}
                    </div>
                )}

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">
                        Display name
                    </label>
                    <input
                        type="text"
                        value={displayName}
                        onChange={e => setDisplayName(e.target.value)}
                        className="w-full h-10 px-3 py-2 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:border-transparent transition-shadow"
                        placeholder="Your name"
                    />
                </div>

                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">
                        Email address
                    </label>
                    <input
                        type="email"
                        value={email}
                        disabled
                        className="w-full h-10 px-3 py-2 text-sm border border-gray-200 rounded-lg bg-gray-50 text-gray-500 cursor-not-allowed"
                    />
                    <p className="mt-1.5 text-xs text-gray-400">
                        {isGoogleAuth
                            ? 'Your email is managed by Google and cannot be changed here.'
                            : 'Email changes are not supported at this time.'}
                    </p>
                </div>

                <div>
                    <button
                        type="submit"
                        disabled={isSaving}
                        className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-white bg-gray-900 rounded-lg hover:bg-gray-700 focus:outline-none focus:ring-2 focus:ring-gray-800 focus:ring-offset-2 disabled:opacity-60 transition-colors"
                    >
                        {isSaving ? (
                            <>
                                <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                                    <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                    <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                                </svg>
                                Saving...
                            </>
                        ) : (
                            'Save changes'
                        )}
                    </button>
                </div>
            </form>
        </div>
    );
}
