import { ArrowLeft, Shield, Lock, Eye } from 'lucide-react';

interface PrivacyPageProps {
    onBack?: () => void;
}

export function PrivacyPage({ onBack }: PrivacyPageProps) {
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
                        <h1 className="text-4xl font-bold text-gray-900 mb-2">Privacy Policy</h1>
                        <p className="text-gray-500">Last updated: January 2026</p>
                    </div>

                    {/* Privacy Highlights */}
                    <div className="grid grid-cols-1 md:grid-cols-3 gap-6 mb-12">
                        <div className="bg-blue-50 rounded-lg p-6 border border-blue-200">
                            <Shield className="w-8 h-8 text-blue-600 mb-3" />
                            <h3 className="font-semibold text-gray-900 mb-2">Your Data is Protected</h3>
                            <p className="text-sm text-gray-700">Enterprise-grade encryption secures all your information.</p>
                        </div>
                        <div className="bg-blue-50 rounded-lg p-6 border border-blue-200">
                            <Lock className="w-8 h-8 text-blue-600 mb-3" />
                            <h3 className="font-semibold text-gray-900 mb-2">We Never Sell Your Data</h3>
                            <p className="text-sm text-gray-700">Your data is yours alone and will never be sold to third parties.</p>
                        </div>
                        <div className="bg-blue-50 rounded-lg p-6 border border-blue-200">
                            <Eye className="w-8 h-8 text-blue-600 mb-3" />
                            <h3 className="font-semibold text-gray-900 mb-2">Full Transparency</h3>
                            <p className="text-sm text-gray-700">You have complete visibility and control over your data.</p>
                        </div>
                    </div>

                    {/* Content */}
                    <div className="prose prose-lg max-w-none">
                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">1. Introduction</h2>
                            <p className="text-gray-700 mb-4">
                                Anizai Inc. ("we", "us", "our" or "Company") operates the Anizai website. This page informs you of our policies regarding the collection, use, and disclosure of personal data when you use our Service and the choices you have associated with that data.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">2. Information Collection and Use</h2>
                            <p className="text-gray-700 mb-4">
                                We collect several different types of information for various purposes to provide and improve our Service to you.
                            </p>
                            <h3 className="text-lg font-semibold text-gray-900 mb-2">Personal Data</h3>
                            <p className="text-gray-700 mb-4">
                                While using our Service, we may ask you to provide us with certain personally identifiable information that can be used to contact or identify you ("Personal Data"). This may include, but is not limited to:
                            </p>
                            <ul className="list-disc pl-6 text-gray-700 space-y-2 mb-4">
                                <li>Email address</li>
                                <li>First name and last name</li>
                                <li>Phone number</li>
                                <li>Address, State, Province, ZIP/Postal code, City</li>
                                <li>Cookies and Usage Data</li>
                            </ul>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">3. Use of Data</h2>
                            <p className="text-gray-700 mb-4">
                                Anizai uses the collected data for various purposes:
                            </p>
                            <ul className="list-disc pl-6 text-gray-700 space-y-2 mb-4">
                                <li>To provide and maintain our Service</li>
                                <li>To notify you about changes to our Service</li>
                                <li>To allow you to participate in interactive features of our Service</li>
                                <li>To provide customer support</li>
                                <li>To gather analysis or valuable information so that we can improve our Service</li>
                                <li>To monitor the usage of our Service</li>
                                <li>To detect, prevent and address technical issues</li>
                            </ul>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">4. Security of Data</h2>
                            <p className="text-gray-700 mb-4">
                                The security of your data is important to us but remember that no method of transmission over the Internet or method of electronic storage is 100% secure. While we strive to use commercially acceptable means to protect your Personal Data, we cannot guarantee its absolute security.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">5. Changes to This Privacy Policy</h2>
                            <p className="text-gray-700 mb-4">
                                We may update our Privacy Policy from time to time. We will notify you of any changes by posting the new Privacy Policy on this page and updating the "effective date" at the top of this Privacy Policy.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">6. Contact Us</h2>
                            <p className="text-gray-700 mb-4">
                                If you have any questions about this Privacy Policy, please contact us at privacy@anizai.com.
                            </p>
                        </section>
                    </div>
                </div>
            </div>
        </div>
    );
}
