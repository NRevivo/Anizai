/**
 * Building blocks used by the auth pages (LoginPage, SignupPage). The
 * outer chrome (sticky nav, gradient background, footer) is provided by
 * the shared PageShell — these exports only handle the centered card and
 * its form fields.
 */

interface AuthCardProps {
    eyebrow: string;
    title: string;
    description?: string;
    children: React.ReactNode;
    footer?: React.ReactNode;
}

/**
 * Centered card used by both LoginPage and SignupPage — same elevation
 * language as the dashboard floating cards.
 */
export function AuthCard({ eyebrow, title, description, children, footer }: AuthCardProps) {
    return (
        <div>
            <div className="rounded-2xl bg-white p-7 sm:p-8 ring-1 ring-slate-900/[0.05] shadow-[0_8px_30px_-12px_rgba(15,23,42,0.12),0_2px_6px_-2px_rgba(15,23,42,0.06)]">
                <div className="mb-7">
                    <div className="mb-3 inline-flex items-center gap-2">
                        <span className="h-1.5 w-1.5 rounded-full bg-anizai-purple-400" />
                        <span className="text-[10.5px] font-medium uppercase tracking-[0.16em] text-slate-500">
                            {eyebrow}
                        </span>
                    </div>
                    <h1 className="text-2xl font-medium tracking-tight text-gray-900">
                        {title}
                    </h1>
                    {description ? (
                        <p className="mt-2 text-[14px] leading-[1.6] text-slate-500">
                            {description}
                        </p>
                    ) : null}
                </div>

                {children}
            </div>

            {footer ? (
                <div className="mt-5 text-center">{footer}</div>
            ) : null}
        </div>
    );
}

export function AuthDivider({ label = 'or' }: { label?: string }) {
    return (
        <div className="relative flex items-center justify-center my-5">
            <div className="absolute inset-0 flex items-center">
                <div className="w-full border-t border-slate-200" />
            </div>
            <span className="relative bg-white px-3 text-[10.5px] font-medium text-slate-400 uppercase tracking-[0.14em]">
                {label}
            </span>
        </div>
    );
}

interface AuthInputProps {
    id: string;
    label: string;
    type?: string;
    value: string;
    onChange: (e: React.ChangeEvent<HTMLInputElement>) => void;
    placeholder?: string;
    autoFocus?: boolean;
}

export function AuthInput({
    id,
    label,
    type = 'text',
    value,
    onChange,
    placeholder,
    autoFocus,
}: AuthInputProps) {
    return (
        <div>
            <label htmlFor={id} className="block text-[12px] font-medium text-slate-600">
                {label}
            </label>
            <input
                id={id}
                name={id}
                type={type}
                value={value}
                onChange={onChange}
                placeholder={placeholder ?? label}
                autoFocus={autoFocus}
                required
                className="mt-2 w-full rounded-lg border border-slate-200 bg-white px-4 py-2.5 text-[14.5px] text-gray-900 placeholder:text-slate-400 focus:border-anizai-purple-400 focus:ring-2 focus:ring-anizai-purple-100 outline-none transition"
            />
        </div>
    );
}
