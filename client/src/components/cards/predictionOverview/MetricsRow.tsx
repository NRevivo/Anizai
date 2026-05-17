import type { Prediction } from '../../../types';

type ConsensusStrength = NonNullable<Prediction['consensusStrength']>;

const CONSENSUS_LEVEL: Record<ConsensusStrength, number> = {
    Strong: 3,
    Moderate: 2,
    Weak: 1,
};

interface MetricsRowProps {
    confidence: number;
    confidenceLabel: Prediction['confidenceLabel'];
    consensusStrength: Prediction['consensusStrength'];
}

function Tile({ children }: { children: React.ReactNode }) {
    return (
        <div className="min-w-0 rounded-xl bg-white p-4 shadow-[0_1px_3px_rgba(15,23,42,0.06),0_1px_2px_rgba(15,23,42,0.04)] ring-1 ring-slate-900/[0.04]">
            {children}
        </div>
    );
}

function TileLabel({ children }: { children: React.ReactNode }) {
    return (
        <p className="text-[11px] font-medium uppercase tracking-[0.1em] text-slate-400">
            {children}
        </p>
    );
}

export function MetricsRow({ confidence, confidenceLabel, consensusStrength }: MetricsRowProps) {
    const confidencePct = Math.round(Math.min(1, Math.max(0, confidence)) * 100);
    const consensusLevel = consensusStrength ? CONSENSUS_LEVEL[consensusStrength] : 0;

    return (
        <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <Tile>
                <TileLabel>Confidence</TileLabel>
                <div className="mt-2 flex items-baseline gap-1.5">
                    <span className="text-3xl font-light leading-none tracking-tight text-gray-900">
                        {confidencePct}
                    </span>
                    <span className="text-sm font-light text-slate-400">/ 100</span>
                </div>
                <div className="mt-3 h-1 w-full rounded-full bg-slate-100">
                    <div
                        className="h-1 rounded-full bg-slate-400"
                        style={{ width: `${confidencePct}%` }}
                    />
                </div>
                {confidenceLabel ? (
                    <p className="mt-2 text-xs font-medium text-slate-500">{confidenceLabel}</p>
                ) : null}
            </Tile>

            <Tile>
                <TileLabel>Consensus</TileLabel>
                <p className="mt-2 text-3xl font-light leading-none tracking-tight text-gray-900">
                    {consensusStrength ?? 'Unrated'}
                </p>
                <div className="mt-3 flex gap-1.5">
                    {[1, 2, 3].map((segment) => (
                        <span
                            key={segment}
                            className={`h-1 flex-1 rounded-full ${
                                segment <= consensusLevel ? 'bg-slate-500' : 'bg-slate-100'
                            }`}
                        />
                    ))}
                </div>
                <p className="mt-2 text-xs font-medium text-slate-500">Evidence agreement</p>
            </Tile>
        </div>
    );
}
