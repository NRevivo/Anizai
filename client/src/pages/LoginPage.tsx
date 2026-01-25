import { useState } from 'react';
import { GoogleAuthButton } from '../components/auth/GoogleAuthButton';
import { Input } from '../components/ui/input';
import { Button } from '../components/ui/button';
import { cn } from '../lib/utils';

interface LoginPageProps {
    onGoogleAuth: () => void;
    onEmailAuth: (email: string) => void;
    onBack: () => void;
}

export function LoginPage({ onGoogleAuth, onEmailAuth, onBack }: LoginPageProps) {
    const [email, setEmail] = useState('');
    const [isEmailMode, setIsEmailMode] = useState(false);

    const handleEmailSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        if (email.trim()) {
            onEmailAuth(email);
        }
    };

    return (
        <div className="min-h-screen w-full bg-gradient-to-br from-slate-50 via-white to-anizai-teal-50/20 flex flex-col items-center justify-center px-4">
            {/* Back Button */}
            <button
                onClick={onBack}
                className="absolute top-6 left-6 flex items-center gap-2 text-gray-500 hover:text-gray-700 transition-colors text-sm font-medium"
            >
                <svg className="w-4 h-4" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                </svg>
                Back
            </button>

            {/* Main Content */}
            <div className="w-full max-w-[400px] flex flex-col items-center">
                {/* Logo */}
                <div className="mb-8">
                    <img
                        src="/logo-brain.png"
                        alt="Anizai"
                        className="w-30 h-30 object-contain mix-blend-multiply"
                    />
                </div>

                {/* Title */}
                <h1 className="text-2xl font-semibold text-gray-900 mb-2 text-center">
                    Welcome back
                </h1>
                <p className="text-gray-500 text-sm mb-8 text-center">
                    Sign in to continue to Anizai
                </p>

                {/* Auth Options */}
                <div className="w-full space-y-4">
                    {/* Google Auth */}
                    <GoogleAuthButton onClick={onGoogleAuth}>
                        Continue with Google
                    </GoogleAuthButton>

                    {/* Divider */}
                    <div className="relative flex items-center justify-center my-6">
                        <div className="absolute inset-0 flex items-center">
                            <div className="w-full border-t border-gray-200" />
                        </div>
                        <span className="relative bg-white px-4 text-xs font-medium text-gray-400 uppercase tracking-wider">
                            or
                        </span>
                    </div>

                    {/* Email Auth */}
                    {!isEmailMode ? (
                        <Button
                            onClick={() => setIsEmailMode(true)}
                            className={cn(
                                "w-full h-12 text-base font-medium",
                                "bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500",
                                "hover:from-anizai-teal-600 hover:via-anizai-blue-600 hover:to-anizai-purple-600",
                                "text-white border-0 shadow-md hover:shadow-lg transition-all duration-200"
                            )}
                        >
                            Continue with Email
                        </Button>
                    ) : (
                        <form onSubmit={handleEmailSubmit} className="space-y-4">
                            <Input
                                type="email"
                                placeholder="Enter your email"
                                value={email}
                                onChange={(e) => setEmail(e.target.value)}
                                className="h-12 text-base focus:ring-2 focus:ring-anizai-blue-500"
                                autoFocus
                            />
                            <Button
                                type="submit"
                                disabled={!email.trim()}
                                className={cn(
                                    "w-full h-12 text-base font-medium",
                                    "bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500",
                                    "hover:from-anizai-teal-600 hover:via-anizai-blue-600 hover:to-anizai-purple-600",
                                    "text-white border-0 shadow-md hover:shadow-lg transition-all duration-200",
                                    "disabled:opacity-50 disabled:cursor-not-allowed"
                                )}
                            >
                                Continue
                            </Button>
                        </form>
                    )}
                </div>

                {/* Terms */}
                <p className="mt-8 text-xs text-gray-400 text-center leading-relaxed">
                    By continuing, you agree to Anizai's{' '}
                    <a href="#" className="text-anizai-blue-500 hover:underline">Terms of Service</a>
                    {' '}and{' '}
                    <a href="#" className="text-anizai-blue-500 hover:underline">Privacy Policy</a>
                </p>
            </div>
        </div>
    );
}
