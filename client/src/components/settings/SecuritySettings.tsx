import { useState } from 'react';
import { signInWithPopup, reauthenticateWithCredential, EmailAuthProvider } from 'firebase/auth';
import { auth, googleProvider } from '../../lib/firebase';

export function SecuritySettings() {
    const user = auth.currentUser;
    const isGoogleAuth = user?.providerData.some(p => p.providerId === 'google.com') ?? false;
    const isEmailAuth = user?.providerData.some(p => p.providerId === 'password') ?? false;

    const [status, setStatus] = useState<{ type: 'success' | 'error'; text: string } | null>(null);
    const [isReauthing, setIsReauthing] = useState(false);
    const [emailPassword, setEmailPassword] = useState('');
    const [showEmailInput, setShowEmailInput] = useState(false);

    const handleReauthGoogle = async () => {
        setIsReauthing(true);
        setStatus(null);
        try {
            await signInWithPopup(auth, googleProvider);
            setStatus({ type: 'success', text: 'Session re-authenticated.' });
        } catch (err: any) {
            setStatus({ type: 'error', text: err.message ?? 'Could not re-authenticate your session.' });
        } finally {
            setIsReauthing(false);
        }
    };

    const handleReauthEmail = async (e: React.FormEvent) => {
        e.preventDefault();
        if (!user?.email) return;
        setIsReauthing(true);
        setStatus(null);
        try {
            const cred = EmailAuthProvider.credential(user.email, emailPassword);
            await reauthenticateWithCredential(user, cred);
            setStatus({ type: 'success', text: 'Session re-authenticated.' });
            setShowEmailInput(false);
            setEmailPassword('');
        } catch (err: any) {
            setStatus({ type: 'error', text: err.message ?? 'Could not re-authenticate your session.' });
        } finally {
            setIsReauthing(false);
        }
    };

    const providerLabel = isGoogleAuth ? 'Google' : isEmailAuth ? 'Email / Password' : 'Unknown';

    return (
        <div className="space-y-8">
            <div>
                <h2 className="text-xl font-semibold text-gray-900">Security</h2>
                <p className="mt-1 text-sm text-gray-500">Review sign-in method and session security.</p>
            </div>

            {/* Provider info */}
            <div className="border border-gray-200 rounded-xl overflow-hidden">
                <div className="bg-gray-50 px-5 py-3 border-b border-gray-200">
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Authentication</p>
                </div>
                <div className="divide-y divide-gray-100">
                    <div className="px-5 py-3.5 flex items-center justify-between">
                        <span className="text-sm text-gray-500">Provider</span>
                        <span className="text-sm font-medium text-gray-900 flex items-center gap-2">
                            {isGoogleAuth && (
                                <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none">
                                    <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                                    <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                                    <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
                                    <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                                </svg>
                            )}
                            {providerLabel}
                        </span>
                    </div>
                    <div className="px-5 py-3.5 flex items-center justify-between">
                        <span className="text-sm text-gray-500">Signed in as</span>
                        <span className="text-sm font-medium text-gray-900 break-all text-right">{user?.email ?? 'Not available'}</span>
                    </div>
                </div>
            </div>

            {/* Feedback */}
            {status && (
                <div className={`text-sm px-4 py-3 rounded-lg ${status.type === 'success' ? 'bg-green-50 text-green-700 border border-green-200' : 'bg-red-50 text-red-700 border border-red-200'}`}>
                    {status.text}
                </div>
            )}

            {/* Re-authenticate */}
            <div className="border border-gray-200 rounded-xl overflow-hidden">
                <div className="bg-gray-50 px-5 py-3 border-b border-gray-200">
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Re-authenticate</p>
                </div>
                <div className="px-5 py-4 space-y-4">
                    <p className="text-sm text-gray-500">
                        Re-authenticate when a sensitive account action requires a fresh sign-in.
                    </p>

                    {isGoogleAuth && (
                        <button
                            onClick={handleReauthGoogle}
                            disabled={isReauthing}
                            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:ring-offset-2 disabled:opacity-60 transition-colors"
                        >
                            <svg className="w-4 h-4" viewBox="0 0 24 24" fill="none">
                                <path d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z" fill="#4285F4"/>
                                <path d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z" fill="#34A853"/>
                                <path d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z" fill="#FBBC05"/>
                                <path d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z" fill="#EA4335"/>
                            </svg>
                            {isReauthing ? 'Re-authenticating...' : 'Re-authenticate with Google'}
                        </button>
                    )}

                    {isEmailAuth && !showEmailInput && (
                        <button
                            onClick={() => setShowEmailInput(true)}
                            className="inline-flex items-center gap-2 px-4 py-2 text-sm font-medium text-gray-700 bg-white border border-gray-300 rounded-lg hover:bg-gray-50 focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:ring-offset-2 transition-colors"
                        >
                            Re-authenticate with Password
                        </button>
                    )}

                    {isEmailAuth && showEmailInput && (
                        <form onSubmit={handleReauthEmail} className="flex items-center gap-3">
                            <input
                                type="password"
                                value={emailPassword}
                                onChange={e => setEmailPassword(e.target.value)}
                                placeholder="Password"
                                required
                                className="flex-1 h-9 px-3 text-sm border border-gray-300 rounded-lg focus:outline-none focus:ring-2 focus:ring-anizai-blue-500"
                            />
                            <button
                                type="submit"
                                disabled={isReauthing || !emailPassword}
                                className="px-3 py-2 text-sm font-medium text-white bg-gray-900 rounded-lg hover:bg-gray-700 disabled:opacity-60 transition-colors"
                            >
                                {isReauthing ? '...' : 'Confirm'}
                            </button>
                            <button type="button" onClick={() => { setShowEmailInput(false); setEmailPassword(''); }} className="text-sm text-gray-400 hover:text-gray-600">
                                Cancel
                            </button>
                        </form>
                    )}
                </div>
            </div>
        </div>
    );
}
