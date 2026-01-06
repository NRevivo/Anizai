import * as React from "react"
import { cn } from "../../lib/utils"

export interface BadgeProps extends React.HTMLAttributes<HTMLDivElement> {
    variant?: 'default' | 'stable' | 'volatile'
}

function Badge({ className, variant = 'default', ...props }: BadgeProps) {
    return (
        <div
            className={cn(
                "inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-semibold transition-colors",
                {
                    'bg-gray-100 text-gray-800': variant === 'default',
                    'bg-green-100 text-green-800': variant === 'stable',
                    'bg-amber-100 text-amber-800': variant === 'volatile',
                },
                className
            )}
            {...props}
        />
    )
}

export { Badge }
