import type { AgentEvent } from '../../types';
import { StateMessage } from '../ui/StateMessage';

interface AgentEventsTimelineProps {
    events: AgentEvent[];
    isLoading?: boolean;
}

function getStatusView(event: AgentEvent) {
    if (event.status === 'failed' || event.type === 'error') {
        return {
            dot: 'bg-red-500',
            badge: 'bg-red-50 text-red-700 border-red-200',
            label: 'Failed',
            rowBorder: 'border-red-100',
        };
    }

    if (event.status === 'in_progress') {
        return {
            dot: 'bg-amber-500',
            badge: 'bg-amber-50 text-amber-700 border-amber-200',
            label: 'In progress',
            rowBorder: 'border-amber-100',
        };
    }

    return {
        dot: 'bg-emerald-500',
        badge: 'bg-emerald-50 text-emerald-700 border-emerald-200',
        label: 'Complete',
        rowBorder: 'border-gray-100',
    };
}

function formatDuration(durationMs: number | null): string | null {
    if (durationMs === null || durationMs < 0) {
        return null;
    }

    if (durationMs >= 1000) {
        return `${(durationMs / 1000).toFixed(durationMs >= 10_000 ? 0 : 1)}s`;
    }

    return `${durationMs}ms`;
}

export function AgentEventsTimeline({ events, isLoading = false }: AgentEventsTimelineProps) {
    return (
        <div className="rounded-lg border border-gray-200 bg-white shadow-sm">
            <div className="border-b border-gray-100 px-4 py-3 sm:px-5">
                <h3 className="text-sm font-semibold text-gray-900">Agent timeline</h3>
                <p className="mt-0.5 text-xs text-gray-500">Compact reasoning and follow-up activity for this session.</p>
            </div>

            <div className="p-4 sm:p-5">
                {isLoading ? (
                    <StateMessage
                        compact
                        variant="loading"
                        title="Loading agent timeline"
                        description="Listening for reasoning and follow-up events."
                    />
                ) : events.length === 0 ? (
                    <StateMessage
                        compact
                        title="No agent events yet"
                        description="Reasoning events will appear here when analysis or follow-up activity is recorded."
                    />
                ) : (
                    <div className="space-y-3">
                        {events.map((event) => {
                            const statusView = getStatusView(event);
                            const durationLabel = formatDuration(event.durationMs);

                            return (
                                <div
                                    key={event.eventId}
                                    className={`rounded-md border px-3 py-3 sm:px-4 ${statusView.rowBorder} ${event.parentMessageId ? 'bg-slate-50/70' : 'bg-white'}`}
                                >
                                    <div className="flex items-start gap-3">
                                        <div className="flex flex-col items-center pt-1">
                                            <span className={`h-2.5 w-2.5 rounded-full ${statusView.dot}`} />
                                            <span className="mt-1 min-h-4 w-px bg-gray-200 last:hidden" />
                                        </div>

                                        <div className="min-w-0 flex-1 space-y-1.5">
                                            <div className="flex flex-wrap items-center gap-2">
                                                <p className="text-sm font-medium text-gray-900 break-words">{event.title}</p>
                                                <span className={`inline-flex items-center rounded-full border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide ${statusView.badge}`}>
                                                    {statusView.label}
                                                </span>
                                                {event.parentMessageId ? (
                                                    <span className="inline-flex items-center rounded-full bg-slate-100 px-2 py-0.5 text-[10px] font-medium text-slate-600">
                                                        Follow-up
                                                    </span>
                                                ) : null}
                                            </div>

                                            {event.description ? (
                                                <p className="text-sm text-gray-600 break-words">{event.description}</p>
                                            ) : null}

                                            <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-gray-500">
                                                <span className="uppercase tracking-wide">{event.type.replaceAll('_', ' ')}</span>
                                                {durationLabel ? <span>{durationLabel}</span> : null}
                                                <span>{event.timestamp.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
                                            </div>
                                        </div>
                                    </div>
                                </div>
                            );
                        })}
                    </div>
                )}
            </div>
        </div>
    );
}
