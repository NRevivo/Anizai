import { type ClassValue, clsx } from "clsx"
import { twMerge } from "tailwind-merge"

export function cn(...inputs: ClassValue[]) {
    return twMerge(clsx(inputs))
}

/**
 * A UUID v4, safe on insecure origins.
 *
 * `crypto.randomUUID` only exists in a **secure context** — https or localhost.
 * Opening the dev server from a phone on the LAN (`http://192.168.x.x:5173`) is not
 * one, so it is `undefined` there. Calling it during render then threw and unmounted
 * the whole React tree: a blank white screen on mobile and nowhere else, which is
 * exactly how this surfaced.
 *
 * `crypto.getRandomValues` has no such restriction, so the fallback keeps real
 * randomness (no `Math.random` — these are idempotency keys, and a collision would
 * silently return someone else's forecast).
 */
export function randomUUID(): string {
    if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
        return crypto.randomUUID();
    }

    const bytes = new Uint8Array(16);
    crypto.getRandomValues(bytes);
    bytes[6] = (bytes[6] & 0x0f) | 0x40; // version 4
    bytes[8] = (bytes[8] & 0x3f) | 0x80; // variant 10xx
    const hex = Array.from(bytes, (b) => b.toString(16).padStart(2, '0')).join('');
    return `${hex.slice(0, 8)}-${hex.slice(8, 12)}-${hex.slice(12, 16)}-${hex.slice(16, 20)}-${hex.slice(20)}`;
}

/** Accepts a 0–1 float and returns a display string like "74.3%" */
export function formatProbability(probability: number): string {
    return `${(probability * 100).toFixed(1)}%`;
}

export function formatDate(date: Date): string {
    return new Intl.DateTimeFormat('en-US', {
        month: 'short',
        day: 'numeric',
        year: 'numeric'
    }).format(date);
}

export function formatRelativeTime(date: Date): string {
    const now = new Date();
    const diffMs = now.getTime() - date.getTime();
    const diffMins = Math.floor(diffMs / 60000);
    const diffHours = Math.floor(diffMs / 3600000);
    const diffDays = Math.floor(diffMs / 86400000);

    if (diffMins < 1) return 'Just now';
    if (diffMins < 60) return `${diffMins}m ago`;
    if (diffHours < 24) return `${diffHours}h ago`;
    if (diffDays < 7) return `${diffDays}d ago`;
    return formatDate(date);
}
