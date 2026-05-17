export type Deadline = { iso: string; label: string };

const MONTHS = [
    'january',
    'february',
    'march',
    'april',
    'may',
    'june',
    'july',
    'august',
    'september',
    'october',
    'november',
    'december',
];

function lastDayOfMonth(year: number, monthIndex: number): string {
    const day = new Date(year, monthIndex + 1, 0).getDate();
    const mm = String(monthIndex + 1).padStart(2, '0');
    const dd = String(day).padStart(2, '0');
    return `${year}-${mm}-${dd}`;
}

/**
 * Best-effort deadline extraction from a forecast question. Heuristic only —
 * no LLM call. Patterns are tried in order; the first match wins.
 */
export function extractDeadline(question: string): Deadline | null {
    const q = question.toLowerCase();

    // 1. Explicit ISO date (YYYY-MM-DD).
    const isoMatch = q.match(/\b(\d{4}-\d{2}-\d{2})\b/);
    if (isoMatch) {
        return { iso: isoMatch[1], label: isoMatch[1] };
    }

    // 2. "before YYYY".
    const beforeMatch = q.match(/\bbefore\s+(\d{4})\b/);
    if (beforeMatch) {
        const year = beforeMatch[1];
        return { iso: `${year}-01-01`, label: `before ${year}` };
    }

    // 3. "by end of YYYY" / "in YYYY".
    const endOfMatch = q.match(/\b(?:by\s+end\s+of|in)\s+(\d{4})\b/);
    if (endOfMatch) {
        const year = endOfMatch[1];
        return { iso: `${year}-12-31`, label: `by end of ${year}` };
    }

    // 4. "by Month YYYY".
    const monthMatch = q.match(
        /\bby\s+(january|february|march|april|may|june|july|august|september|october|november|december)\s+(\d{4})\b/
    );
    if (monthMatch) {
        const monthIndex = MONTHS.indexOf(monthMatch[1]);
        const year = Number(monthMatch[2]);
        const monthName = monthMatch[1][0].toUpperCase() + monthMatch[1].slice(1);
        return { iso: lastDayOfMonth(year, monthIndex), label: `by ${monthName} ${year}` };
    }

    // 5. "this year" / "next year" — relative to the current date.
    if (/\bthis\s+year\b/.test(q)) {
        const year = new Date().getFullYear();
        return { iso: `${year}-12-31`, label: `by end of ${year}` };
    }
    if (/\bnext\s+year\b/.test(q)) {
        const year = new Date().getFullYear() + 1;
        return { iso: `${year}-12-31`, label: `by end of ${year}` };
    }

    // 6. No match.
    return null;
}
