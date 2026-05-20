interface FooterProps {
    onContact?: () => void;
    onNavigation?: {
        features: () => void;
        methodology: () => void;
        about: () => void;
        terms: () => void;
        privacy: () => void;
        cookies: () => void;
        pricing: () => void;
    };
}

export function Footer({ onContact, onNavigation }: FooterProps) {
    return (
        <footer className="border-t border-slate-200/70 pt-16 pb-12 px-6">
            <div className="max-w-6xl mx-auto grid grid-cols-2 md:grid-cols-4 gap-8 mb-12">
                <div className="col-span-2 md:col-span-1">
                    <div className="flex items-center gap-2 mb-5">
                        <img src="/logo-brain.png" alt="Anizai" className="h-7 w-auto opacity-80" />
                        <span className="text-base font-medium tracking-tight text-gray-900">
                            Anizai
                        </span>
                    </div>
                    <p className="text-[13px] leading-[1.65] text-slate-500 max-w-xs">
                        Decision-grade forecasts. Probability, confidence, drivers and
                        headwinds — with every piece of evidence cited.
                    </p>
                </div>

                <FooterColumn title="Product">
                    <FooterLink onClick={() => onNavigation?.features()}>Features</FooterLink>
                    <FooterLink onClick={() => onNavigation?.methodology()}>Methodology</FooterLink>
                    <FooterLink onClick={() => onNavigation?.pricing()}>Pricing</FooterLink>
                </FooterColumn>

                <FooterColumn title="Company">
                    <FooterLink onClick={() => onNavigation?.about()}>About</FooterLink>
                    <FooterLink onClick={() => onContact?.()}>Contact</FooterLink>
                </FooterColumn>

                <FooterColumn title="Legal">
                    <FooterLink onClick={() => onNavigation?.terms()}>Terms</FooterLink>
                    <FooterLink onClick={() => onNavigation?.privacy()}>Privacy</FooterLink>
                    <FooterLink onClick={() => onNavigation?.cookies()}>Cookies</FooterLink>
                </FooterColumn>
            </div>

            <div className="max-w-6xl mx-auto pt-8 border-t border-slate-200/70 flex items-center justify-between gap-4">
                <p className="text-[12.5px] text-slate-400">
                    © 2026 Anizai Inc. All rights reserved.
                </p>
                <p className="text-[12.5px] text-slate-400 hidden sm:block">
                    Private beta
                </p>
            </div>
        </footer>
    );
}

function FooterColumn({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <div>
            <h3 className="text-[10.5px] font-medium uppercase tracking-[0.14em] text-slate-500 mb-4">
                {title}
            </h3>
            <ul className="space-y-3">{children}</ul>
        </div>
    );
}

function FooterLink({ onClick, children }: { onClick?: () => void; children: React.ReactNode }) {
    return (
        <li>
            <button
                onClick={onClick}
                className="text-[13.5px] text-slate-600 hover:text-gray-900 transition-colors text-left"
            >
                {children}
            </button>
        </li>
    );
}
