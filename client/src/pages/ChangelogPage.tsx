import { ArrowLeft, Sparkles, CheckCircle2 } from 'lucide-react';

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
        'Fixed critical bugs in forecast export',
      ],
    },
    {
      date: 'December 2025',
      version: 'v2.4.0',
      title: 'New Features Release',
      changes: [
        'Added sentiment analysis integration',
        'New API endpoints for third-party integrations',
        'Improved user onboarding experience',
        'Dark mode support',
      ],
    },
    {
      date: 'November 2025',
      version: 'v2.3.0',
      title: 'UI Improvements',
      changes: [
        'Redesigned forecasting interface',
        'New card-based layout for forecasts',
        'Improved mobile responsiveness',
        'Added keyboard shortcuts',
      ],
    },
    {
      date: 'October 2025',
      version: 'v2.0.0',
      title: 'Major Release',
      changes: [
        'Complete redesign of the platform',
        'New authentication system',
        'Improved data security',
        'New pricing tiers',
      ],
    },
  ];

  return (
    <div className="min-h-screen bg-white">
      {/* Header (sticky) */}
      <div className="bg-white/80 backdrop-blur border-b border-gray-200 px-6 py-5 sticky top-0 z-20">
        <div className="max-w-6xl mx-auto flex items-center gap-4">
          <button
            onClick={onBack}
            className="flex items-center gap-2 text-gray-600 hover:text-gray-900 transition-colors"
          >
            <ArrowLeft className="w-5 h-5" />
            <span className="font-medium">Back</span>
          </button>
        </div>
      </div>

      {/* Hero */}
      <section className="relative overflow-hidden">
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute -top-28 -left-28 h-[420px] w-[420px] rounded-full bg-anizai-teal-200/30 blur-3xl" />
          <div className="absolute -bottom-28 -right-28 h-[420px] w-[420px] rounded-full bg-anizai-purple-200/25 blur-3xl" />
          <div className="absolute top-24 right-1/4 h-[260px] w-[260px] rounded-full bg-anizai-blue-200/20 blur-3xl" />
        </div>

        <div className="px-6 pt-14 pb-10">
          <div className="max-w-4xl mx-auto">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/70 px-3 py-1 text-xs text-gray-600">
              <Sparkles className="w-4 h-4 text-anizai-purple-600" />
              Updates
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                Changelog
              </span>
            </h1>

            <p className="mt-4 text-lg sm:text-xl text-gray-600 max-w-2xl">
              Latest releases, fixes, and product improvements.
            </p>
          </div>
        </div>
      </section>

      {/* Content */}
      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto">
          <div className="space-y-6">
            {entries.map((entry, index) => (
              <div
                key={index}
                className="rounded-2xl border border-gray-200 bg-white shadow-sm hover:shadow-md transition-shadow overflow-hidden"
              >
                {/* Card header */}
                <div className="p-6 sm:p-7 border-b border-gray-100">
                  <div className="flex items-start justify-between gap-4">
                    <div>
                      <div className="flex flex-wrap items-center gap-2 mb-2">
                        <span className="inline-flex items-center rounded-full bg-gray-100 px-3 py-1 text-xs font-semibold text-gray-700">
                          {entry.version}
                        </span>
                        <span className="text-xs text-gray-500">{entry.date}</span>
                      </div>

                      <h3 className="text-xl sm:text-2xl font-semibold text-gray-900">
                        {entry.title}
                      </h3>
                    </div>

                    <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-anizai-blue-50 border border-anizai-blue-100">
                      <Sparkles className="w-5 h-5 text-anizai-blue-600" />
                    </div>
                  </div>
                </div>

                {/* Changes */}
                <div className="p-6 sm:p-7">
                  <ul className="space-y-3">
                    {entry.changes.map((change, idx) => (
                      <li key={idx} className="flex items-start gap-3 text-gray-700">
                        <CheckCircle2 className="w-5 h-5 text-anizai-teal-600 mt-0.5 flex-shrink-0" />
                        <span className="leading-relaxed">{change}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            ))}
          </div>

          <p className="mt-10 text-xs text-gray-400">
            Note: Release notes are illustrative for the project demo.
          </p>
        </div>
      </div>
    </div>
  );
}
