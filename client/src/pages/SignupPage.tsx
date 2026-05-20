import { useState } from 'react';
import { GoogleAuthButton } from '../components/auth/GoogleAuthButton';
import { Button } from '../components/ui/button';
import { PageShell, type PageShellProps } from '../components/site/PageShell';
import {
    AuthCard,
    AuthDivider,
    AuthInput,
} from '../components/site/AuthShell';

interface SignupPageProps extends Omit<PageShellProps, 'children'> {
    onCreateAccount: (payload: { name: string; email: string; password: string }) => void;
    onGoogleSignup: () => void;
}

export function SignupPage(props: SignupPageProps) {
    const { onCreateAccount, onGoogleSignup, onSignIn, ...shellProps } = props;
    const [name, setName] = useState('');
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (name.trim() && email.trim() && password.trim()) {
            onCreateAccount({ name, email, password });
        }
    };

    const isDisabled = !name.trim() || !email.trim() || !password.trim();

    return (
        <PageShell {...shellProps} onSignIn={onSignIn}>
            <section className="w-full px-6 py-16 lg:py-20 flex items-start justify-center">
                <div className="w-full max-w-[420px]">
                    <AuthCard
                        eyebrow="Create account"
                        title="Start forecasting."
                        description="Free during private beta. No credit card required."
                        footer={
                            <p className="text-[13px] text-slate-500">
                                Already have an account?{' '}
                                <button
                                    type="button"
                                    onClick={onSignIn}
                                    className="text-gray-900 font-medium hover:underline underline-offset-4"
                                >
                                    Sign in
                                </button>
                            </p>
                        }
                    >
                        <div className="space-y-3">
                            <GoogleAuthButton onClick={onGoogleSignup}>
                                Continue with Google
                            </GoogleAuthButton>
                        </div>

                        <AuthDivider />

                        <form onSubmit={handleSubmit} className="space-y-4">
                            <AuthInput
                                id="name"
                                label="Full name"
                                value={name}
                                onChange={(e) => setName(e.target.value)}
                                placeholder="Ada Lovelace"
                                autoFocus
                            />
                            <AuthInput
                                id="email"
                                label="Email"
                                type="email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                placeholder="you@company.com"
                            />
                            <AuthInput
                                id="password"
                                label="Password"
                                type="password"
                                value={password}
                                onChange={(e) => setPassword(e.target.value)}
                                placeholder="At least 8 characters"
                            />
                            <Button
                                type="submit"
                                disabled={isDisabled}
                                className="w-full h-12 text-[14.5px] font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-lg shadow-[0_1px_2px_rgba(15,23,42,0.08)] disabled:opacity-50"
                            >
                                Create account
                            </Button>
                        </form>

                        <p className="mt-6 text-[11.5px] text-slate-400 leading-[1.6]">
                            By creating an account, you agree to Anizai’s Terms of Service
                            and Privacy Policy.
                        </p>
                    </AuthCard>
                </div>
            </section>
        </PageShell>
    );
}
