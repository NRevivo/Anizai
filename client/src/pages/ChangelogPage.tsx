import { ArrowLeft, Sparkles } from 'lucide-react';

interface ChangelogPageProps {
    onBack?: () => void;
}

export function ChangelogPage({ onBack }: ChangelogPageProps) {
    const entries = [
        {
            date: 'January 2026',
            version: 'v2.5.0',
            title: 'Major Performance Update',
            changes: [
                'Improved prediction accuracy by 15% with new ML models',
                'Added real-time collaboration features',
                'Enhanced dashboard with new visualization options',
                'Fixed critical bugs in forecast export'
            ]
        },
        {
            date: 'December 2025',
            version: 'v2.4.0',
            title: 'New Features Release',
            changes: [
                'Added sentiment analysis integration',
                'New API endpoints for third-party integrations',
                'Improved user onboarding experience',
                'Dark mode support'
            ]
        },
        {
            date: 'November 2025',
            version: 'v2.3.0',
            title: 'UI Improvements',
            changes: [
                'Redesigned forecasting interface',
                'New card-based layout for forecasts',
                'Improved mobile responsiveness',
                'Added keyboard shortcuts'
            ]
        },
        {
            date: 'October 2025',
            version: 'v2.0.0',
            title: 'Major Release',
            changes: [
                'Complete redesign of the platform',
                'New authentication system',
                'Improved data security',
                'New pricing tiers'
            ]
        }
    ];

    return (
        <div className="min-h-screen bg-white">
            {/* Header */}
            <div className="bg-gray-50 border-b border-gray-200 px-6 py-6">
                <div className="max-w-4xl mx-auto flex items-center gap-4">
                    <button
                        onClick={onBack}
                        className="flex items-center gap-2 text-gray-600 hover:text-gray-900 transition-colors"
                    >
                        <ArrowLeft className="w-5 h-5" />
                        <span className="font-medium">Back</span>
                    </button>
                </div>
            </div>

            {/* Main Content */}
            <div className="px-6 py-16">
                <div className="max-w-4xl mx-auto">
                    <div className="mb-12">
                        <h1 className="text-4xl font-bold text-gray-900 mb-4">Changelog</h1>
                        <p className="text-xl text-gray-600">
                            Latest updates and improvements to Anizai.
                        </p>
                    </div>

                    {/* Changelog Entries */}
                    <div className="space-y-8">
                        {entries.map((entry, index) => (
                            <div key={index} className="border-l-4 border-blue-600 pl-6 pb-8">
                                <div className="flex items-start justify-between mb-2">
                                    <div>
                                        <h3 className="text-2xl font-semibold text-gray-900">{entry.title}</h3>
                                        <p className="text-sm text-gray-500 mt-1">{entry.date} • {entry.version}</p>
                                    </div>
                                    <Sparkles className="w-6 h-6 text-blue-600 flex-shrink-0" />
                                </div>
                                <ul className="space-y-2 mt-4">
                                    {entry.changes.map((change, idx) => (
                                        <li key={idx} className="flex items-start gap-3 text-gray-700">
                                            <span className="text-blue-600 font-bold mt-1">•</span>
                                            <span>{change}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
