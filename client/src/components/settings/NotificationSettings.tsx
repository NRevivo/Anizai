interface NotificationSettingsProps {}

interface NotificationToggle {
    id: string;
    label: string;
    description: string;
}

const NOTIFICATIONS: NotificationToggle[] = [
    {
        id: 'email_tracked',
        label: 'Tracked forecast alerts',
        description: 'Email me when a tracked forecast has new evidence or rationale.',
    },
    {
        id: 'probability_change',
        label: 'Probability shift alerts',
        description: 'Notify me when a forecast probability changes significantly.',
    },
    {
        id: 'trending_events',
        label: 'Trending forecast alerts',
        description: 'Notify me when a trending forecast matches my interests.',
    },
];

// eslint-disable-next-line @typescript-eslint/no-unused-vars
export function NotificationSettings(_: NotificationSettingsProps) {
    return (
        <div className="space-y-8">
            <div>
                <h2 className="text-xl font-semibold text-gray-900">Notifications</h2>
                <p className="mt-1 text-sm text-gray-500">Review notification options for forecasts and evidence.</p>
            </div>

            <div className="border border-gray-200 rounded-xl divide-y divide-gray-100 overflow-hidden">
                {NOTIFICATIONS.map(n => (
                    <div key={n.id} className="flex items-center justify-between px-5 py-4 gap-6 bg-white">
                        <div className="min-w-0">
                            <p className="text-sm font-medium text-gray-900">{n.label}</p>
                            <p className="text-xs text-gray-500 mt-0.5">{n.description}</p>
                        </div>
                        <div className="flex items-center gap-2 shrink-0">
                            <span className="text-xs font-medium text-gray-400 bg-gray-100 border border-gray-200 rounded-full px-2.5 py-0.5 whitespace-nowrap">
                                Coming soon
                            </span>
                            {/* Disabled toggle */}
                            <button
                                type="button"
                                disabled
                                aria-disabled="true"
                                className="relative inline-flex h-6 w-11 items-center rounded-full bg-gray-200 cursor-not-allowed opacity-60"
                            >
                                <span className="inline-block h-4 w-4 translate-x-1 rounded-full bg-white shadow-sm" />
                            </button>
                        </div>
                    </div>
                ))}
            </div>

            <p className="text-xs text-gray-400">
                Notification preferences are not active yet.
            </p>
        </div>
    );
}
