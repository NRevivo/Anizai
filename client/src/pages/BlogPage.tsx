import { ArrowLeft } from 'lucide-react';

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
            excerpt: 'Explore how machine learning is revolutionizing the way we predict market trends and make business decisions.',
            category: 'AI & Technology'
        },
        {
            id: 2,
            title: 'Evidence-Based Decision Making in 2026',
            date: 'January 10, 2026',
            author: 'Michael Chen',
            excerpt: 'Learn why data-driven insights are becoming essential for competitive advantage in modern business.',
            category: 'Business Insights'
        },
        {
            id: 3,
            title: 'Understanding Forecast Accuracy Metrics',
            date: 'January 5, 2026',
            author: 'Emily Rodriguez',
            excerpt: 'A deep dive into how we measure and evaluate the accuracy of our forecasting models.',
            category: 'Technical Deep Dive'
        },
        {
            id: 4,
            title: 'Case Study: Improving Retail Forecasts',
            date: 'December 28, 2025',
            author: 'David Williams',
            excerpt: 'See how a major retailer improved inventory management using Anizai forecasts.',
            category: 'Case Studies'
        },
        {
            id: 5,
            title: 'Trends That Will Shape 2026',
            date: 'December 20, 2025',
            author: 'Lisa Park',
            excerpt: 'Our expert predictions for the major trends that will define the coming year.',
            category: 'Market Analysis'
        },
        {
            id: 6,
            title: 'Getting Started with Anizai',
            date: 'December 15, 2025',
            author: 'James Tucker',
            excerpt: 'A beginner\'s guide to using Anizai for your first forecast.',
            category: 'Getting Started'
        }
    ];

    return (
        <div className="min-h-screen bg-white">
            {/* Header */}
            <div className="bg-gray-50 border-b border-gray-200 px-6 py-6">
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

            {/* Main Content */}
            <div className="px-6 py-16">
                <div className="max-w-4xl mx-auto">
                    <div className="mb-12">
                        <h1 className="text-4xl font-bold text-gray-900 mb-4">Blog</h1>
                        <p className="text-xl text-gray-600">
                            Insights, updates, and deep dives into forecasting, AI, and data-driven decision making.
                        </p>
                    </div>

                    {/* Blog Posts Grid */}
                    <div className="space-y-6">
                        {posts.map((post) => (
                            <article
                                key={post.id}
                                className="border border-gray-200 rounded-lg p-8 hover:shadow-lg transition-shadow cursor-pointer"
                            >
                                <div className="flex items-start justify-between mb-3">
                                    <span className="inline-block px-3 py-1 bg-blue-100 text-blue-700 text-sm font-medium rounded-full">
                                        {post.category}
                                    </span>
                                    <span className="text-sm text-gray-500">{post.date}</span>
                                </div>
                                <h2 className="text-2xl font-semibold text-gray-900 mb-3 hover:text-blue-600">
                                    {post.title}
                                </h2>
                                <p className="text-gray-600 mb-4">{post.excerpt}</p>
                                <div className="flex items-center justify-between">
                                    <span className="text-sm text-gray-500">By {post.author}</span>
                                    <span className="text-blue-600 font-medium hover:underline">Read more →</span>
                                </div>
                            </article>
                        ))}
                    </div>
                </div>
            </div>
        </div>
    );
}
