import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { StateMessage } from '../ui/StateMessage';
import type { TimelineEvent } from '../../types';
import { useState } from 'react';

interface EvidenceTimelineProps {
    events: TimelineEvent[];
}

export function EvidenceTimeline({ events }: EvidenceTimelineProps) {
    const [filter, setFilter] = useState<'all' | 'news' | 'expert' | 'social'>('all');

    // Sort events by date descending
    const sortedEvents = [...events].sort((a, b) => new Date(b.date).getTime() - new Date(a.date).getTime());

    // Key evidence: prioritizing items marked manually or with high impact
    const keyEvidence = sortedEvents.filter(e => e.isKeyEvidence).slice(0, 2);

    // Filtered list for the main feed
    const filteredEvents = filter === 'all'
        ? sortedEvents
        : sortedEvents.filter(e => e.sourceType === filter);

    return (
        <Card className="h-full max-w-full overflow-hidden border-gray-200 bg-white shadow-sm flex flex-col">
            <CardHeader className="p-4 sm:p-5 pb-3 border-b border-gray-50 flex flex-col gap-3">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-center sm:justify-between">
                    <div className="min-w-0">
                        <CardTitle className="text-base font-semibold text-gray-900">Evidence</CardTitle>
                        <CardDescription className="text-xs text-gray-500 mt-1">
                            {events.length} supporting source{events.length === 1 ? '' : 's'}
                        </CardDescription>
                    </div>
                    <div className="flex w-full sm:w-fit max-w-full overflow-x-auto bg-gray-100/50 p-1 rounded-lg">
                        {(['all', 'news', 'expert', 'social'] as const).map((type) => (
                            <button
                                key={type}
                                onClick={() => setFilter(type)}
                                className={`min-h-8 flex-1 sm:flex-none px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide rounded-md transition-all ${filter === type
                                        ? 'bg-white text-gray-900 shadow-sm'
                                        : 'text-gray-500 hover:text-gray-700'
                                    }`}
                            >
                                {type}
                            </button>
                        ))}
                    </div>
                </div>

                <div className="bg-slate-50 rounded-md p-3 border border-slate-100">
                    <p className="text-xs text-slate-600 leading-relaxed font-medium break-words">
                        <span className="text-anizai-teal-600 font-bold uppercase text-[10px] tracking-wider mr-2">Evidence</span>
                        Sources and signals used to support the forecast result.
                    </p>
                </div>
            </CardHeader>
            <CardContent className="p-4 sm:p-5 pt-0 flex-1 overflow-y-auto">
                <div className="relative">
                    {filter === 'all' && keyEvidence.length > 0 && (
                        <div className="mb-5 pt-4">
                            <h5 className="text-[10px] font-bold text-gray-400 uppercase tracking-widest mb-3 px-2">Key evidence</h5>
                            <div className="space-y-3">
                                {keyEvidence.map(event => (
                                    <div key={`key-${event.id}`} className="bg-gradient-to-r from-gray-50 to-white border-l-2 border-anizai-teal-400 p-3 pl-4 rounded-r-md">
                                        <div className="flex flex-wrap justify-between items-start gap-1 mb-1">
                                            <span className="text-[10px] uppercase font-bold text-anizai-teal-700 tracking-wide">
                                                {event.impactLabel || 'Key evidence'}
                                            </span>
                                            <span className="text-[10px] text-gray-400 font-mono">{event.date}</span>
                                        </div>
                                        <h4 className="text-sm font-semibold text-gray-900 break-words">{event.title}</h4>
                                    </div>
                                ))}
                            </div>
                            <div className="my-4 border-t border-gray-100 flex items-center justify-center">
                                <span className="bg-white px-2 text-[10px] text-gray-300 font-medium uppercase tracking-wider -mt-2">All evidence</span>
                            </div>
                        </div>
                    )}

                    {filteredEvents.length > 0 ? (
                        <div className="relative pl-4 space-y-4">
                            <div className="absolute left-[19px] top-2 bottom-2 w-px bg-gray-100" />

                            {filteredEvents.map((event) => (
                                <div key={event.id} className="relative pl-8 group">
                                    <div className={`absolute left-[15px] top-1.5 w-2 h-2 rounded-full ring-4 ring-white z-10 ${event.impact === 'positive' ? 'bg-anizai-teal-500' :
                                            event.impact === 'negative' ? 'bg-red-400' : 'bg-gray-300'
                                        }`} />

                                    <div className="flex flex-col gap-1">
                                        <div className="flex flex-wrap items-center gap-2">
                                            <span className="text-[10px] font-bold uppercase tracking-wider px-1.5 py-px rounded-sm bg-gray-50 text-gray-500">
                                                {event.sourceType}
                                            </span>
                                            <span className="text-xs text-gray-400 font-medium">{event.date}</span>
                                            <span className={`text-[10px] font-medium ${event.impact === 'positive' ? 'text-anizai-teal-600' :
                                                    event.impact === 'negative' ? 'text-red-500' : 'text-gray-400'
                                                }`}>
                                                {event.impactLabel || (event.impact === 'positive' ? 'Supports forecast' : 'Neutral')}
                                            </span>
                                        </div>

                                        <h4 className="text-sm font-medium text-gray-900 leading-snug break-words group-hover:text-anizai-blue-600 transition-colors cursor-pointer">
                                            {event.title}
                                        </h4>

                                        <p className="text-xs text-gray-500 leading-relaxed break-words line-clamp-2">
                                            {event.description}
                                        </p>
                                    </div>
                                </div>
                            ))}
                        </div>
                    ) : (
                        <div className="mt-4">
                            <StateMessage
                                compact
                                title={events.length === 0 ? 'No evidence yet' : 'No matching evidence'}
                                description={events.length === 0
                                    ? 'Sources will appear here when evidence is attached to this forecast.'
                                    : 'Change the evidence filter to view available sources.'}
                            />
                        </div>
                    )}
                </div>
            </CardContent>
        </Card>
    );
}
