import { auth } from '../../lib/firebase';
import type { UserProfile } from '../../services/user.service';

interface AccountSettingsProps {
    userProfile: UserProfile | null;
    onLogout: () => void;
}

function formatDate(iso: string | null | undefined): string {
    if (!iso) return 'Not available';
    return new Date(iso).toLocaleDateString('en-US', {
        year: 'numeric',
        month: 'long',
        day: 'numeric',
    });
}

export function AccountSettings({ userProfile, onLogout }: AccountSettingsProps) {
    const email = auth.currentUser?.email ?? userProfile?.email ?? 'Not available';
    const createdAt = userProfile?.createdAt;
    const plan = userProfile?.plan ?? 'free';

    return (
        <div className="space-y-8">
            <div>
                <h2 className="text-xl font-semibold text-gray-900">Account</h2>
                <p className="mt-1 text-sm text-gray-500">Review account details and sign out.</p>
            </div>

            {/* Info card */}
            <div className="border border-gray-200 rounded-xl overflow-hidden">
                <div className="bg-gray-50 px-5 py-3 border-b border-gray-200">
                    <p className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Account details</p>
                </div>
                <dl className="divide-y divide-gray-100">
                    <Row label="Email" value={email} />
                    <Row label="Created" value={formatDate(createdAt)} />
                    <Row
                        label="Plan"
                        value={
                            <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-semibold capitalize ${plan === 'premium' ? 'bg-anizai-blue-100 text-anizai-blue-700' : 'bg-gray-100 text-gray-600'}`}>
                                {plan}
                            </span>
                        }
                    />
                </dl>
            </div>

            {/* Danger zone */}
            <div className="border border-red-100 rounded-xl overflow-hidden">
                <div className="bg-red-50 px-5 py-3 border-b border-red-100">
                    <p className="text-xs font-semibold text-red-400 uppercase tracking-wider">Sign out</p>
                </div>
                <div className="px-5 py-4 flex items-center justify-between">
                    <div>
                        <p className="text-sm font-medium text-gray-900">Sign out</p>
                        <p className="text-xs text-gray-500 mt-0.5">End your current session on this device.</p>
                    </div>
                    <button
                        onClick={onLogout}
                        className="px-4 py-2 text-sm font-medium text-red-600 border border-red-200 rounded-lg hover:bg-red-50 focus:outline-none focus:ring-2 focus:ring-red-400 focus:ring-offset-2 transition-colors"
                    >
                        Sign out
                    </button>
                </div>
            </div>
        </div>
    );
}

interface RowProps {
    label: string;
    value: React.ReactNode;
    mono?: boolean;
}

function Row({ label, value, mono }: RowProps) {
    return (
        <div className="px-5 py-3.5 flex items-start justify-between gap-4">
            <dt className="text-sm text-gray-500 shrink-0">{label}</dt>
            <dd className={`text-sm font-medium text-gray-900 text-right break-all ${mono ? 'font-mono text-xs text-gray-600' : ''}`}>
                {value}
            </dd>
        </div>
    );
}
