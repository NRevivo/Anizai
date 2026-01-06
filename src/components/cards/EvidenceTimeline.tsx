import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import type { TimelineEvent } from '../../types';

interface EvidenceTimelineProps {
    events: TimelineEvent[];
}

const sourceIcons = {
    news: (
        <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M19 20H5a2 2 0 01-2-2V6a2 2 0 012-2h10a2 2 0 012 2v1m2 13a2 2 0 01-2-2V7m2 13a2 2 0 002-2V9a2 2 0 00-2-2h-2m-4-3H9M7 16h6M7 8h6v4H7V8z" />
        </svg>
    ),
    expert: (
        <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z" />
        </svg>
    ),
    social: (
        <svg className="w-4 h-4 text-gray-400" fill="none" stroke="currentColor" viewBox="0 0 24 24">
            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M17 8h2a2 2 0 012 2v6a2 2 0 01-2 2h-2v4l-4-4H9a1.994 1.994 0 01-1.414-.586m0 0L11 14h4a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v6a2 2 0 002 2h2v4l.586-.586z" />
        </svg>
    ),
};

export function EvidenceTimeline({ events }: EvidenceTimelineProps) {
    return (
        <Card>
            <CardHeader>
                <CardTitle>Evidence Timeline</CardTitle>
                <CardDescription>Key events and their impact on the prediction</CardDescription>
            </CardHeader>
            <CardContent>
                <div className="relative">
                    {/* Gradient timeline line */}
                    <div className="absolute left-0 top-0 bottom-0 w-0.5 bg-gradient-to-b from-anizai-teal-300 via-anizai-blue-300 to-anizai-purple-300" />

                    {/* Events */}
                    <div className="space-y-6">
                        {events.map((event) => (
                            <div key={event.id} className="relative pl-6">
                                {/* Gradient dot for positive, gray for others */}
                                <div className={`absolute left-0 top-1.5 w-2 h-2 rounded-full ${event.impact === 'positive'
                                        ? 'bg-gradient-to-br from-anizai-teal-500 to-anizai-blue-500'
                                        : event.impact === 'negative'
                                            ? 'bg-gray-400'
                                            : 'bg-gray-300'
                                    }`} />

                                {/* Neutral card with subtle gradient accent for positive */}
                                <div className={`p-4 rounded-lg bg-white border ${event.impact === 'positive'
                                        ? 'border-l-2 border-l-anizai-teal-400 border-gray-200'
                                        : event.impact === 'negative'
                                            ? 'border-l-2 border-l-gray-400 border-gray-200'
                                            : 'border-gray-200'
                                    }`}>
                                    <div className="flex items-start justify-between mb-2">
                                        <div className="flex items-center gap-2">
                                            {sourceIcons[event.sourceType]}
                                            <span className="text-xs font-medium text-gray-500 uppercase tracking-wide">
                                                {event.sourceType}
                                            </span>
                                        </div>
                                        <span className="text-xs text-gray-400">
                                            {event.date}
                                        </span>
                                    </div>
                                    <h4 className="font-medium text-sm text-gray-900 mb-1.5">
                                        {event.title}
                                    </h4>
                                    <p className="text-xs text-gray-600 leading-relaxed">
                                        {event.description}
                                    </p>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
