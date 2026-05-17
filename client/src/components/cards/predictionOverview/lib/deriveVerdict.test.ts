import { describe, expect, it } from 'vitest';
import { deriveVerdict } from './deriveVerdict';

describe('deriveVerdict', () => {
    it('row 1: confidence < 0.20 → insufficient (warning)', () => {
        const v = deriveVerdict({ finalProbability: 0.9, confidence: 0.19 });
        expect(v.action).toBe('insufficient');
        expect(v.label).toBe("Don't Bet — Insufficient Evidence");
        expect(v.tone).toBe('warning');
    });

    it('row 1 takes precedence over a strong-yes probability', () => {
        const v = deriveVerdict({ finalProbability: 0.95, confidence: 0.1 });
        expect(v.action).toBe('insufficient');
    });

    it('row 2: prob ≥ 0.70 && confidence ≥ 0.60 → strong-bet-yes (positive)', () => {
        const v = deriveVerdict({ finalProbability: 0.7, confidence: 0.6 });
        expect(v.action).toBe('strong-bet-yes');
        expect(v.label).toBe('Strong Yes');
        expect(v.tone).toBe('positive');
    });

    it('row 3: prob ≤ 0.30 && confidence ≥ 0.60 → strong-bet-no (negative)', () => {
        const v = deriveVerdict({ finalProbability: 0.3, confidence: 0.6 });
        expect(v.action).toBe('strong-bet-no');
        expect(v.label).toBe('Strong No');
        expect(v.tone).toBe('negative');
    });

    it('row 4: prob in [0.40, 0.60] → avoid (neutral)', () => {
        const v = deriveVerdict({ finalProbability: 0.5, confidence: 0.9 });
        expect(v.action).toBe('avoid');
        expect(v.label).toBe('Coin Flip — Avoid');
        expect(v.tone).toBe('neutral');
    });

    it('row 4 boundary: prob = 0.40', () => {
        expect(deriveVerdict({ finalProbability: 0.4, confidence: 0.9 }).action).toBe('avoid');
    });

    it('row 4 boundary: prob = 0.60', () => {
        expect(deriveVerdict({ finalProbability: 0.6, confidence: 0.9 }).action).toBe('avoid');
    });

    it('row 5: prob ≥ 0.60 (low confidence) → lean-yes (positive)', () => {
        const v = deriveVerdict({ finalProbability: 0.65, confidence: 0.3 });
        expect(v.action).toBe('lean-yes');
        expect(v.label).toBe('Lean Yes');
        expect(v.tone).toBe('positive');
    });

    it('row 5: prob ≥ 0.70 but confidence < 0.60 → lean-yes (not strong)', () => {
        expect(deriveVerdict({ finalProbability: 0.8, confidence: 0.4 }).action).toBe('lean-yes');
    });

    it('row 6: prob ≤ 0.40 (low confidence) → lean-no (negative)', () => {
        const v = deriveVerdict({ finalProbability: 0.35, confidence: 0.3 });
        expect(v.action).toBe('lean-no');
        expect(v.label).toBe('Lean No');
        expect(v.tone).toBe('negative');
    });

    it('row 6: prob ≤ 0.30 but confidence < 0.60 → lean-no (not strong)', () => {
        expect(deriveVerdict({ finalProbability: 0.2, confidence: 0.4 }).action).toBe('lean-no');
    });

    it('confidence boundary 0.20 is enough to escape insufficient', () => {
        expect(deriveVerdict({ finalProbability: 0.8, confidence: 0.2 }).action).toBe('lean-yes');
    });

    it('confidence boundary 0.60 qualifies for strong-bet-yes', () => {
        expect(deriveVerdict({ finalProbability: 0.75, confidence: 0.6 }).action).toBe('strong-bet-yes');
    });

    it('prob boundary 0.70 qualifies for strong-bet-yes', () => {
        expect(deriveVerdict({ finalProbability: 0.7, confidence: 0.7 }).action).toBe('strong-bet-yes');
    });

    it('prob boundary 0.30 qualifies for strong-bet-no', () => {
        expect(deriveVerdict({ finalProbability: 0.3, confidence: 0.7 }).action).toBe('strong-bet-no');
    });

    it('row 7 fallback: a NaN probability that escapes every range → avoid', () => {
        const v = deriveVerdict({ finalProbability: NaN, confidence: 0.9 });
        expect(v.action).toBe('avoid');
        expect(v.label).toBe('Unclear — Avoid');
        expect(v.tone).toBe('neutral');
    });
});
