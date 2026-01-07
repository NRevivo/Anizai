export function WhoItsFor() {
    const personas = [
        {
            title: 'Prediction Market Users',
            description: 'Make more informed decisions with transparent, evidence-based probability assessments.',
            icon: (
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M13 7h8m0 0v8m0-8l-8 8-4-4-6 6" />
                </svg>
            )
        },
        {
            title: 'Analysts & Researchers',
            description: 'Access structured, real-time data on emerging events and their likelihood.',
            icon: (
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 19v-6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2a2 2 0 002-2zm0 0V9a2 2 0 012-2h2a2 2 0 012 2v10m-6 0a2 2 0 002 2h2a2 2 0 002-2m0 0V5a2 2 0 012-2h2a2 2 0 012 2v14a2 2 0 01-2 2h-2a2 2 0 01-2-2z" />
                </svg>
            )
        },
        {
            title: 'Curious Minds',
            description: 'Understand complex future events through clear, analytical forecasting.',
            icon: (
                <svg className="w-6 h-6" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
                </svg>
            )
        }
    ];

    return (
        <section className="w-full bg-gray-50 py-24 px-6">
            <div className="max-w-5xl mx-auto">
                <div className="text-center mb-16">
                    <h2 className="text-3xl font-semibold text-gray-900 mb-4">
                        Who It's For
                    </h2>
                    <p className="text-lg text-gray-600">
                        Anizai serves anyone seeking clarity about future events
                    </p>
                </div>

                <div className="grid grid-cols-1 md:grid-cols-3 gap-8">
                    {personas.map((persona, index) => (
                        <div key={index} className="bg-white rounded-lg border border-gray-200 p-6 shadow-sm">
                            <div className="w-12 h-12 bg-gradient-to-br from-anizai-teal-100 to-anizai-blue-100 rounded-lg flex items-center justify-center mb-4 text-anizai-teal-600">
                                {persona.icon}
                            </div>
                            <h3 className="text-lg font-semibold text-gray-900 mb-2">
                                {persona.title}
                            </h3>
                            <p className="text-sm text-gray-600 leading-relaxed">
                                {persona.description}
                            </p>
                        </div>
                    ))}
                </div>
            </div>
        </section>
    );
}
