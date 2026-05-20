import { Button } from '../ui/button';

interface ClosingCTAProps {
    onPrimary: () => void;
}

export function ClosingCTA({ onPrimary }: ClosingCTAProps) {
    return (
        <section className="w-full px-6 py-24 lg:py-32">
            <div className="max-w-3xl mx-auto text-center">
                <h2 className="text-3xl sm:text-4xl lg:text-5xl font-light leading-tight tracking-[-0.02em] text-gray-900">
                    Start your first forecast.
                </h2>
                <p className="mt-5 text-[15px] text-slate-500">
                    Free during private beta. No credit card required.
                </p>
                <div className="mt-10 flex justify-center">
                    <Button
                        onClick={onPrimary}
                        className="h-12 px-7 text-[15px] font-medium bg-gray-900 text-white hover:bg-gray-800 shadow-[0_1px_2px_rgba(15,23,42,0.08)] rounded-lg"
                    >
                        Get started
                    </Button>
                </div>
            </div>
        </section>
    );
}
