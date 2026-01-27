import { ArrowLeft, ShieldCheck, Info, CheckCircle2 } from 'lucide-react';

interface PrivacyPageProps {
  onBack?: () => void;
}

export function PrivacyPage({ onBack }: PrivacyPageProps) {
  return (
    <div className="min-h-screen bg-white">
      {/* Header */}
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
              <ShieldCheck className="w-4 h-4 text-anizai-purple-600" />
              Privacy
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              Privacy{' '}
              <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                Policy
              </span>
            </h1>

            <p className="mt-3 text-sm text-gray-500">Last updated: January 2026</p>
          </div>
        </div>
      </section>

      {/* Content */}
      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto">
          {/* Info banner */}
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm mb-6">
            <div className="flex items-start gap-3">
              <Info className="w-5 h-5 text-anizai-blue-600 mt-0.5" />
              <p className="text-sm text-gray-600 leading-relaxed">
                This policy explains how data is handled in the demo version of Anizai.
                A full legal review is recommended for production use.
              </p>
            </div>
          </div>

          {/* Sections */}
          <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            <div className="p-6 sm:p-8 space-y-10">
              {[
                {
                  title: '1. Information We Collect',
                  body:
                    'We may collect account details (such as email), usage data (feature interactions), and content you submit during forecasting sessions.',
                },
                {
                  title: '2. How We Use Information',
                  body:
                    'Data is used to operate the platform, improve forecasting quality, troubleshoot issues, and enhance the overall user experience.',
                },
                {
                  title: '3. Data Sharing',
                  body:
                    'We do not sell personal information. In this demo, data is used only to support core functionality.',
                },
                {
                  title: '4. Security',
                  body:
                    'We apply reasonable security practices, but no system can guarantee complete security.',
                },
                {
                  title: '5. Data Retention',
                  body:
                    'Data is kept only as long as necessary for functionality or improvement, then deleted or anonymized when possible.',
                },
                {
                  title: '6. Your Choices',
                  body:
                    'You may request deletion of your account data by contacting us via the Contact page (demo scope).',
                },
              ].map((sec) => (
                <section key={sec.title}>
                  <div className="flex items-center gap-2 mb-3">
                    <CheckCircle2 className="w-5 h-5 text-anizai-teal-600" />
                    <h2 className="text-xl font-semibold text-gray-900">{sec.title}</h2>
                  </div>
                  <p className="text-gray-700 leading-relaxed">{sec.body}</p>
                </section>
              ))}
            </div>

            <div className="px-6 sm:px-8 py-4 border-t border-gray-200 bg-gray-50">
              <p className="text-xs text-gray-500">
                Questions about privacy? Contact us via the Contact page.
              </p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
