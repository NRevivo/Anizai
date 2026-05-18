import { ListTree } from 'lucide-react';
import type { AgentEvent } from '../../types';
import { StateMessage } from '../ui/StateMessage';

interface AgentEventsTimelineProps {
    events: AgentEvent[];
    isLoading?: boolean;
}

// Maps the canonical agentEvents status enum (pending | running | done |
// failed) to the subdued brand palette: amber for running/pending,
// anizai-teal for done, rose for failed. An `error` event type is always
// treated as a failure regardless of status.
function getStatusView(event: AgentEvent) {
    if (event.status === 'failed' || event.type === 'error') {
        return {
            dot: 'bg-rose-500',
            badge: 'bg-rose-50 text-rose-700 border-rose-200',
            label: 'Failed',
            rowBorder: 'border-rose-100',
        };
    }

    if (event.status === 'running' || event.status === 'pending') {
        return {
            dot: 'bg-amber-500',
            badge: 'bg-amber-50 text-amber-700 border-amber-200',
            label: event.status === 'pending' ? 'Pending' : 'Running',
            rowBorder: 'border-amber-100',
        };
    }

    if (event.status === 'done') {
        return {
            dot: 'bg-anizai-teal-500',
            badge: 'bg-anizai-teal-50 text-anizai-teal-700 border-anizai-teal-200',
            label: 'Done',
            rowBorder: 'border-anizai-teal-100',
        };
    }

    // Unknown / missing status — degrade to a neutral row rather than crash.
    return {
        dot: 'bg-slate-300',
        badge: 'bg-slate-50 text-slate-600 border-slate-200',
        label: 'Event',
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

            {isLoading ? (
                <div className="p-4 sm:p-5">
                    <StateMessage
                        compact
                        variant="loading"
                        title="Loading agent timeline"
                        description="Listening for reasoning and follow-up events."
                    />
                </div>
            ) : events.length === 0 ? (
                <div className="flex flex-col items-center justify-center gap-2 px-4 py-7 text-center">
                    <span className="flex h-10 w-10 items-center justify-center rounded-full bg-slate-50 ring-1 ring-slate-200/70">
                        <ListTree className="h-5 w-5 text-slate-400" />
                    </span>
                    <p className="text-sm font-semibold text-gray-900">Agent execution timeline</p>
                    <p className="max-w-xs text-xs text-gray-500">
                        Step-by-step trace of the agent&apos;s reasoning will appear here once available.
                    </p>
                </div>
            ) : (
                <div className="space-y-3 p-4 sm:p-5">
                    {events.map((event) => {
                        const statusView = getStatusView(event);
                        const durationLabel = formatDuration(event.durationMs);
                        const typeLabel = event.type ? event.type.replaceAll('_', ' ') : 'event';

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
                                            <span className="uppercase tracking-wide">{typeLabel}</span>
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
    );
}
