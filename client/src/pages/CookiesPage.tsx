import { ArrowLeft, Cookie, Settings, CheckCircle2, Info } from 'lucide-react';

interface CookiesPageProps {
  onBack?: () => void;
}

export function CookiesPage({ onBack }: CookiesPageProps) {
  const cookieTypes = [
    {
      title: 'Essential Cookies',
      desc: 'Required for core functionality like security, accessibility, and basic site operation.',
    },
    {
      title: 'Performance Cookies',
      desc: 'Help us understand how the site is used (pages visited, errors) so we can improve performance.',
    },
    {
      title: 'Functional Cookies',
      desc: 'Remember preferences like language, UI settings, and basic personalization.',
    },
    {
      title: 'Marketing Cookies',
      desc: 'Used to measure campaigns and deliver more relevant content or ads (if enabled).',
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
              <Cookie className="w-4 h-4 text-anizai-purple-600" />
              Cookies
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              Cookie{' '}
              <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                Policy
              </span>
            </h1>

            <p className="mt-3 text-sm text-gray-500">Last updated: January 2026</p>

            <p className="mt-4 text-lg sm:text-xl text-gray-600 max-w-2xl">
              Cookies help us run the platform smoothly and improve your experience. Here’s what we use and how you can control it.
            </p>
          </div>
        </div>
      </section>

      {/* Content */}
      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto">
          {/* Quick note */}
          <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm mb-6">
            <div className="flex items-start gap-3">
              <Info className="w-5 h-5 text-anizai-blue-600 mt-0.5" />
              <p className="text-sm text-gray-600 leading-relaxed">
                In the demo version, cookie usage is minimal. For production, a full review and consent flow is recommended.
              </p>
            </div>
          </div>

          {/* What are cookies */}
          <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden mb-6">
            <div className="p-6 sm:p-8">
              <h2 className="text-2xl font-semibold text-gray-900 mb-2">What Are Cookies?</h2>
              <p className="text-gray-700 leading-relaxed">
                Cookies are small pieces of text stored on your device when you visit a website. They help remember preferences
                and provide analytics that improve reliability and performance.
              </p>
            </div>
          </div>

          {/* Types grid */}
          <div className="mb-6">
            <h2 className="text-2xl font-semibold text-gray-900 mb-2">Types of Cookies We Use</h2>
            <p className="text-gray-600 mb-5">
              We group cookies into categories so you can understand what each one does.
            </p>

            <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
              {cookieTypes.map((c) => (
                <div
                  key={c.title}
                  className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow"
                >
                  <div className="flex items-start gap-3">
                    <CheckCircle2 className="w-5 h-5 text-anizai-teal-600 mt-0.5" />
                    <div>
                      <h3 className="text-lg font-semibold text-gray-900">{c.title}</h3>
                      <p className="text-gray-600 mt-1 leading-relaxed">{c.desc}</p>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>

          {/* Your choices banner */}
          <div className="rounded-2xl border border-gray-200 bg-gradient-to-r from-gray-50 via-white to-gray-50 p-7 shadow-sm mb-6">
            <div className="flex items-start gap-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-anizai-blue-50 border border-anizai-blue-100">
                <Settings className="h-5 w-5 text-anizai-blue-600" />
              </div>
              <div>
                <h2 className="text-2xl font-semibold text-gray-900">Your Choices</h2>
                <p className="mt-2 text-gray-700 leading-relaxed">
                  You can accept or reject cookies using your browser settings. Disabling some cookies may affect parts of the site.
                  In a production version, we recommend adding a consent banner for non-essential cookies.
                </p>
              </div>
            </div>
          </div>

          {/* Third-party + duration */}
          <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-6">
            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
              <h2 className="text-xl font-semibold text-gray-900 mb-2">Third-Party Cookies</h2>
              <p className="text-gray-700 leading-relaxed">
                We may allow third-party providers (analytics or embedded services) to place cookies. Those providers follow their
                own privacy policies.
              </p>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm">
              <h2 className="text-xl font-semibold text-gray-900 mb-2">How Long Do Cookies Last?</h2>
              <p className="text-gray-700 leading-relaxed">
                Cookies can be session-based (deleted when you close the browser) or persistent (stored until they expire).
                Duration depends on the cookie’s purpose.
              </p>
            </div>
          </div>

          {/* Contact footer */}
          <div className="rounded-2xl border border-gray-200 bg-white shadow-sm overflow-hidden">
            <div className="p-6 sm:p-8">
              <h2 className="text-xl font-semibold text-gray-900 mb-2">Contact</h2>
              <p className="text-gray-700 leading-relaxed">
                If you have questions about cookie usage, please reach out via the Contact page.
              </p>
            </div>

            <div className="px-6 sm:px-8 py-4 border-t border-gray-200 bg-gray-50">
              <p className="text-xs text-gray-500">This page is provided for project/demo purposes.</p>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
