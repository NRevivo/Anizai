import { ArrowLeft, BarChart3, Brain, TrendingUp, Zap, Shield, Users, CheckCircle2 } from 'lucide-react';
import { Button } from '../components/ui/button';

interface FeaturesPageProps {
  onBack?: () => void;
  onGetStarted?: () => void; // אופציונלי לחיבור ל-login/plan
}

export function FeaturesPage({ onBack, onGetStarted }: FeaturesPageProps) {
  const features = [
    {
      icon: Brain,
      title: 'AI-Powered Analysis',
      description: 'Turns real-time signals into forecasts you can understand and trust.',
      accent: 'purple',
    },
    {
      icon: BarChart3,
      title: 'Real-Time Data',
      description: 'Keeps context updated using live and recent information streams.',
      accent: 'blue',
    },
    {
      icon: TrendingUp,
      title: 'Trend Tracking',
      description: 'Highlights changes in sentiment and momentum as they develop.',
      accent: 'teal',
    },
    {
      icon: Zap,
      title: 'Fast Sessions',
      description: 'Create a forecast session in seconds with structured outputs.',
      accent: 'blue',
    },
    {
      icon: Shield,
      title: 'Secure & Private',
      description: 'Designed with privacy and safe defaults in mind for user data.',
      accent: 'purple',
    },
    {
      icon: Users,
      title: 'Collaboration Ready',
      description: 'Built to support teams and shared workflows over time.',
      accent: 'teal',
    },
  ] as const;

  const accentStyles = (accent: 'blue' | 'teal' | 'purple') => {
    if (accent === 'teal') return { bg: 'bg-teal-50', border: 'border-teal-100', text: 'text-teal-700', icon: 'text-teal-600' };
    if (accent === 'purple') return { bg: 'bg-purple-50', border: 'border-purple-100', text: 'text-purple-700', icon: 'text-purple-600' };
    return { bg: 'bg-blue-50', border: 'border-blue-100', text: 'text-blue-700', icon: 'text-blue-600' };
  };

  return (
    <div className="min-h-screen bg-white">
      {/* Header (sticky like the other pages) */}
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
          <div className="absolute -top-28 -left-28 h-[420px] w-[420px] rounded-full bg-teal-200/30 blur-3xl" />
          <div className="absolute -bottom-28 -right-28 h-[420px] w-[420px] rounded-full bg-purple-200/25 blur-3xl" />
          <div className="absolute top-24 right-1/4 h-[260px] w-[260px] rounded-full bg-blue-200/20 blur-3xl" />
        </div>

        <div className="px-6 pt-14 pb-10">
          <div className="max-w-6xl mx-auto">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/70 px-3 py-1 text-xs text-gray-600">
              <span className="h-2 w-2 rounded-full bg-blue-600" />
              Product features
            </div>

            <div className="flex flex-col lg:flex-row lg:items-end lg:justify-between gap-8">
              <div>
                <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
                  Features that help you forecast with{' '}
                  <span className="text-blue-600">confidence</span>
                </h1>
                <p className="mt-4 text-lg sm:text-xl text-gray-600 max-w-2xl">
                  Anizai combines real-time signals, evidence retrieval, and explainable outputs — so forecasts are clear, not mysterious.
                </p>
              </div>
            </div>

            {/* Quick value bullets */}
            <div className="mt-8 grid grid-cols-1 sm:grid-cols-3 gap-4">
              {[
                'Evidence-first outputs',
                'Confidence & uncertainty',
                'Designed for real workflows',
              ].map((t) => (
                <div key={t} className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                  <div className="flex items-start gap-2">
                    <CheckCircle2 className="w-4.5 h-4.5 text-teal-600 mt-0.5" />
                    <p className="text-sm font-medium text-gray-900">{t}</p>
                  </div>
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>

      {/* Features grid */}
      <div className="px-6 pb-16">
        <div className="max-w-6xl mx-auto">
          <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-6">
            {features.map((feature, index) => {
              const Icon = feature.icon;
              const s = accentStyles(feature.accent);

              return (
                <div
                  key={index}
                  className="group rounded-2xl border border-gray-200 bg-white p-7 shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all"
                >
                  <div className={`inline-flex items-center gap-2 rounded-xl border ${s.border} ${s.bg} px-3 py-2`}>
                    <Icon className={`w-5 h-5 ${s.icon}`} />
                    <span className={`text-xs font-semibold ${s.text}`}>Feature</span>
                  </div>

                  <h3 className="mt-4 text-lg font-semibold text-gray-900">
                    {feature.title}
                  </h3>

                  <p className="mt-2 text-sm text-gray-600 leading-relaxed">
                    {feature.description}
                  </p>

                  <div className="mt-5 h-px bg-gray-100" />

                  <p className="mt-4 text-xs text-gray-500">
                    Built to support evidence-based forecasting.
                  </p>
                </div>
              );
            })}
          </div>

          {/* Bottom CTA */}
          <div className="mt-12 rounded-2xl border border-gray-200 bg-gray-50 p-8">
            <h3 className="text-xl font-semibold text-gray-900">Want to see it in action?</h3>
            <p className="mt-2 text-gray-600 max-w-2xl">
              Create your first forecasting session and explore how evidence and confidence scores are generated.
            </p>
            <div className="mt-6 flex flex-col sm:flex-row gap-3">
              <Button
                variant="primary"
                className="h-11 px-6"
                onClick={onGetStarted}
                disabled={!onGetStarted}
              >
                Get started
              </Button>
              <Button variant="outline" className="h-11 px-6" onClick={onBack}>
                Back
              </Button>
            </div>
          </div>

          <p className="mt-8 text-xs text-gray-400">
            Note: Feature descriptions are aligned with the project scope and demo flow.
          </p>
        </div>
      </div>
    </div>
  );
}
