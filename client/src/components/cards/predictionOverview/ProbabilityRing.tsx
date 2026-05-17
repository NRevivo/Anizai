import { useId } from 'react';

interface ProbabilityRingProps {
    /** 0–1 float. */
    probability: number;
    /** Outer diameter in px. */
    size?: number;
}

const STROKE = 12;

/**
 * The hero visualization of the card — the single place the brand
 * lavender→teal gradient appears. The arc length encodes finalProbability.
 */
export function ProbabilityRing({ probability, size = 152 }: ProbabilityRingProps) {
    const gradientId = useId();
    const clamped = Math.min(1, Math.max(0, probability));
    const pct = Math.round(clamped * 100);

    const radius = (size - STROKE) / 2;
    const center = size / 2;
    const circumference = 2 * Math.PI * radius;
    const dashOffset = circumference * (1 - clamped);

    return (
        <div className="relative shrink-0" style={{ width: size, height: size }}>
            <svg width={size} height={size} viewBox={`0 0 ${size} ${size}`} aria-hidden>
                <defs>
                    <linearGradient id={gradientId} x1="0%" y1="0%" x2="100%" y2="100%">
                        <stop offset="0%" stopColor="#c084fc" />
                        <stop offset="100%" stopColor="#2dd4bf" />
                    </linearGradient>
                </defs>
                <circle
                    cx={center}
                    cy={center}
                    r={radius}
                    fill="none"
                    stroke="#eef0f4"
                    strokeWidth={STROKE}
                />
                <circle
                    cx={center}
                    cy={center}
                    r={radius}
                    fill="none"
                    stroke={`url(#${gradientId})`}
                    strokeWidth={STROKE}
                    strokeLinecap="round"
                    strokeDasharray={circumference}
                    strokeDashoffset={dashOffset}
                    transform={`rotate(-90 ${center} ${center})`}
                />
            </svg>
            <div className="absolute inset-0 flex flex-col items-center justify-center">
                <span className="text-4xl font-light leading-none tracking-tight text-gray-900">
                    {pct}
                    <span className="text-xl font-light text-gray-400">%</span>
                </span>
                <span className="mt-1.5 text-[10px] font-medium uppercase tracking-[0.14em] text-slate-400">
                    Probability
                </span>
            </div>
        </div>
    );
}
