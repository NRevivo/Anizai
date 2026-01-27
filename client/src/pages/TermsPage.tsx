import { ArrowLeft, CheckCircle2, Info } from 'lucide-react';

interface TermsPageProps {
  onBack?: () => void;
}

export function TermsPage({ onBack }: TermsPageProps) {
  return (
    <div className="min-h-screen bg-white">
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

      <section className="relative overflow-hidden">
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute -top-28 -left-28 h-[420px] w-[420px] rounded-full bg-anizai-teal-200/30 blur-3xl" />
          <div className="absolute -bottom-28 -right-28 h-[420px] w-[420px] rounded-full bg-anizai-purple-200/25 blur-3xl" />
          <div className="absolute top-24 right-1/4 h-[260px] w-[260px] rounded-full bg-anizai-blue-200/20 blur-3xl" />
        </div>

        <div className="px-6 pt-14 pb-10">
          <div className="max-w-4xl mx-auto">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/70 px-3 py-1 text-xs text-gray-600">
              <span className="h-2 w-2 rounded-full bg-anizai-purple-500" />
              Legal
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              Terms of{' '}
              <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                Service
              </span>
            </h1>

            <p className="mt-3 text-sm text-gray-500">Last updated: January 2026</p>
          </div>
        </div>
      </section>

      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto">
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm mb-6">
            <div className="flex items-start gap-3">
              <Info className="w-5 h-5 text-anizai-blue-600 mt-0.5" />
              <p className="text-sm text-gray-600 leading-relaxed">
                Forecasts and predictions are probabilistic and provided for informational purposes only.
              </p>
            </div>
          </div>

          <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            <div className="p-6 sm:p-8 space-y-10">
              {[
                {
                  title: '1. Acceptance of Terms',
                  body:
                    'By accessing and using the Anizai platform, you accept and agree to be bound by these terms. If you do not agree, please do not use this service.',
                },
                {
                  title: '2. Use License',
                  body:
                    'Permission is granted to temporarily download one copy of the materials on Anizai for personal, non-commercial viewing only. This is a license, not a transfer of title.',
                  list: [
                    'Modify or copy the materials',
                    'Use the materials for any commercial purpose or public display',
                    'Attempt to decompile or reverse engineer any software on the platform',
                    'Remove copyright or other proprietary notations',
                    'Transmit the materials to another person or “mirror” them on any other server',
                  ],
                },
                {
                  title: '3. Disclaimer',
                  body:
                    'The materials on Anizai are provided on an “as is” basis. Anizai makes no warranties, expressed or implied, and disclaims all other warranties.',
                },
                {
                  title: '4. Limitations',
                  body:
                    'In no event shall Anizai or its suppliers be liable for any damages arising from the use or inability to use the materials on Anizai.',
                },
                {
                  title: '5. Accuracy of Materials',
                  body:
                    'Materials may include errors. Anizai does not warrant that the materials are accurate, complete, or current and may update them at any time without notice.',
                },
                {
                  title: '6. Links',
                  body:
                    'Anizai is not responsible for the contents of linked sites. Use of any linked website is at the user’s own risk.',
                },
                {
                  title: '7. Modifications',
                  body:
                    'Anizai may revise these terms at any time without notice. By using this platform, you agree to be bound by the then-current version.',
                },
                {
                  title: '8. Governing Law',
                  body:
                    'These terms are governed by the applicable laws of the jurisdiction in which Anizai operates.',
                },
              ].map((sec) => (
                <section key={sec.title}>
                  <div className="flex items-center gap-2 mb-3">
                    <CheckCircle2 className="w-5 h-5 text-anizai-teal-600" />
                    <h2 className="text-xl font-semibold text-gray-900">{sec.title}</h2>
                  </div>

                  <p className="text-gray-700 leading-relaxed">{sec.body}</p>

                  {'list' in sec && sec.list ? (
                    <ul className="mt-4 space-y-2 text-gray-700">
                      {sec.list.map((item) => (
                        <li key={item} className="flex items-start gap-3">
                          <span className="mt-1.5 h-1.5 w-1.5 rounded-full bg-gray-400 flex-shrink-0" />
                          <span className="leading-relaxed">{item}</span>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                </section>
              ))}
            </div>

            <div className="px-6 sm:px-8 py-4 border-t border-gray-200 bg-gray-50">
              <p className="text-xs text-gray-500">Questions? Reach us via the Contact page.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
