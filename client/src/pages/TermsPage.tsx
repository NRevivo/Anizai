import { ArrowLeft, CheckCircle } from 'lucide-react';

interface TermsPageProps {
    onBack?: () => void;
}

export function TermsPage({ onBack }: TermsPageProps) {
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
                        <h1 className="text-4xl font-bold text-gray-900 mb-2">Terms of Service</h1>
                        <p className="text-gray-500">Last updated: January 2026</p>
                    </div>

                    {/* Content */}
                    <div className="prose prose-lg max-w-none">
                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">1. Acceptance of Terms</h2>
                            <p className="text-gray-700 mb-4">
                                By accessing and using the Anizai platform, you accept and agree to be bound by the terms and provision of this agreement. If you do not agree to abide by the above, please do not use this service.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">2. Use License</h2>
                            <p className="text-gray-700 mb-4">
                                Permission is granted to temporarily download one copy of the materials (information or software) on Anizai for personal, non-commercial transitory viewing only. This is the grant of a license, not a transfer of title, and under this license you may not:
                            </p>
                            <ul className="list-disc pl-6 text-gray-700 space-y-2">
                                <li>Modify or copy the materials</li>
                                <li>Use the materials for any commercial purpose or for any public display</li>
                                <li>Attempt to decompile or reverse engineer any software contained on the platform</li>
                                <li>Remove any copyright or other proprietary notations from the materials</li>
                                <li>Transmit the materials to another person or "mirror" the materials on any other server</li>
                            </ul>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">3. Disclaimer</h2>
                            <p className="text-gray-700 mb-4">
                                The materials on Anizai are provided on an 'as is' basis. Anizai makes no warranties, expressed or implied, and hereby disclaims and negates all other warranties including, without limitation, implied warranties or conditions of merchantability, fitness for a particular purpose, or non-infringement of intellectual property or other violation of rights.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">4. Limitations</h2>
                            <p className="text-gray-700 mb-4">
                                In no event shall Anizai or its suppliers be liable for any damages (including, without limitation, damages for loss of data or profit, or due to business interruption) arising out of the use or inability to use the materials on Anizai.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">5. Accuracy of Materials</h2>
                            <p className="text-gray-700 mb-4">
                                The materials appearing on Anizai could include technical, typographical, or photographic errors. Anizai does not warrant that any of the materials on the platform are accurate, complete, or current. Anizai may make changes to the materials contained on the platform at any time without notice.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">6. Links</h2>
                            <p className="text-gray-700 mb-4">
                                Anizai has not reviewed all of the sites linked to its website and is not responsible for the contents of any such linked site. The inclusion of any link does not imply endorsement by Anizai of the site. Use of any such linked website is at the user's own risk.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">7. Modifications</h2>
                            <p className="text-gray-700 mb-4">
                                Anizai may revise these terms of service for the platform at any time without notice. By using this platform, you are agreeing to be bound by the then current version of these terms of service.
                            </p>
                        </section>

                        <section className="mb-8">
                            <h2 className="text-2xl font-semibold text-gray-900 mb-4">8. Governing Law</h2>
                            <p className="text-gray-700 mb-4">
                                These terms and conditions are governed by and construed in accordance with the laws of the jurisdiction in which Anizai operates, and you irrevocably submit to the exclusive jurisdiction of the courts in that location.
                            </p>
                        </section>
                    </div>
                </div>
            </div>
        </div>
    );
}
