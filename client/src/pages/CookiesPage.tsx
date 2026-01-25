import { ArrowLeft, Cookie, Settings } from 'lucide-react';

interface CookiesPageProps {
    onBack?: () => void;
}

export function CookiesPage({ onBack }: CookiesPageProps) {
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
                        <h1 className="text-4xl font-bold text-gray-900 mb-2">Cookie Policy</h1>
                        <p className="text-gray-500">Last updated: January 2026</p>
                    </div>

                    {/* Content */}
                    <div className="prose prose-lg max-w-none">
                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">What Are Cookies?</h2>
                            <p className="text-gray-700 mb-4">
                                Cookies are small pieces of text stored on your device when you visit our website. They help us provide you with a better experience by remembering your preferences and tracking how you use our site.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">Types of Cookies We Use</h2>
                            <div className="space-y-6">
                                <div className="border-l-4 border-blue-600 pl-6">
                                    <h3 className="text-lg font-semibold text-gray-900 mb-2">Essential Cookies</h3>
                                    <p className="text-gray-700">
                                        These cookies are necessary for the website to function properly. They enable core functionality such as security, network management, and accessibility.
                                    </p>
                                </div>
                                <div className="border-l-4 border-blue-600 pl-6">
                                    <h3 className="text-lg font-semibold text-gray-900 mb-2">Performance Cookies</h3>
                                    <p className="text-gray-700">
                                        These cookies collect information about how you use our website, such as which pages you visit and any errors you encounter. This data helps us improve our site performance.
                                    </p>
                                </div>
                                <div className="border-l-4 border-blue-600 pl-6">
                                    <h3 className="text-lg font-semibold text-gray-900 mb-2">Functional Cookies</h3>
                                    <p className="text-gray-700">
                                        These cookies enable personalization of our website, such as remembering your preferences, language selection, and login information.
                                    </p>
                                </div>
                                <div className="border-l-4 border-blue-600 pl-6">
                                    <h3 className="text-lg font-semibold text-gray-900 mb-2">Marketing Cookies</h3>
                                    <p className="text-gray-700">
                                        These cookies track your browsing habits and preferences to deliver targeted advertising and measure campaign effectiveness.
                                    </p>
                                </div>
                            </div>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">Your Choices</h2>
                            <p className="text-gray-700 mb-4">
                                You have the right to decide whether to accept or reject cookies. You can configure your browser to refuse all cookies or to alert you when cookies are being sent. However, please note that some parts of our website may not function properly if you choose to disable cookies.
                            </p>
                            <div className="bg-blue-50 rounded-lg p-6 border border-blue-200 mt-4">
                                <div className="flex items-start gap-3">
                                    <Settings className="w-6 h-6 text-blue-600 mt-1 flex-shrink-0" />
                                    <div>
                                        <h4 className="font-semibold text-gray-900 mb-2">Cookie Settings</h4>
                                        <p className="text-gray-700 text-sm mb-4">
                                            You can manage your cookie preferences and opt-out of non-essential cookies at any time through your browser settings or by updating your preferences in your account.
                                        </p>
                                    </div>
                                </div>
                            </div>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">Third-Party Cookies</h2>
                            <p className="text-gray-700 mb-4">
                                We may allow third-party service providers to place cookies on your device for analytics, advertising, and other purposes. These providers have their own privacy policies governing their use of cookies.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">How Long Do Cookies Last?</h2>
                            <p className="text-gray-700 mb-4">
                                Cookies can be either persistent (lasting until a set expiration date) or session-based (deleted when you close your browser). The duration depends on the specific cookie's purpose.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">Contact Us</h2>
                            <p className="text-gray-700 mb-4">
                                If you have questions about our use of cookies, please contact us at privacy@anizai.com.
                            </p>
                        </section>
                    </div>
                </div>
            </div>
        </div>
    );
}
