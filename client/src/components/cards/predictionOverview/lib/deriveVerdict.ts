export type VerdictAction =
    | 'strong-bet-yes'
    | 'lean-yes'
    | 'avoid'
    | 'lean-no'
    | 'strong-bet-no'
    | 'insufficient';

export type VerdictTone = 'positive' | 'negative' | 'neutral' | 'warning';

export type Verdict = {
    action: VerdictAction;
    label: string;
    tone: VerdictTone;
};

type VerdictInput = {
    finalProbability: number;
    confidence: number;
};

const TONE_BY_ACTION: Record<VerdictAction, VerdictTone> = {
    'strong-bet-yes': 'positive',
    'lean-yes': 'positive',
    'strong-bet-no': 'negative',
    'lean-no': 'negative',
    avoid: 'neutral',
    insufficient: 'warning',
};

function verdict(action: VerdictAction, label: string): Verdict {
    return { action, label, tone: TONE_BY_ACTION[action] };
}

export function deriveVerdict(p: VerdictInput): Verdict {
    const { finalProbability, confidence } = p;

    if (confidence < 0.2) {
        return verdict('insufficient', "Don't Bet — Insufficient Evidence");
    }
    if (finalProbability >= 0.7 && confidence >= 0.6) {
        return verdict('strong-bet-yes', 'Strong Yes');
    }
    if (finalProbability <= 0.3 && confidence >= 0.6) {
        return verdict('strong-bet-no', 'Strong No');
    }
    if (finalProbability >= 0.4 && finalProbability <= 0.6) {
        return verdict('avoid', 'Coin Flip — Avoid');
    }
    if (finalProbability >= 0.6) {
        return verdict('lean-yes', 'Lean Yes');
    }
    if (finalProbability <= 0.4) {
        return verdict('lean-no', 'Lean No');
    }
    return verdict('avoid', 'Unclear — Avoid');
}
