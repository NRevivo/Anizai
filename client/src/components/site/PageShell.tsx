import { Footer } from '../landing/Footer';

export type NavSlug = 'features' | 'methodology' | 'pricing' | 'about' | 'contact';

export interface PageShellProps {
    children: React.ReactNode;
    onHome: () => void;
    onSignIn: () => void;
    onSignUp: () => void;
    onContact?: () => void;
    onNavigation?: {
        home: () => void;
        features: () => void;
        methodology: () => void;
        about: () => void;
        terms: () => void;
        privacy: () => void;
        cookies: () => void;
        pricing: () => void;
    };
    /** Highlights the matching item in the center nav. */
    activeNav?: NavSlug;
    /** When true, the right-side header buttons switch to the signed-in pair. */
    isSignedIn?: boolean;
    /** Called by the "Open workspace" button when signed in. */
    onOpenWorkspace?: () => void;
    /** Called by the "Sign out" link when signed in. */
    onSignOut?: () => void;
}

interface NavLink {
    slug: NavSlug;
    label: string;
}

const NAV_LINKS: NavLink[] = [
    { slug: 'features', label: 'Features' },
    { slug: 'methodology', label: 'Methodology' },
    { slug: 'pricing', label: 'Pricing' },
    { slug: 'about', label: 'About' },
    { slug: 'contact', label: 'Contact' },
];

/**
 * The shared chrome for all public marketing/legal pages — same translucent
 * sticky nav, lavender→teal background gradient, and footer as the landing.
 * This is what makes the sub-pages feel like the same product as the
 * dashboard and landing.
 */
export function PageShell({
    children,
    onHome,
    onSignIn,
    onSignUp,
    onContact,
    onNavigation,
    activeNav,
    isSignedIn,
    onOpenWorkspace,
    onSignOut,
}: PageShellProps) {
    const handleNavClick = (slug: NavSlug) => {
        if (slug === 'contact') {
            onContact?.();
            return;
        }
        onNavigation?.[slug]?.();
    };

    return (
        <div
            className="w-full h-screen overflow-y-auto bg-slate-50 font-sans text-gray-900"
            style={{
                backgroundImage:
                    'radial-gradient(ellipse 55% 45% at 0% 0%, rgba(168,85,247,0.06), transparent 70%), radial-gradient(ellipse 55% 45% at 100% 100%, rgba(20,184,166,0.06), transparent 70%)',
            }}
        >
            <header className="sticky top-0 z-30 w-full backdrop-blur-md bg-slate-50/70 border-b border-slate-200/60">
                <div className="max-w-6xl mx-auto px-6 h-16 flex items-center justify-between gap-6">
                    <button
                        onClick={onHome}
                        className="flex items-center gap-2 shrink-0"
                        aria-label="Anizai home"
                    >
                        <img src="/logo-brain.png" alt="" className="h-7 w-auto" />
                        <span className="text-base font-medium tracking-tight text-gray-900">
                            Anizai
                        </span>
                    </button>

                    <nav className="hidden lg:flex items-center gap-1" aria-label="Primary">
                        {NAV_LINKS.map((link) => {
                            const isActive = activeNav === link.slug;
                            return (
                                <button
                                    key={link.slug}
                                    onClick={() => handleNavClick(link.slug)}
                                    aria-current={isActive ? 'page' : undefined}
                                    className={`h-9 px-3 inline-flex items-center text-[13.5px] font-medium rounded-md transition-colors ${
                                        isActive
                                            ? 'text-gray-900 bg-slate-900/[0.04]'
                                            : 'text-slate-600 hover:text-gray-900 hover:bg-slate-900/[0.03]'
                                    }`}
                                >
                                    {link.label}
                                </button>
                            );
                        })}
                    </nav>

                    <div className="flex items-center gap-2 sm:gap-3 shrink-0">
                        {isSignedIn ? (
                            <>
                                <button
                                    onClick={onSignOut}
                                    className="hidden sm:inline-flex h-9 px-3 items-center text-[14px] font-medium text-slate-600 hover:text-gray-900 transition-colors"
                                >
                                    Sign out
                                </button>
                                <button
                                    onClick={onOpenWorkspace}
                                    className="h-9 px-4 inline-flex items-center text-[14px] font-medium bg-gray-900 text-white hover:bg-gray-800 transition-colors rounded-md shadow-[0_1px_2px_rgba(15,23,42,0.08)]"
                                >
                                    Open workspace
                                </button>
                            </>
                        ) : (
                            <>
                                <button
                                    onClick={onSignIn}
                                    className="hidden sm:inline-flex h-9 px-3 items-center text-[14px] font-medium text-slate-600 hover:text-gray-900 transition-colors"
                                >
                                    Sign in
                                </button>
                                <button
                                    onClick={onSignUp}
                                    className="h-9 px-4 inline-flex items-center text-[14px] font-medium bg-gray-900 text-white hover:bg-gray-800 transition-colors rounded-md shadow-[0_1px_2px_rgba(15,23,42,0.08)]"
                                >
                                    Get started
                                </button>
                            </>
                        )}
                    </div>
                </div>

                <nav
                    className="lg:hidden border-t border-slate-200/60 bg-slate-50/40"
                    aria-label="Primary"
                >
                    <div className="max-w-6xl mx-auto px-2 sm:px-4 h-11 flex items-center gap-1 overflow-x-auto no-scrollbar">
                        {NAV_LINKS.map((link) => {
                            const isActive = activeNav === link.slug;
                            return (
                                <button
                                    key={link.slug}
                                    onClick={() => handleNavClick(link.slug)}
                                    aria-current={isActive ? 'page' : undefined}
                                    className={`h-8 px-3 inline-flex items-center text-[13px] font-medium rounded-md shrink-0 transition-colors ${
                                        isActive
                                            ? 'text-gray-900 bg-slate-900/[0.05]'
                                            : 'text-slate-600 hover:text-gray-900'
                                    }`}
                                >
                                    {link.label}
                                </button>
                            );
                        })}
                    </div>
                </nav>
            </header>

            <main>{children}</main>

            <Footer onContact={onContact} onNavigation={onNavigation} />
        </div>
    );
}

interface PageHeaderProps {
    eyebrow: string;
    eyebrowDot?: 'purple' | 'teal';
    title: React.ReactNode;
    description?: React.ReactNode;
    children?: React.ReactNode;
}

/**
 * Standard page-header treatment — eyebrow + H1 + description. The same
 * pattern the landing uses for section heads, scaled up for top-of-page.
 */
export function PageHeader({
    eyebrow,
    eyebrowDot = 'purple',
    title,
    description,
    children,
}: PageHeaderProps) {
    return (
        <section className="w-full px-6 pt-16 pb-12 lg:pt-20 lg:pb-16">
            <div className="max-w-5xl mx-auto">
                <div className="mb-5 inline-flex items-center gap-2">
                    <span
                        className={`h-1.5 w-1.5 rounded-full ${
                            eyebrowDot === 'teal'
                                ? 'bg-anizai-teal-500'
                                : 'bg-anizai-purple-400'
                        }`}
                        aria-hidden
                    />
                    <span className="text-[11px] font-medium uppercase tracking-[0.16em] text-slate-500">
                        {eyebrow}
                    </span>
                </div>
                <h1 className="text-4xl sm:text-5xl lg:text-[3.5rem] font-medium leading-[1.05] tracking-[-0.03em] text-gray-900 max-w-3xl">
                    {title}
                </h1>
                {description ? (
                    <p className="mt-6 max-w-2xl text-[17px] leading-[1.65] text-slate-600">
                        {description}
                    </p>
                ) : null}
                {children ? <div className="mt-8">{children}</div> : null}
            </div>
        </section>
    );
}
