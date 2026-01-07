import { GoogleAuthButton } from '../auth/GoogleAuthButton';

interface FinalCTAProps {
    onAuth: () => void;
}

export function FinalCTA({ onAuth }: FinalCTAProps) {
    return (
        <section className="w-full bg-white py-24 px-6">
            <div className="max-w-3xl mx-auto text-center">
                <h2 className="text-3xl font-semibold text-gray-900 mb-6">
                    Start Forecasting Today
                </h2>
                <p className="text-lg text-gray-600 mb-10">
                    Join Anizai and gain clarity on the events that matter to you
                </p>
                <GoogleAuthButton onClick={onAuth}>
                    Get started with Google
                </GoogleAuthButton>
            </div>
        </section>
    );
}
