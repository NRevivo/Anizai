import * as React from "react"
import { cn } from "../../lib/utils"

export interface ButtonProps
    extends React.ButtonHTMLAttributes<HTMLButtonElement> {
    variant?: 'default' | 'outline' | 'ghost' | 'primary'
    size?: 'default' | 'sm' | 'lg'
}

const Button = React.forwardRef<HTMLButtonElement, ButtonProps>(
    ({ className, variant = 'default', size = 'default', ...props }, ref) => {
        return (
            <button
                className={cn(
                    "inline-flex items-center justify-center rounded-md font-medium transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-anizai-blue-500 disabled:pointer-events-none disabled:opacity-50",
                    {
                        'bg-gray-900 text-white hover:bg-gray-800': variant === 'default',
                        'border border-gray-300 bg-white hover:bg-gray-50': variant === 'outline',
                        'hover:bg-gray-100': variant === 'ghost',
                        'bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 text-white hover:opacity-90': variant === 'primary',
                    },
                    {
                        'h-10 px-4 py-2 text-sm': size === 'default',
                        'h-9 px-3 text-xs': size === 'sm',
                        'h-11 px-8 text-base': size === 'lg',
                    },
                    className
                )}
                ref={ref}
                {...props}
            />
        )
    }
)
Button.displayName = "Button"

export { Button }
