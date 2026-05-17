import { AlertCircle } from 'lucide-react';

interface GapsNoticeProps {
    gaps: string[];
}

export function GapsNotice({ gaps }: GapsNoticeProps) {
    if (gaps.length === 0) {
        return null;
    }

    return (
        <div className="rounded-xl bg-amber-50/50 p-4">
            <div className="flex items-center gap-2">
                <AlertCircle className="h-4 w-4 shrink-0 text-amber-500/80" aria-hidden />
                <p className="text-sm font-medium text-amber-900">What I couldn't verify</p>
            </div>
            <ul className="mt-2 space-y-1 pl-6 text-sm leading-relaxed text-amber-900/80">
                {gaps.map((gap) => (
                    <li key={gap} className="list-disc break-words">
                        {gap}
                    </li>
                ))}
            </ul>
        </div>
    );
}
