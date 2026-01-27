import { ArrowLeft, Lightbulb } from 'lucide-react';
import { Button } from '../components/ui/button';

interface AboutPageProps {
  onBack?: () => void;
  // CTA בסוף: לא חובה, אבל מומלץ כדי לחבר ל־Get Started / Pricing
  onGetStarted?: () => void;
  onMethodology?: () => void;
}

export function AboutPage({ onBack, onGetStarted, onMethodology }: AboutPageProps) {
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
        {/* Soft gradient background */}
        <div className="absolute inset-0 pointer-events-none">
          <div className="absolute -top-32 -left-32 h-[420px] w-[420px] rounded-full bg-anizai-teal-200/35 blur-3xl" />
          <div className="absolute -bottom-32 -right-32 h-[420px] w-[420px] rounded-full bg-anizai-purple-200/30 blur-3xl" />
          <div className="absolute top-24 right-1/4 h-[260px] w-[260px] rounded-full bg-anizai-blue-200/25 blur-3xl" />
        </div>

        <div className="px-6 pt-14 pb-10">
          <div className="max-w-4xl mx-auto">
            <div className="mb-4 inline-flex items-center gap-2 rounded-full border border-gray-200 bg-white/70 px-3 py-1 text-xs text-gray-600">
              <span className="h-2 w-2 rounded-full bg-anizai-teal-500" />
              Evidence-based forecasting
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              About <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">Anizai</span>
            </h1>

            <p className="mt-4 text-lg sm:text-xl text-gray-600 max-w-2xl">
              Transforming uncertainty into confidence through evidence-based forecasting.
            </p>

            
          </div>
        </div>
      </section>

      {/* Main Content */}
      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto">
          {/* Story Section */}
          <div className="mt-8 mb-14">
            <h2 className="text-2xl font-semibold text-gray-900 mb-4">Our Story</h2>

            <p className="text-gray-700 mb-4 leading-relaxed">
              Anizai is an advanced, RAG-based (Retrieval-Augmented Generation) forecasting platform designed to provide
              contextual and data-driven insights into future events. In today’s fast-paced digital world, individuals
              and organizations struggle to understand the cumulative impact of news, public sentiment, and expert
              opinions on upcoming occurrences.
            </p>

            <p className="text-gray-700 leading-relaxed">
              We address this challenge by building a dynamic, real-time knowledge base that ingests information from
              global news outlets, social media trends, and specialized expert publications—moving beyond static or
              superficial AI responses.
            </p>

            <p className="mt-6 text-gray-700 font-medium">
              Anizai implements a professional Data Engineering pipeline that enables:
            </p>
          </div>

          {/* Capabilities / Cards */}
          <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-14">
            <div className="group rounded-xl p-7 border border-blue-100 bg-gradient-to-b from-blue-50 to-white shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all">
              <Lightbulb className="w-9 h-9 text-blue-600 mb-4" />
              <h3 className="text-base font-semibold text-gray-900 mb-2">Contextual Signals</h3>
              <p className="text-gray-700 leading-relaxed">
                Finds patterns by connecting what happened before with what’s happening now. It leverages diverse data sources to provide a holistic view of evolving situations.
              </p>
            </div>

            <div className="group rounded-xl p-7 border border-blue-100 bg-gradient-to-b from-blue-50 to-white shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all">
              <Lightbulb className="w-9 h-9 text-blue-600 mb-4" />
              <h3 className="text-base font-semibold text-gray-900 mb-2">Reliability Scoring</h3>
              <p className="text-gray-700 leading-relaxed">
              Checks how reliable the information is, giving more weight to trusted expert sources.
              </p>
            </div>

            <div className="group rounded-xl p-7 border border-blue-100 bg-gradient-to-b from-blue-50 to-white shadow-sm hover:shadow-md hover:-translate-y-0.5 transition-all">
              <Lightbulb className="w-9 h-9 text-blue-600 mb-4" />
              <h3 className="text-base font-semibold text-gray-900 mb-2">Explainable Forecasts</h3>
              <p className="text-gray-700 leading-relaxed">
                Shows clear predictions with confidence levels, not just simple yes or no answers.
              </p>
            </div>
          </div>

          {/* Team Section */}
          <div className="mb-6">
            <h2 className="text-2xl font-semibold text-gray-900 mb-4">Our Team</h2>

            {/* Transition line that connects “tech” to “people” */}
            <p className="text-gray-700 mb-4 leading-relaxed">
              Built by a multidisciplinary team with backgrounds in data engineering, machine learning, and forecasting.
              Our focus is to combine scalable data infrastructure with transparent, evidence-driven reasoning.
            </p>

    
          </div>

          {/* Bottom CTA */}
          <div className="mt-12 rounded-2xl border border-gray-200 bg-white p-8 shadow-sm">
            <h3 className="text-xl font-semibold text-gray-900">
              Ready to explore evidence-based forecasting?
            </h3>
            <p className="mt-2 text-gray-600">
              Start a session or learn how our methodology works.
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
              <Button
                variant="outline"
                className="h-11 px-6"
                onClick={onMethodology}
                disabled={!onMethodology}
              >
                Methodology
              </Button>
            </div>
          </div>

          {/* Small footer note */}
          <p className="mt-8 text-xs text-gray-400">
            Note: Forecasts are probabilistic and provided for informational purposes.
          </p>
        </div>
      </div>
    </div>
  );
}
