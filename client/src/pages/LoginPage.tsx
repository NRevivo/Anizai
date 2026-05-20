import { useState } from 'react';
import { GoogleAuthButton } from '../components/auth/GoogleAuthButton';
import { Button } from '../components/ui/button';
import { PageShell, type PageShellProps } from '../components/site/PageShell';
import {
    AuthCard,
    AuthDivider,
    AuthInput,
} from '../components/site/AuthShell';

interface LoginPageProps extends Omit<PageShellProps, 'children'> {
    onGoogleAuth: () => void;
    onEmailAuth: (email: string, password?: string) => void;
}

export function LoginPage(props: LoginPageProps) {
    const { onGoogleAuth, onEmailAuth, onSignUp, ...shellProps } = props;
    const [email, setEmail] = useState('');
    const [password, setPassword] = useState('');
    const [isEmailMode, setIsEmailMode] = useState(false);

    const handleEmailSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (email.trim() && password.trim()) {
            onEmailAuth(email, password);
        }
    };

    const isDisabled = !email.trim() || !password.trim();

    return (
        <PageShell {...shellProps} onSignUp={onSignUp}>
            <section className="w-full px-6 py-16 lg:py-20 flex items-start justify-center">
                <div className="w-full max-w-[420px]">
                    <AuthCard
                        eyebrow="Sign in"
                        title="Welcome back."
                        description="Continue to your Anizai workspace."
                        footer={
                            <p className="text-[13px] text-slate-500">
                                Don’t have an account?{' '}
                                <button
                                    type="button"
                                    onClick={onSignUp}
                                    className="text-gray-900 font-medium hover:underline underline-offset-4"
                                >
                                    Sign up
                                </button>
                            </p>
                        }
                    >
                        <div className="space-y-3">
                            <GoogleAuthButton onClick={onGoogleAuth}>
                                Continue with Google
                            </GoogleAuthButton>
                        </div>

                        <AuthDivider />

                        {!isEmailMode ? (
                            <Button
                                onClick={() => setIsEmailMode(true)}
                                className="w-full h-12 text-[14.5px] font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-lg shadow-[0_1px_2px_rgba(15,23,42,0.08)]"
                            >
                                Continue with email
                            </Button>
                        ) : (
                            <form onSubmit={handleEmailSubmit} className="space-y-4">
                                <AuthInput
                                    id="email"
                                    label="Email"
                                    type="email"
                                    value={email}
                                    onChange={(e) => setEmail(e.target.value)}
                                    placeholder="you@company.com"
                                    autoFocus
                                />
                                <AuthInput
                                    id="password"
                                    label="Password"
                                    type="password"
                                    value={password}
                                    onChange={(e) => setPassword(e.target.value)}
                                    placeholder="••••••••"
                                />
                                <Button
                                    type="submit"
                                    disabled={isDisabled}
                                    className="w-full h-12 text-[14.5px] font-medium bg-gray-900 text-white hover:bg-gray-800 rounded-lg shadow-[0_1px_2px_rgba(15,23,42,0.08)] disabled:opacity-50"
                                >
                                    Continue
                                </Button>
                            </form>
                        )}

                        <p className="mt-7 text-[11.5px] text-slate-400 leading-[1.6]">
                            By continuing, you agree to Anizai’s Terms of Service and Privacy
                            Policy.
                        </p>
                    </AuthCard>
                </div>
            </section>
        </PageShell>
    );
}
