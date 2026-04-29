interface FooterProps {
    onAuth?: () => void;
    onContact?: () => void;
    onNavigation?: {
        features: () => void;
        methodology: () => void;
        changelog: () => void;
        about: () => void;
        blog: () => void;
        terms: () => void;
        privacy: () => void;
        cookies: () => void;
        pricing: () => void;
    };
}

export function Footer({ onAuth: _onAuth, onContact, onNavigation }: FooterProps) {
    return (
        <footer className="bg-gray-50 border-t border-gray-200 pt-16 pb-12 px-6">
            <div className="max-w-6xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-8 mb-12">
                <div className="col-span-2 md:col-span-1">
                    <div className="flex items-center gap-2 mb-6">
                        <img src="/logo-brain.png" alt="Anizai" className="h-8 w-auto opacity-80" />
                        <span className="text-xl font-semibold text-gray-900 tracking-tight">Anizai</span>
                    </div>
                    <p className="text-sm text-gray-500 mb-6 max-w-xs">
                        Evidence-based forecasting for a complex world. Predict with confidence.
                    </p>

                </div>

                {/* Links Column 1 */}
                <div>
                    <h3 className="text-sm font-semibold text-gray-900 tracking-wider uppercase mb-4">Product</h3>
                    <ul className="space-y-3">
                        <li>
                            <button
                                onClick={() => onNavigation?.features()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Features
                            </button>
                        </li>
                        <li>
                            <button
                                onClick={() => onNavigation?.methodology()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Methodology
                            </button>
                        </li>
                        <li>
                            <button
                                onClick={() => onNavigation?.pricing()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Pricing
                            </button>
                        </li>
                        <li>
                            <button
                                onClick={() => onNavigation?.changelog()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Changelog
                            </button>
                        </li>
                    </ul>
                </div>

                {/* Links Column 2 */}
                <div>
                    <h3 className="text-sm font-semibold text-gray-900 tracking-wider uppercase mb-4">Company</h3>
                    <ul className="space-y-3">
                        <li>
                            <button
                                onClick={() => onNavigation?.about()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                About
                            </button>
                        </li>
                        <li>
                            <button
                                onClick={() => onNavigation?.blog()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Blog
                            </button>
                        </li>
                        <li>
                            <button
                                onClick={() => onContact?.()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Contact
                            </button>
                        </li>
                    </ul>
                </div>

                {/* Links Column 3 */}
                <div>
                    <h3 className="text-sm font-semibold text-gray-900 tracking-wider uppercase mb-4">Legal</h3>
                    <ul className="space-y-3">
                        <li>
                            <button
                                onClick={() => onNavigation?.terms()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Terms
                            </button>
                        </li>
                        <li>
                            <button
                                onClick={() => onNavigation?.privacy()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Privacy
                            </button>
                        </li>
                        <li>
                            <button
                                onClick={() => onNavigation?.cookies()}
                                className="text-sm text-gray-500 hover:text-gray-900 transition-colors text-left"
                            >
                                Cookies
                            </button>
                        </li>
                    </ul>
                </div>
            </div>

            <div className="max-w-6xl mx-auto pt-8 border-t border-gray-200 flex flex-col md:flex-row justify-between items-center gap-4">
                <p className="text-sm text-gray-400">© 2026 Anizai Inc. All rights reserved.</p>
                <div className="flex items-center gap-2">
                    <div className="w-2 h-2 rounded-full bg-green-500 animate-pulse"></div>
                    <span className="text-sm text-gray-500 font-medium">Systems Operational</span>
                </div>
            </div>
        </footer>
    );
}
