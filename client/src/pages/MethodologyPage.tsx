import { ArrowLeft, CheckCircle, Database, Cpu, Search, BarChart3, ShieldCheck, RefreshCw } from 'lucide-react';

interface MethodologyPageProps {
  onBack?: () => void;
}

export function MethodologyPage({ onBack }: MethodologyPageProps) {
  return (
    <div className="min-h-screen bg-white">
      {/* Header (sticky like About/Contact) */}
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
              <span className="h-2 w-2 rounded-full bg-anizai-purple-500" />
              Methodology
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              Our{' '}
              <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                Methodology
              </span>
            </h1>

            <p className="mt-4 text-lg sm:text-xl text-gray-600 max-w-2xl">
              How we deliver evidence-based forecasting using a clear pipeline and explainable outputs.
            </p>

            {/* Quick highlights */}
            <div className="mt-8 grid grid-cols-1 sm:grid-cols-3 gap-4">
              <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                <p className="text-xs text-gray-500">Focus</p>
                <p className="text-sm font-semibold text-gray-900">Evidence over guesses</p>
              </div>
              <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                <p className="text-xs text-gray-500">Output</p>
                <p className="text-sm font-semibold text-gray-900">Probabilities + confidence</p>
              </div>
              <div className="rounded-xl border border-gray-200 bg-white p-4 shadow-sm">
                <p className="text-xs text-gray-500">Approach</p>
                <p className="text-sm font-semibold text-gray-900">RAG + real-time data</p>
              </div>
            </div>
          </div>
        </div>
      </section>

      {/* Main */}
      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto">
          {/* Pipeline section */}
          <div className="mt-10 mb-8">
            <h2 className="text-2xl font-semibold text-gray-900">The Forecasting Pipeline</h2>
            <p className="mt-2 text-gray-600">
              A simple, repeatable process: collect signals, process them, ground predictions in evidence, and improve over time.
            </p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            {/* Step 1 */}
            <div className="group rounded-2xl border border-gray-200 bg-white p-7 shadow-sm hover:shadow-md transition-all">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-anizai-teal-50 border border-anizai-teal-100">
                    <Database className="h-5 w-5 text-anizai-teal-600" />
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">Step 1</p>
                    <h3 className="text-lg font-semibold text-gray-900">Data Collection</h3>
                  </div>
                </div>
                <div className="text-xs font-semibold text-gray-400">01</div>
              </div>
              <p className="mt-4 text-gray-700 leading-relaxed">
                We gather signals from multiple sources (news, market data, social sentiment, and expert commentary) to build a broad
                and up-to-date dataset.
              </p>
            </div>

            {/* Step 2 */}
            <div className="group rounded-2xl border border-gray-200 bg-white p-7 shadow-sm hover:shadow-md transition-all">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-anizai-blue-50 border border-anizai-blue-100">
                    <Cpu className="h-5 w-5 text-anizai-blue-600" />
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">Step 2</p>
                    <h3 className="text-lg font-semibold text-gray-900">Processing & Enrichment</h3>
                  </div>
                </div>
                <div className="text-xs font-semibold text-gray-400">02</div>
              </div>
              <p className="mt-4 text-gray-700 leading-relaxed">
                We clean, normalize, and enrich incoming data so it can be searched, compared, and used consistently across sessions.
              </p>
            </div>

            {/* Step 3 */}
            <div className="group rounded-2xl border border-gray-200 bg-white p-7 shadow-sm hover:shadow-md transition-all">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-anizai-purple-50 border border-anizai-purple-100">
                    <Search className="h-5 w-5 text-anizai-purple-600" />
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">Step 3</p>
                    <h3 className="text-lg font-semibold text-gray-900">Evidence Retrieval (RAG)</h3>
                  </div>
                </div>
                <div className="text-xs font-semibold text-gray-400">03</div>
              </div>
              <p className="mt-4 text-gray-700 leading-relaxed">
                When a user creates a forecast, we retrieve the most relevant evidence and context before generating any output.
              </p>
            </div>

            {/* Step 4 */}
            <div className="group rounded-2xl border border-gray-200 bg-white p-7 shadow-sm hover:shadow-md transition-all">
              <div className="flex items-start justify-between gap-4">
                <div className="flex items-center gap-3">
                  <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-gray-50 border border-gray-200">
                    <BarChart3 className="h-5 w-5 text-gray-700" />
                  </div>
                  <div>
                    <p className="text-xs text-gray-500">Step 4</p>
                    <h3 className="text-lg font-semibold text-gray-900">Forecast & Explanation</h3>
                  </div>
                </div>
                <div className="text-xs font-semibold text-gray-400">04</div>
              </div>
              <p className="mt-4 text-gray-700 leading-relaxed">
                We produce a probability-based forecast and an explanation showing the key drivers, confidence level, and uncertainty.
              </p>
            </div>
          </div>

          {/* Continuous learning banner */}
          <div className="mt-8 rounded-2xl border border-gray-200 bg-gradient-to-r from-gray-50 via-white to-gray-50 p-7 shadow-sm">
            <div className="flex items-start gap-4">
              <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-green-50 border border-green-100">
                <RefreshCw className="h-5 w-5 text-green-700" />
              </div>
              <div>
                <h3 className="text-lg font-semibold text-gray-900">Continuous Learning</h3>
                <p className="mt-1 text-gray-700 leading-relaxed">
                  We track outcomes and feedback to improve future forecasts. Over time, the system learns what signals matter most.
                </p>
              </div>
            </div>
          </div>

          {/* Principles */}
          <div className="mt-14 mb-6">
            <h2 className="text-2xl font-semibold text-gray-900">Key Principles</h2>
            <p className="mt-2 text-gray-600">The rules we follow to keep forecasts trustworthy and useful.</p>
          </div>

          <div className="grid grid-cols-1 md:grid-cols-2 gap-6">
            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-start gap-3">
                <CheckCircle className="w-5 h-5 text-anizai-blue-600 mt-0.5" />
                <div>
                  <p className="font-semibold text-gray-900">Transparency</p>
                  <p className="text-gray-600 mt-1">Predictions come with evidence and clear reasoning.</p>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-start gap-3">
                <CheckCircle className="w-5 h-5 text-anizai-teal-600 mt-0.5" />
                <div>
                  <p className="font-semibold text-gray-900">Accuracy</p>
                  <p className="text-gray-600 mt-1">We validate results and monitor performance over time.</p>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-start gap-3">
                <ShieldCheck className="w-5 h-5 text-anizai-purple-600 mt-0.5" />
                <div>
                  <p className="font-semibold text-gray-900">Objectivity</p>
                  <p className="text-gray-600 mt-1">We aim to reduce bias by relying on measurable signals and evidence.</p>
                </div>
              </div>
            </div>

            <div className="rounded-2xl border border-gray-200 bg-white p-6 shadow-sm hover:shadow-md transition-shadow">
              <div className="flex items-start gap-3">
                <RefreshCw className="w-5 h-5 text-gray-700 mt-0.5" />
                <div>
                  <p className="font-semibold text-gray-900">Continuous Improvement</p>
                  <p className="text-gray-600 mt-1">We refine the pipeline based on feedback and observed outcomes.</p>
                </div>
              </div>
            </div>
          </div>

          {/* Small note */}
          <p className="mt-10 text-xs text-gray-400">
            Note: Forecasts are probabilistic and provided for informational purposes.
          </p>
        </div>
      </div>
    </div>
  );
}
