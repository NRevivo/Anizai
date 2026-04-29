import type { ReactNode } from 'react';

interface StateMessageProps {
    title: string;
    description?: string;
    variant?: 'empty' | 'loading' | 'error' | 'warning';
    compact?: boolean;
    align?: 'left' | 'center';
    action?: ReactNode;
}

const variantClasses = {
    empty: 'border-gray-200 bg-gray-50 text-gray-500',
    loading: 'border-anizai-blue-100 bg-anizai-blue-50/40 text-gray-600',
    error: 'border-red-200 bg-red-50 text-red-700',
    warning: 'border-amber-200 bg-amber-50 text-amber-800',
};

export function StateMessage({
    title,
    description,
    variant = 'empty',
    compact = false,
    align = 'left',
    action,
}: StateMessageProps) {
    const isLoading = variant === 'loading';

    return (
        <div
            className={`max-w-full rounded-md border border-dashed ${variantClasses[variant]} ${compact ? 'p-3' : 'p-4'} ${align === 'center' ? 'text-center' : 'text-left'}`}
        >
            <div className={`flex gap-3 ${align === 'center' ? 'flex-col items-center' : 'items-start'}`}>
                {isLoading ? (
                    <span className="mt-0.5 inline-flex h-4 w-4 shrink-0 animate-spin rounded-full border-2 border-anizai-blue-200 border-t-anizai-blue-600" />
                ) : null}
                <div className="min-w-0">
                    <p className={`break-words font-semibold ${compact ? 'text-sm' : 'text-base'} ${variant === 'error' ? 'text-red-800' : variant === 'warning' ? 'text-amber-900' : 'text-gray-900'}`}>
                        {title}
                    </p>
                    {description ? (
                        <p className={`mt-1 break-words leading-relaxed ${compact ? 'text-xs' : 'text-sm'}`}>
                            {description}
                        </p>
                    ) : null}
                    {action ? <div className="mt-3">{action}</div> : null}
                </div>
            </div>
        </div>
    );
}
