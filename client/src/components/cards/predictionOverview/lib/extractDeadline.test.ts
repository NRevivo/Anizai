import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { extractDeadline } from './extractDeadline';

describe('extractDeadline', () => {
    it('pattern 1: explicit ISO date is returned as-is', () => {
        expect(extractDeadline('Will the bridge open by 2027-06-30?')).toEqual({
            iso: '2027-06-30',
            label: '2027-06-30',
        });
    });

    it('pattern 1 wins over a "before" clause around the same date', () => {
        expect(extractDeadline('Resolved before 2027-03-15 confirmed?')).toEqual({
            iso: '2027-03-15',
            label: '2027-03-15',
        });
    });

    it('pattern 2: "before YYYY" → Jan 1', () => {
        expect(extractDeadline('Will GPT-5 ship before 2026?')).toEqual({
            iso: '2026-01-01',
            label: 'before 2026',
        });
    });

    it('pattern 3: "by end of YYYY" → Dec 31', () => {
        expect(extractDeadline('Will it launch by end of 2028?')).toEqual({
            iso: '2028-12-31',
            label: 'by end of 2028',
        });
    });

    it('pattern 3: "in YYYY" → Dec 31', () => {
        expect(extractDeadline('Will a recession happen in 2027?')).toEqual({
            iso: '2027-12-31',
            label: 'by end of 2027',
        });
    });

    it('pattern 4: "by Month YYYY" → last day of that month', () => {
        expect(extractDeadline('Will the merger close by June 2027?')).toEqual({
            iso: '2027-06-30',
            label: 'by June 2027',
        });
    });

    it('pattern 4: February of a leap year → Feb 29', () => {
        expect(extractDeadline('Will it resolve by February 2028?')).toEqual({
            iso: '2028-02-29',
            label: 'by February 2028',
        });
    });

    it('pattern 4: February of a non-leap year → Feb 28', () => {
        expect(extractDeadline('Will it resolve by February 2027?')).toEqual({
            iso: '2027-02-28',
            label: 'by February 2027',
        });
    });

    it('returns null when no deadline is present', () => {
        expect(extractDeadline('Will the project succeed?')).toBeNull();
    });

    describe('relative years (clock pinned to 2026-05-17)', () => {
        beforeEach(() => {
            vi.useFakeTimers();
            vi.setSystemTime(new Date('2026-05-17T12:00:00Z'));
        });

        afterEach(() => {
            vi.useRealTimers();
        });

        it('pattern 5: "this year" → Dec 31 of the current year', () => {
            expect(extractDeadline('Will inflation cool down this year?')).toEqual({
                iso: '2026-12-31',
                label: 'by end of 2026',
            });
        });

        it('pattern 5: "next year" → Dec 31 of the next year', () => {
            expect(extractDeadline('Will the team relocate next year?')).toEqual({
                iso: '2027-12-31',
                label: 'by end of 2027',
            });
        });
    });
});
