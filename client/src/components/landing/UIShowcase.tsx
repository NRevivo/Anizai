export function UIShowcase() {
    return (
        <section className="w-full bg-gray-50 py-24 px-6 overflow-hidden">
            <div className="max-w-6xl mx-auto">
                <div className="text-center mb-16">
                    <h2 className="text-3xl font-semibold text-gray-900 mb-4">
                        Comprehensive Analysis Dashboard
                    </h2>
                    <p className="text-lg text-gray-600 max-w-2xl mx-auto">
                        Track predictions with real-time evidence, confidence metrics, and interactive insights in a unified workspace.
                    </p>
                </div>

                {/* Dashboard Preview Container with Browser Frame */}
                <div className="relative rounded-xl border border-gray-200 bg-white shadow-2xl overflow-hidden transform transition-transform hover:scale-[1.005] duration-500">
                    {/* Browser Header / Traffic Lights */}
                    <div className="h-12 bg-gray-50 border-b border-gray-200 flex items-center px-4 gap-2">
                        <div className="w-3 h-3 rounded-full bg-[#FF5F56] border border-[#E0443E]"></div>
                        <div className="w-3 h-3 rounded-full bg-[#FFBD2E] border border-[#DEA123]"></div>
                        <div className="w-3 h-3 rounded-full bg-[#27C93F] border border-[#1AAB29]"></div>

                        {/* Fake URL Bar */}
                        <div className="ml-4 flex-1 max-w-sm h-7 bg-white border border-gray-200 rounded text-xs flex items-center px-3 text-gray-400 font-mono">
                            app.anizai.com/dashboard
                        </div>
                    </div>

                    <div className="p-8 bg-gray-50">
                        {/* Mock Dashboard Header */}
                        <div className="mb-8 flex items-start justify-between">
                            <div>
                                <h3 className="text-2xl font-semibold bg-gradient-to-r from-anizai-teal-700 via-anizai-blue-700 to-anizai-purple-700 bg-clip-text text-transparent mb-2 leading-tight">
                                    Will AI regulation pass in the EU by Q2 2026?
                                </h3>
                                <div className="flex items-center gap-2">
                                    <span className="inline-flex items-center px-2 py-0.5 rounded text-xs font-medium bg-green-100 text-green-800">
                                        Active
                                    </span>
                                    <p className="text-sm text-gray-500">Last updated 06/01/2026, 10:30:00</p>
                                </div>
                            </div>
                            <div className="hidden sm:block">
                                <div className="h-10 w-32 bg-white border border-gray-200 rounded-lg shadow-sm"></div>
                            </div>
                        </div>

                        {/* Mock Cards Grid */}
                        <div className="grid grid-cols-1 lg:grid-cols-2 gap-6">
                            {/* Probability Gauge Mock - Enhanced */}
                            <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm hover:shadow-md transition-shadow">
                                <div className="flex justify-between items-start mb-6">
                                    <div>
                                        <h4 className="text-sm font-semibold text-gray-900">Prediction Overview</h4>
                                        <p className="text-xs text-gray-500 mt-1">Current market probability</p>
                                    </div>
                                    <div className="h-5 w-5 text-gray-300">
                                        <svg fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 16h-1v-4h-1m1-4h.01M21 12a9 9 0 11-18 0 9 9 0 0118 0z" /></svg>
                                    </div>
                                </div>

                                <div className="flex items-center justify-center py-4">
                                    <div className="relative">
                                        <svg className="transform -rotate-90" width="160" height="160">
                                            <circle cx="80" cy="80" r="70" stroke="#f3f4f6" strokeWidth="12" fill="none" />
                                            <circle
                                                cx="80" cy="80" r="70"
                                                stroke="url(#mockGradient)"
                                                strokeWidth="12"
                                                fill="none"
                                                strokeDasharray="440"
                                                strokeDashoffset="120"
                                                strokeLinecap="round"
                                                style={{ filter: 'drop-shadow(0 0 6px rgba(59, 130, 246, 0.3))' }}
                                            />
                                            <defs>
                                                <linearGradient id="mockGradient" x1="0%" y1="0%" x2="100%" y2="100%">
                                                    <stop offset="0%" stopColor="rgb(20, 184, 166)" />
                                                    <stop offset="50%" stopColor="rgb(59, 130, 246)" />
                                                    <stop offset="100%" stopColor="rgb(168, 85, 247)" />
                                                </linearGradient>
                                            </defs>
                                        </svg>
                                        <div className="absolute inset-0 flex flex-col items-center justify-center">
                                            <span className="text-4xl font-bold bg-gradient-to-r from-anizai-teal-600 via-anizai-blue-600 to-anizai-purple-600 bg-clip-text text-transparent">
                                                72.3%
                                            </span>
                                            <span className="text-sm font-medium text-gray-500 mt-1">Probability</span>
                                        </div>
                                    </div>
                                </div>

                                <div className="mt-4 pt-4 border-t border-gray-100 flex justify-between items-center text-xs text-gray-500">
                                    <span>Confidence Score: <span className="font-semibold text-gray-700">84/100</span></span>
                                    <span className="text-green-600 font-medium flex items-center">
                                        <svg className="w-3 h-3 mr-1" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" /></svg>
                                        +2.4% today
                                    </span>
                                </div>
                            </div>

                            {/* Evidence Timeline Mock - Enhanced */}
                            <div className="bg-white border border-gray-200 rounded-xl p-6 shadow-sm hover:shadow-md transition-shadow">
                                <div className="flex justify-between items-start mb-6">
                                    <div>
                                        <h4 className="text-sm font-semibold text-gray-900">Evidence Timeline</h4>
                                        <p className="text-xs text-gray-500 mt-1">Key events driving the forecast</p>
                                    </div>
                                </div>

                                <div className="space-y-6 relative">
                                    {/* Vertical Line */}
                                    <div className="absolute left-3 top-2 bottom-0 w-0.5 bg-gray-100"></div>

                                    <div className="relative pl-10">
                                        <div className="absolute left-0 top-1 w-6 h-6 rounded-full bg-anizai-teal-50 border border-anizai-teal-200 flex items-center justify-center z-10">
                                            <div className="w-2 h-2 rounded-full bg-anizai-teal-500"></div>
                                        </div>
                                        <div className="p-3 rounded-lg bg-gray-50 border border-gray-100 hover:bg-white hover:border-anizai-teal-200 transition-colors cursor-default">
                                            <p className="text-xs font-semibold text-gray-900">EU Parliament committee vote</p>
                                            <p className="text-xs text-gray-600 mt-1 line-clamp-2">Internal market committee votes strongly in favor of the compromise text, signaling smooth passage.</p>
                                            <p className="text-[10px] text-gray-400 mt-2 flex items-center gap-1">
                                                <span className="uppercase tracking-wide font-medium">Reuters</span>
                                                <span>•</span>
                                                <span>11h ago</span>
                                            </p>
                                        </div>
                                    </div>

                                    <div className="relative pl-10">
                                        <div className="absolute left-0 top-1 w-6 h-6 rounded-full bg-gray-50 border border-gray-200 flex items-center justify-center z-10">
                                            <div className="w-2 h-2 rounded-full bg-gray-400"></div>
                                        </div>
                                        <div className="p-3 rounded-lg bg-gray-50 border border-gray-100 cursor-default">
                                            <p className="text-xs font-semibold text-gray-900">Industry lobbying concerns</p>
                                            <p className="text-xs text-gray-600 mt-1 line-clamp-1">Major tech coalition releases letter warning of innovation drag.</p>
                                            <p className="text-[10px] text-gray-400 mt-2 flex items-center gap-1">
                                                <span className="uppercase tracking-wide font-medium">Financial Times</span>
                                                <span>•</span>
                                                <span>1d ago</span>
                                            </p>
                                        </div>
                                    </div>
                                </div>
                            </div>
                        </div>
                    </div>
                </div>
            </div>
        </section>
    );
}
