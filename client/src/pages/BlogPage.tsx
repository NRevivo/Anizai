import { ArrowLeft, Calendar, User } from 'lucide-react';

interface BlogPageProps {
  onBack?: () => void;
}

export function BlogPage({ onBack }: BlogPageProps) {
  const posts = [
    {
      id: 1,
      title: 'The Future of AI-Powered Forecasting',
      date: 'January 15, 2026',
      author: 'Sarah Johnson',
      excerpt:
        'Explore how machine learning is reshaping forecasting and enabling better, evidence-based decisions.',
      category: 'AI & Technology',
    },
    {
      id: 2,
      title: 'Evidence-Based Decision Making in 2026',
      date: 'January 10, 2026',
      author: 'Michael Chen',
      excerpt:
        'Why probabilistic thinking and data-driven insights are becoming critical for modern organizations.',
      category: 'Business Insights',
    },
    {
      id: 3,
      title: 'Understanding Forecast Accuracy Metrics',
      date: 'January 5, 2026',
      author: 'Emily Rodriguez',
      excerpt:
        'A practical explanation of how forecasting accuracy is measured and evaluated.',
      category: 'Technical Deep Dive',
    },
    {
      id: 4,
      title: 'Case Study: Improving Retail Forecasts',
      date: 'December 28, 2025',
      author: 'David Williams',
      excerpt:
        'How a retail company improved planning and inventory using probabilistic forecasts.',
      category: 'Case Studies',
    },
  ];

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
              <span className="h-2 w-2 rounded-full bg-anizai-blue-600" />
              Blog
            </div>

            <h1 className="text-4xl sm:text-5xl font-bold tracking-tight text-gray-900">
              Insights from{' '}
              <span className="bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                Anizai
              </span>
            </h1>

            <p className="mt-4 text-lg text-gray-600 max-w-2xl">
              Articles, updates, and deep dives into forecasting, AI, and evidence-based decision making.
            </p>
          </div>
        </div>
      </section>

      {/* Posts */}
      <div className="px-6 pb-16">
        <div className="max-w-4xl mx-auto grid grid-cols-1 gap-6">
          {posts.map((post) => (
            <article
              key={post.id}
              className="group rounded-2xl border border-gray-200 bg-white p-7 shadow-sm hover:shadow-lg transition-all cursor-pointer"
            >
              <div className="flex items-center justify-between mb-4">
                <span className="inline-flex items-center rounded-full bg-blue-50 border border-blue-100 px-3 py-1 text-xs font-medium text-blue-700">
                  {post.category}
                </span>
                <div className="flex items-center gap-2 text-xs text-gray-500">
                  <Calendar className="w-4 h-4" />
                  {post.date}
                </div>
              </div>

              <h2 className="text-xl sm:text-2xl font-semibold text-gray-900 group-hover:text-anizai-blue-600 transition-colors">
                {post.title}
              </h2>

              <p className="mt-3 text-gray-600 leading-relaxed">
                {post.excerpt}
              </p>

              <div className="mt-6 flex items-center justify-between">
                <div className="flex items-center gap-2 text-sm text-gray-500">
                  <User className="w-4 h-4" />
                  {post.author}
                </div>
                <span className="text-sm font-medium text-anizai-blue-600 group-hover:underline">
                  Read more →
                </span>
              </div>
            </article>
          ))}
        </div>
      </div>
    </div>
  );
}
