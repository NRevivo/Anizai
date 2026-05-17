import { useEffect, useMemo, useRef, useState } from 'react';
import { ExternalLink } from 'lucide-react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../ui/card';
import { StateMessage } from '../ui/StateMessage';
import type { TimelineEvent } from '../../types';

interface EvidenceTimelineProps {
    events: TimelineEvent[];
    insight?: string | null;
    /** Evidence IDs to highlight, set when a Drivers/Headwinds factor is clicked. */
    highlightedEvidenceIds?: string[];
}

type SourceType = TimelineEvent['sourceType'];
type Filter = 'all' | SourceType;

const SOURCE_ORDER: SourceType[] = ['news', 'expert', 'social', 'market'];
const SOURCE_LABEL: Record<SourceType, string> = {
    news: 'News',
    expert: 'Expert',
    social: 'Social',
    market: 'Market',
};

// One displayed row — may collapse several retrieved evidence items that
// share a title + domain (the agent often retrieves the same source twice).
interface EvidenceRow {
    key: string;
    evidenceIds: string[];
    duplicateCount: number;
    title: string;
    url: string | null;
    sourceType: SourceType;
    sourceDomain: string | null;
    source: string | null;
    earliest: Date | null;
    latest: Date | null;
    snippet: string;
    impactOnForecast: TimelineEvent['impactOnForecast'];
    impactLabel?: string;
    relevanceScore: number | null;
    credibilityTier: string | null;
    isKeyEvidence: boolean;
}

/** A valid Date from the event's timestamp, or null. */
function eventDate(event: TimelineEvent): Date | null {
    const d = event.timestamp;
    return d instanceof Date && !Number.isNaN(d.getTime()) ? d : null;
}

function formatDate(d: Date | null): string {
    if (!d) {
        return 'Undated';
    }
    const sameYear = d.getFullYear() === new Date().getFullYear();
    return d.toLocaleDateString('en-US', {
        month: 'short',
        day: 'numeric',
        ...(sameYear ? {} : { year: 'numeric' }),
    });
}

function sameCalendarDay(a: Date, b: Date): boolean {
    return (
        a.getFullYear() === b.getFullYear() &&
        a.getMonth() === b.getMonth() &&
        a.getDate() === b.getDate()
    );
}

function retrievalNote(row: EvidenceRow): string | null {
    if (row.duplicateCount < 2) {
        return null;
    }
    if (row.earliest && row.latest && !sameCalendarDay(row.earliest, row.latest)) {
        return `Retrieved ${row.duplicateCount}× between ${formatDate(row.earliest)} and ${formatDate(row.latest)}`;
    }
    return `Retrieved ${row.duplicateCount}×`;
}

function dedupeEvents(events: TimelineEvent[]): EvidenceRow[] {
    const rows = new Map<string, EvidenceRow>();

    for (const event of events) {
        const domainKey = (event.sourceDomain ?? event.source ?? '').trim().toLowerCase();
        const key = `${event.title.trim().toLowerCase()}|${domainKey}`;
        const ids = [event.id, event.evidenceId].filter((id): id is string => Boolean(id));
        const ts = eventDate(event);

        const existing = rows.get(key);
        if (existing) {
            existing.evidenceIds.push(...ids);
            existing.duplicateCount += 1;
            existing.isKeyEvidence = existing.isKeyEvidence || Boolean(event.isKeyEvidence);
            if (ts) {
                if (!existing.earliest || ts < existing.earliest) existing.earliest = ts;
                if (!existing.latest || ts > existing.latest) existing.latest = ts;
            }
            continue;
        }

        rows.set(key, {
            key,
            evidenceIds: ids,
            duplicateCount: 1,
            title: event.title,
            url: event.url ?? null,
            sourceType: event.sourceType,
            sourceDomain: event.sourceDomain ?? null,
            source: event.source ?? null,
            earliest: ts,
            latest: ts,
            snippet: event.snippet || event.description || '',
            impactOnForecast: event.impactOnForecast,
            impactLabel: event.impactLabel,
            relevanceScore: event.relevanceScore ?? null,
            credibilityTier: event.credibilityTier ?? null,
            isKeyEvidence: Boolean(event.isKeyEvidence),
        });
    }

    return Array.from(rows.values());
}

function impactView(impact: TimelineEvent['impactOnForecast']) {
    if (impact === 'positive') {
        return { dot: 'bg-anizai-teal-500', text: 'text-anizai-teal-700', label: 'Supports forecast' };
    }
    if (impact === 'negative') {
        return { dot: 'bg-rose-500', text: 'text-rose-700', label: 'Contradicts forecast' };
    }
    return { dot: 'bg-slate-300', text: 'text-slate-400', label: 'Neutral' };
}

function formatCredibility(tier: string | null): string {
    if (!tier) {
        return 'Not rated';
    }
    const match = tier.match(/(\d+)/);
    return match ? `Tier ${match[1]}` : tier;
}

function QualityIndicators({ row }: { row: EvidenceRow }) {
    const relevance =
        row.relevanceScore != null ? `${Math.round(row.relevanceScore * 100)}%` : 'Not rated';

    return (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1 text-[11px] text-slate-400">
            <span>
                Relevance <span className="font-medium text-slate-500">{relevance}</span>
            </span>
            <span className="text-slate-300">·</span>
            <span>
                Credibility{' '}
                <span className="font-medium text-slate-500">
                    {formatCredibility(row.credibilityTier)}
                </span>
            </span>
        </div>
    );
}

function EvidenceRowView({ row, highlighted }: { row: EvidenceRow; highlighted: boolean }) {
    const impact = impactView(row.impactOnForecast);
    const hasUrl = Boolean(row.url);
    const note = retrievalNote(row);

    return (
        <div className="relative pl-7">
            {/* Temporal marker on the group rail; colour doubles as impact. */}
            <span
                className={`absolute left-[3px] top-[15px] h-2.5 w-2.5 rounded-full ring-[3px] ring-white ${impact.dot}`}
                aria-hidden
            />
            <div
                className={`rounded-lg p-3 transition-all duration-700 ${
                    highlighted
                        ? 'bg-anizai-purple-50 ring-1 ring-anizai-purple-200'
                        : 'bg-slate-50/70 ring-1 ring-transparent'
                }`}
            >
                <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
                    <span className="rounded bg-slate-100 px-1.5 py-0.5 text-[10px] font-medium uppercase tracking-wide text-slate-500">
                        {SOURCE_LABEL[row.sourceType]}
                    </span>
                    <span className="text-[11px] font-medium text-slate-500">
                        {formatDate(row.latest)}
                    </span>
                    {row.sourceDomain ? (
                        <span className="text-[11px] text-slate-400">{row.sourceDomain}</span>
                    ) : row.source ? (
                        <span className="text-[11px] text-slate-400">{row.source}</span>
                    ) : null}
                    <span className={`ml-auto text-[11px] font-medium ${impact.text}`}>
                        {row.impactLabel || impact.label}
                    </span>
                </div>

                <div className="mt-1.5 flex items-start gap-2">
                    {hasUrl ? (
                        <a
                            href={row.url ?? undefined}
                            target="_blank"
                            rel="noreferrer"
                            className="group inline-flex items-start gap-1 text-sm font-medium leading-snug text-gray-900 transition-colors hover:text-anizai-blue-600"
                        >
                            <span className="break-words">{row.title}</span>
                            <ExternalLink
                                className="mt-0.5 h-3.5 w-3.5 shrink-0 text-slate-400 transition-colors group-hover:text-anizai-blue-600"
                                aria-hidden
                            />
                        </a>
                    ) : (
                        <span className="text-sm font-medium leading-snug text-gray-900 break-words">
                            {row.title}
                        </span>
                    )}
                    {row.isKeyEvidence ? (
                        <span className="ml-auto shrink-0 rounded-full bg-anizai-teal-50 px-2 py-0.5 text-[10px] font-semibold uppercase tracking-wide text-anizai-teal-700">
                            Key
                        </span>
                    ) : null}
                </div>

                {row.snippet ? (
                    <p className="mt-1 line-clamp-2 break-words text-xs leading-relaxed text-slate-500">
                        {row.snippet}
                    </p>
                ) : null}

                <div className="mt-2 flex flex-col gap-1">
                    <QualityIndicators row={row} />
                    {note ? <span className="text-[11px] text-slate-400">{note}</span> : null}
                </div>
            </div>
        </div>
    );
}

export function EvidenceTimeline({
    events,
    insight = null,
    highlightedEvidenceIds = [],
}: EvidenceTimelineProps) {
    const [filter, setFilter] = useState<Filter>('all');
    const rootRef = useRef<HTMLDivElement>(null);

    const rows = useMemo(() => dedupeEvents(events), [events]);

    const highlightSet = useMemo(() => new Set(highlightedEvidenceIds), [highlightedEvidenceIds]);
    const isHighlighted = (row: EvidenceRow) =>
        highlightSet.size > 0 && row.evidenceIds.some((id) => highlightSet.has(id));

    // A factor link forces the full list into view so highlighted rows aren't
    // hidden behind an active filter, and scrolls the card into the viewport.
    useEffect(() => {
        if (highlightedEvidenceIds.length === 0) {
            return;
        }
        setFilter('all');
        rootRef.current?.scrollIntoView({ behavior: 'smooth', block: 'start' });
    }, [highlightedEvidenceIds]);

    const availableTypes = useMemo(() => {
        const present = new Set(rows.map((row) => row.sourceType));
        return SOURCE_ORDER.filter((type) => present.has(type));
    }, [rows]);

    const groups = useMemo(() => {
        const visibleTypes = filter === 'all' ? availableTypes : availableTypes.filter((t) => t === filter);
        return visibleTypes
            .map((type) => ({
                type,
                rows: rows
                    .filter((row) => row.sourceType === type)
                    // Most recent first; undated rows sink to the bottom.
                    .sort(
                        (a, b) =>
                            (b.latest?.getTime() ?? -Infinity) - (a.latest?.getTime() ?? -Infinity)
                    ),
            }))
            .filter((group) => group.rows.length > 0);
    }, [rows, availableTypes, filter]);

    const hasResults = groups.length > 0;

    return (
        <Card
            ref={rootRef}
            className="h-full max-w-full overflow-hidden border-0 bg-white shadow-[0_4px_24px_rgba(15,23,42,0.06),0_1px_3px_rgba(15,23,42,0.05)] ring-1 ring-slate-900/[0.05] flex flex-col scroll-mt-4"
        >
            <CardHeader className="flex flex-col gap-3 p-5 pb-3 sm:p-7 sm:pb-4">
                <div className="flex flex-col gap-3 sm:flex-row sm:items-start sm:justify-between">
                    <div className="min-w-0">
                        <CardTitle className="text-base font-semibold text-gray-900">Evidence</CardTitle>
                        <CardDescription className="mt-1 text-xs text-slate-500">
                            {insight?.trim()
                                ? insight.trim()
                                : `${rows.length} distinct source${rows.length === 1 ? '' : 's'} behind this forecast`}
                        </CardDescription>
                    </div>
                    <div className="flex w-full max-w-full gap-1 overflow-x-auto rounded-lg bg-slate-100/70 p-1 sm:w-fit">
                        {(['all', ...availableTypes] as Filter[]).map((type) => (
                            <button
                                key={type}
                                type="button"
                                onClick={() => setFilter(type)}
                                className={`min-h-7 flex-1 rounded-md px-2.5 py-1 text-[10px] font-semibold uppercase tracking-wide transition-colors sm:flex-none ${
                                    filter === type
                                        ? 'bg-white text-gray-900 shadow-sm'
                                        : 'text-slate-500 hover:text-slate-700'
                                }`}
                            >
                                {type === 'all' ? 'All' : SOURCE_LABEL[type]}
                            </button>
                        ))}
                    </div>
                </div>
            </CardHeader>

            <CardContent className="flex-1 space-y-6 p-5 pt-1 sm:p-7 sm:pt-1">
                {hasResults ? (
                    groups.map((group) => (
                        <div key={group.type}>
                            <div className="mb-2 flex items-center gap-2">
                                <h4 className="text-[11px] font-medium uppercase tracking-[0.1em] text-slate-400">
                                    {SOURCE_LABEL[group.type]}
                                </h4>
                                <span className="text-[11px] font-medium text-slate-300">
                                    {group.rows.length}
                                </span>
                            </div>
                            <div className="relative space-y-2">
                                {/* Subtle chronological rail behind the row dots. */}
                                {group.rows.length > 1 ? (
                                    <span
                                        className="absolute left-[7px] top-4 bottom-4 w-px bg-slate-200"
                                        aria-hidden
                                    />
                                ) : null}
                                {group.rows.map((row) => (
                                    <EvidenceRowView
                                        key={row.key}
                                        row={row}
                                        highlighted={isHighlighted(row)}
                                    />
                                ))}
                            </div>
                        </div>
                    ))
                ) : (
                    <StateMessage
                        compact
                        title={events.length === 0 ? 'No evidence yet' : 'No matching evidence'}
                        description={
                            events.length === 0
                                ? 'Sources will appear here when evidence is attached to this forecast.'
                                : 'Change the evidence filter to view available sources.'
                        }
                    />
                )}
            </CardContent>
        </Card>
    );
}
