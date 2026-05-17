import type { KeyFactor } from '../../../types';

interface DriversAndHeadwindsProps {
    keyFactors: KeyFactor[];
    onFactorSelect?: (evidenceIds: string[]) => void;
}

type ColumnTone = 'positive' | 'negative';

const ROW_SURFACE: Record<ColumnTone, string> = {
    positive: 'bg-emerald-50/40',
    negative: 'bg-rose-50/40',
};

const BAR_COLOR: Record<ColumnTone, string> = {
    positive: 'bg-emerald-600/70',
    negative: 'bg-rose-600/70',
};

function FactorRow({
    factor,
    tone,
    onFactorSelect,
}: {
    factor: KeyFactor;
    tone: ColumnTone;
    onFactorSelect?: (evidenceIds: string[]) => void;
}) {
    const sources = factor.evidence_ids?.length ?? 0;
    const weightPct = Math.min(100, Math.max(0, Math.round(factor.weight * 100)));
    const linkable = sources > 0 && Boolean(onFactorSelect);

    const inner = (
        <>
            <div className="flex flex-wrap items-center gap-2">
                <p className="text-sm font-semibold text-gray-900">{factor.label}</p>
                {sources > 0 ? (
                    <span className="text-[11px] font-medium text-slate-400">
                        {linkable ? 'View ' : ''}
                        {sources} {sources === 1 ? 'source' : 'sources'}
                    </span>
                ) : null}
            </div>
            <p className="mt-1 break-words text-xs leading-relaxed text-slate-500">
                {factor.description}
            </p>
            <div className="mt-2.5 h-1 w-full rounded-full bg-slate-200/60">
                <div
                    className={`h-1 rounded-full ${BAR_COLOR[tone]}`}
                    style={{ width: `${weightPct}%` }}
                />
            </div>
        </>
    );

    if (linkable) {
        return (
            <button
                type="button"
                onClick={() => onFactorSelect?.(factor.evidence_ids)}
                className={`block w-full rounded-lg p-3 text-left transition-colors hover:bg-slate-100/70 ${ROW_SURFACE[tone]}`}
            >
                {inner}
            </button>
        );
    }

    return <div className={`rounded-lg p-3 ${ROW_SURFACE[tone]}`}>{inner}</div>;
}

function Column({
    title,
    tone,
    factors,
    subtitle,
    onFactorSelect,
}: {
    title: string;
    tone: ColumnTone;
    factors: KeyFactor[];
    subtitle?: string;
    onFactorSelect?: (evidenceIds: string[]) => void;
}) {
    const sorted = factors.slice().sort((a, b) => b.weight - a.weight);

    return (
        <div className="flex-1">
            <h4 className="text-[11px] font-medium uppercase tracking-[0.1em] text-slate-400">
                {title}
            </h4>
            {subtitle ? (
                <p className="mb-2.5 mt-0.5 text-[11px] text-slate-400">{subtitle}</p>
            ) : (
                <div className="mb-2.5" />
            )}
            <div className="space-y-2">
                {sorted.map((factor, index) => (
                    <FactorRow
                        key={`${index}-${factor.label}`}
                        factor={factor}
                        tone={tone}
                        onFactorSelect={onFactorSelect}
                    />
                ))}
            </div>
        </div>
    );
}

export function DriversAndHeadwinds({ keyFactors, onFactorSelect }: DriversAndHeadwindsProps) {
    const drivers = keyFactors.filter((factor) => factor.direction === 'increases');
    const headwinds = keyFactors.filter((factor) => factor.direction === 'decreases');

    if (drivers.length === 0 && headwinds.length === 0) {
        return null;
    }

    if (drivers.length === 0) {
        return (
            <Column
                title="Headwinds"
                tone="negative"
                factors={headwinds}
                subtitle="Only headwinds identified for this forecast"
                onFactorSelect={onFactorSelect}
            />
        );
    }

    if (headwinds.length === 0) {
        return (
            <Column
                title="Drivers"
                tone="positive"
                factors={drivers}
                subtitle="Only drivers identified for this forecast"
                onFactorSelect={onFactorSelect}
            />
        );
    }

    return (
        <div className="flex flex-col gap-5 md:flex-row">
            <Column title="Drivers" tone="positive" factors={drivers} onFactorSelect={onFactorSelect} />
            <Column title="Headwinds" tone="negative" factors={headwinds} onFactorSelect={onFactorSelect} />
        </div>
    );
}
