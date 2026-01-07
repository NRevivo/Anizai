import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import type { TimelineEvent } from '../../types';

interface EvidenceTimelineProps {
    events: TimelineEvent[];
}



export function EvidenceTimeline({ events }: EvidenceTimelineProps) {
    return (
        <Card className="h-full border-gray-200 bg-white shadow-sm">
            <CardHeader className="pb-4 border-b border-gray-50 flex flex-row items-center justify-between">
                <div>
                    <CardTitle className="text-lg font-semibold text-gray-900">Live Evidence Feed</CardTitle>
                    <CardDescription className="text-xs text-gray-500 mt-1">Real-time data ingestion</CardDescription>
                </div>
                <button className="text-xs font-medium text-anizai-blue-600 hover:text-anizai-blue-700 bg-anizai-blue-50 px-3 py-1.5 rounded-full transition-colors">
                    View All Sources
                </button>
            </CardHeader>
            <CardContent className="pt-0">
                <div className="relative">
                    {/* Vertical Line */}
                    <div className="absolute left-6 top-6 bottom-6 w-px bg-gray-100" />

                    <div className="divide-y divide-gray-50">
                        {events.map((event) => (
                            <div key={event.id} className="relative pl-16 py-6 group hover:bg-gray-50/50 -mx-6 px-6 transition-colors">
                                {/* Icon/Dot */}
                                <div className="absolute left-6 -translate-x-1/2 top-10 flex flex-col items-center gap-1 z-10 bg-white py-1">
                                    <div className={`w-3 h-3 rounded-full border-2 ${event.impact === 'positive'
                                        ? 'bg-anizai-teal-500 border-anizai-teal-100'
                                        : event.impact === 'negative'
                                            ? 'bg-gray-400 border-gray-200'
                                            : 'bg-gray-300 border-gray-100'
                                        }`} />
                                </div>

                                {/* Content */}
                                <div className="flex justify-between items-start gap-4">
                                    <div className="space-y-1.5">
                                        <div className="flex items-center gap-2">
                                            <span className={`text-[10px] font-bold uppercase tracking-wider px-1.5 py-0.5 rounded border ${event.sourceType === 'news' ? 'bg-blue-50 text-blue-700 border-blue-100' :
                                                event.sourceType === 'expert' ? 'bg-purple-50 text-purple-700 border-purple-100' :
                                                    'bg-gray-50 text-gray-600 border-gray-200'
                                                }`}>
                                                {event.sourceType}
                                            </span>
                                            <span className="text-xs font-medium text-gray-900">Reuters</span>
                                            <span className="text-xs text-gray-400">•</span>
                                            <span className="text-xs text-gray-400">{event.date}</span>
                                        </div>

                                        <h4 className="text-sm font-semibold text-gray-900 leading-snug">
                                            {event.title}
                                        </h4>
                                        <p className="text-sm text-gray-600 leading-relaxed line-clamp-2 max-w-2xl">
                                            {event.description}
                                        </p>
                                    </div>

                                    {/* Impact Badge */}
                                    <div className="shrink-0 flex flex-col items-end gap-1">
                                        <span className={`text-[10px] font-semibold uppercase tracking-wide ${event.impact === 'positive' ? 'text-green-600' : 'text-gray-400'
                                            }`}>
                                            {event.impact === 'positive' ? '+ Impact' : 'Neutral'}
                                        </span>
                                        {event.impact === 'positive' && (
                                            <div className="flex gap-0.5">
                                                <div className="w-1 h-3 bg-green-500 rounded-sm"></div>
                                                <div className="w-1 h-3 bg-green-500 rounded-sm"></div>
                                                <div className="w-1 h-3 bg-green-200 rounded-sm"></div>
                                            </div>
                                        )}
                                    </div>
                                </div>
                            </div>
                        ))}
                    </div>
                </div>
            </CardContent>
        </Card>
    );
}
