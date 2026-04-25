import { useState, useCallback } from 'react';
import { updateUserPlan } from '../../services/user.service';
import type { UserProfile, UserPlan } from '../../services/user.service';

// ─── Card Validation Helpers ──────────────────────────────────────────────────

function luhnCheck(num: string): boolean {
    const digits = num.replace(/\D/g, '').split('').map(Number);
    let sum = 0;
    let shouldDouble = false;
    for (let i = digits.length - 1; i >= 0; i--) {
        let d = digits[i];
        if (shouldDouble) {
            d *= 2;
            if (d > 9) d -= 9;
        }
        sum += d;
        shouldDouble = !shouldDouble;
    }
    return sum % 10 === 0;
}

function formatCardNumber(raw: string): string {
    return raw
        .replace(/\D/g, '')
        .slice(0, 16)
        .replace(/(.{4})/g, '$1 ')
        .trim();
}

function formatExpiry(raw: string): string {
    const digits = raw.replace(/\D/g, '').slice(0, 4);
    if (digits.length >= 3) return `${digits.slice(0, 2)}/${digits.slice(2)}`;
    return digits;
}

function detectCardType(num: string): string {
    const n = num.replace(/\D/g, '');
    if (/^4/.test(n)) return 'Visa';
    if (/^5[1-5]/.test(n) || /^2[2-7]/.test(n)) return 'Mastercard';
    if (/^3[47]/.test(n)) return 'Amex';
    if (/^6/.test(n)) return 'Discover';
    return '';
}

interface CardData {
    name: string;
    number: string;
    expiry: string;
    cvv: string;
}

interface FormErrors {
    name?: string;
    number?: string;
    expiry?: string;
    cvv?: string;
}

function validateCard(card: CardData): FormErrors {
    const errors: FormErrors = {};
    const numberClean = card.number.replace(/\D/g, '');
    const nameTrim = card.name.trim();

    if (!nameTrim || nameTrim.length < 2) {
        errors.name = 'Enter the cardholder name as it appears on the card.';
    }
    if (numberClean.length < 13 || numberClean.length > 19) {
        errors.number = 'Card number must be 13–19 digits.';
    } else if (!luhnCheck(numberClean)) {
        errors.number = 'Card number is invalid. Please check and try again.';
    }
    // Expiry
    const expiryRaw = card.expiry.replace(/\D/g, '');
    if (expiryRaw.length !== 4) {
        errors.expiry = 'Enter expiry as MM/YY.';
    } else {
        const month = parseInt(expiryRaw.slice(0, 2), 10);
        const year = parseInt(`20${expiryRaw.slice(2)}`, 10);
        const now = new Date();
        const expDate = new Date(year, month - 1, 1);
        if (month < 1 || month > 12) {
            errors.expiry = 'Month must be between 01 and 12.';
        } else if (expDate < new Date(now.getFullYear(), now.getMonth(), 1)) {
            errors.expiry = 'Card has expired.';
        }
    }
    // CVV
    const cvvClean = card.cvv.replace(/\D/g, '');
    if (cvvClean.length < 3 || cvvClean.length > 4) {
        errors.cvv = 'CVV must be 3–4 digits.';
    }

    return errors;
}

/** Simulates a payment processor. Uses test card numbers for predictable outcomes. */
async function mockProcessPayment(card: CardData): Promise<void> {
    await new Promise(r => setTimeout(r, 1800)); // realistic delay
    const num = card.number.replace(/\D/g, '');
    // Declined patterns for testing
    if (num.startsWith('4000000000000002')) throw new Error('Your card was declined.');
    if (num.startsWith('4000000000009995')) throw new Error('Insufficient funds.');
    if (num.startsWith('4000000000000069')) throw new Error('Card expired.');
    if (num.startsWith('4000000000000127')) throw new Error('Incorrect CVV.');
}

// ─── Plan Badge ───────────────────────────────────────────────────────────────

function PlanBadge({ plan }: { plan: UserPlan }) {
    if (plan === 'premium') {
        return (
            <span className="inline-flex items-center gap-1.5 px-3 py-1 rounded-full text-sm font-semibold bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 text-white shadow-sm">
                <svg className="w-3.5 h-3.5" fill="currentColor" viewBox="0 0 20 20">
                    <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                </svg>
                Premium
            </span>
        );
    }
    return (
        <span className="inline-flex items-center px-3 py-1 rounded-full text-sm font-semibold bg-gray-100 text-gray-600">
            Free
        </span>
    );
}

// ─── Payment Form ─────────────────────────────────────────────────────────────

interface PaymentFormProps {
    onSuccess: (card: CardData) => Promise<void>;
    onCancel: () => void;
    isProcessing: boolean;
}

function PaymentForm({ onSuccess, onCancel, isProcessing }: PaymentFormProps) {
    const [card, setCard] = useState<CardData>({ name: '', number: '', expiry: '', cvv: '' });
    const [errors, setErrors] = useState<FormErrors>({});
    const [touched, setTouched] = useState<Record<string, boolean>>({});

    const cardType = detectCardType(card.number);

    const handleChange = (field: keyof CardData, rawValue: string) => {
        let value = rawValue;
        if (field === 'number') value = formatCardNumber(rawValue);
        if (field === 'expiry') value = formatExpiry(rawValue);
        if (field === 'cvv') value = rawValue.replace(/\D/g, '').slice(0, 4);
        const updated = { ...card, [field]: value };
        setCard(updated);
        if (touched[field]) {
            const newErrors = validateCard(updated);
            setErrors(prev => ({ ...prev, [field]: newErrors[field] }));
        }
    };

    const handleBlur = (field: keyof CardData) => {
        setTouched(prev => ({ ...prev, [field]: true }));
        const newErrors = validateCard(card);
        setErrors(prev => ({ ...prev, [field]: newErrors[field] }));
    };

    const handleSubmit = (e: React.FormEvent) => {
        e.preventDefault();
        setTouched({ name: true, number: true, expiry: true, cvv: true });
        const errs = validateCard(card);
        setErrors(errs);
        if (Object.keys(errs).length > 0) return;
        void onSuccess(card);
    };

    const inputClass = (field: keyof CardData) =>
        `w-full h-11 px-3.5 text-sm border rounded-lg transition-shadow focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:border-transparent ${
            touched[field] && errors[field]
                ? 'border-red-400 bg-red-50'
                : 'border-gray-300 bg-white'
        }`;

    return (
        <form onSubmit={handleSubmit} className="space-y-4">
            {/* Card number */}
            <div>
                <div className="flex items-center justify-between mb-1.5">
                    <label className="text-sm font-medium text-gray-700">Card Number</label>
                    {cardType && <span className="text-xs text-gray-500 font-medium">{cardType}</span>}
                </div>
                <div className="relative">
                    <input
                        id="card-number"
                        type="text"
                        inputMode="numeric"
                        placeholder="1234 5678 9012 3456"
                        value={card.number}
                        onChange={e => handleChange('number', e.target.value)}
                        onBlur={() => handleBlur('number')}
                        disabled={isProcessing}
                        autoComplete="cc-number"
                        className={inputClass('number') + ' pr-10'}
                    />
                    <svg className="absolute right-3 top-3 w-5 h-5 text-gray-300" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                        <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={1.5} d="M3 10h18M7 15h1m4 0h1m-7 4h12a3 3 0 003-3V8a3 3 0 00-3-3H6a3 3 0 00-3 3v8a3 3 0 003 3z" />
                    </svg>
                </div>
                {touched.number && errors.number && <p className="mt-1.5 text-xs text-red-600">{errors.number}</p>}
            </div>

            {/* Name */}
            <div>
                <label className="block text-sm font-medium text-gray-700 mb-1.5">Cardholder Name</label>
                <input
                    id="card-name"
                    type="text"
                    placeholder="Jane Doe"
                    value={card.name}
                    onChange={e => handleChange('name', e.target.value)}
                    onBlur={() => handleBlur('name')}
                    disabled={isProcessing}
                    autoComplete="cc-name"
                    className={inputClass('name')}
                />
                {touched.name && errors.name && <p className="mt-1.5 text-xs text-red-600">{errors.name}</p>}
            </div>

            {/* Expiry + CVV */}
            <div className="grid grid-cols-2 gap-4">
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">Expiry Date</label>
                    <input
                        id="card-expiry"
                        type="text"
                        inputMode="numeric"
                        placeholder="MM/YY"
                        value={card.expiry}
                        onChange={e => handleChange('expiry', e.target.value)}
                        onBlur={() => handleBlur('expiry')}
                        disabled={isProcessing}
                        autoComplete="cc-exp"
                        className={inputClass('expiry')}
                    />
                    {touched.expiry && errors.expiry && <p className="mt-1.5 text-xs text-red-600">{errors.expiry}</p>}
                </div>
                <div>
                    <label className="block text-sm font-medium text-gray-700 mb-1.5">CVV</label>
                    <input
                        id="card-cvv"
                        type="text"
                        inputMode="numeric"
                        placeholder="123"
                        value={card.cvv}
                        onChange={e => handleChange('cvv', e.target.value)}
                        onBlur={() => handleBlur('cvv')}
                        disabled={isProcessing}
                        autoComplete="cc-csc"
                        className={inputClass('cvv')}
                    />
                    {touched.cvv && errors.cvv && <p className="mt-1.5 text-xs text-red-600">{errors.cvv}</p>}
                </div>
            </div>

            {/* Lock note */}
            <div className="flex items-center gap-2 text-xs text-gray-400 pt-1">
                <svg className="w-3.5 h-3.5 shrink-0" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z" />
                </svg>
                Your card details are processed securely and not stored on our servers.
            </div>

            {/* Buttons */}
            <div className="flex items-center gap-3 pt-2">
                <button
                    type="submit"
                    disabled={isProcessing}
                    className="flex-1 flex items-center justify-center gap-2 h-11 px-5 text-sm font-semibold text-white bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 rounded-lg hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:ring-offset-2 disabled:opacity-60 transition-opacity"
                >
                    {isProcessing ? (
                        <>
                            <svg className="animate-spin w-4 h-4" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                            </svg>
                            Processing payment…
                        </>
                    ) : (
                        <>
                            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                            </svg>
                            Upgrade to Premium — $19/mo
                        </>
                    )}
                </button>
                <button
                    type="button"
                    onClick={onCancel}
                    disabled={isProcessing}
                    className="h-11 px-4 text-sm font-medium text-gray-600 border border-gray-300 rounded-lg hover:bg-gray-50 disabled:opacity-50 transition-colors"
                >
                    Cancel
                </button>
            </div>
        </form>
    );
}

// ─── Cancel Confirmation ──────────────────────────────────────────────────────

interface CancelConfirmProps {
    onConfirm: () => void;
    onDismiss: () => void;
    isProcessing: boolean;
}

function CancelConfirm({ onConfirm, onDismiss, isProcessing }: CancelConfirmProps) {
    return (
        <div className="rounded-xl border border-amber-200 bg-amber-50 p-5 space-y-3">
            <div className="flex items-start gap-3">
                <svg className="w-5 h-5 text-amber-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                    <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M12 9v2m0 4h.01m-6.938 4h13.856c1.54 0 2.502-1.667 1.732-3L13.732 4c-.77-1.333-2.694-1.333-3.464 0L3.34 16c-.77 1.333.192 3 1.732 3z" />
                </svg>
                <div>
                    <p className="text-sm font-semibold text-amber-800">Cancel Premium subscription?</p>
                    <p className="text-sm text-amber-700 mt-1">
                        You'll be downgraded to the Free plan immediately and lose access to unlimited forecasts.
                        This action cannot be undone.
                    </p>
                </div>
            </div>
            <div className="flex items-center gap-3">
                <button
                    onClick={onConfirm}
                    disabled={isProcessing}
                    className="flex items-center gap-2 px-4 py-2 text-sm font-semibold text-white bg-red-600 rounded-lg hover:bg-red-700 disabled:opacity-60 transition-colors"
                >
                    {isProcessing ? (
                        <>
                            <svg className="animate-spin w-3.5 h-3.5" fill="none" viewBox="0 0 24 24">
                                <circle className="opacity-25" cx="12" cy="12" r="10" stroke="currentColor" strokeWidth="4" />
                                <path className="opacity-75" fill="currentColor" d="M4 12a8 8 0 018-8v8z" />
                            </svg>
                            Cancelling…
                        </>
                    ) : (
                        'Yes, cancel subscription'
                    )}
                </button>
                <button
                    onClick={onDismiss}
                    disabled={isProcessing}
                    className="px-4 py-2 text-sm font-medium text-gray-600 hover:text-gray-800 disabled:opacity-50 transition-colors"
                >
                    Keep Premium
                </button>
            </div>
        </div>
    );
}

// ─── Main Component ───────────────────────────────────────────────────────────

type View = 'overview' | 'payment-form' | 'cancel-confirm';

interface AlertBanner {
    type: 'success' | 'error';
    title: string;
    message: string;
}

interface SubscriptionSettingsProps {
    userProfile: UserProfile | null;
    onPlanChange: (updated: UserProfile) => void;
}

const PLAN_FEATURES: Record<UserPlan, string[]> = {
    free: [
        '3 forecasts per month',
        'Basic probability analysis',
        'Public trending forecasts',
        'Standard support',
    ],
    premium: [
        'Unlimited forecasts',
        'Advanced multi-source analysis',
        'Priority AI processing',
        'Forecast tracking & alerts',
        'Priority support',
        'Early access to new features',
    ],
};

export function SubscriptionSettings({ userProfile, onPlanChange }: SubscriptionSettingsProps) {
    const [view, setView] = useState<View>('overview');
    const [isProcessing, setIsProcessing] = useState(false);
    const [alert, setAlert] = useState<AlertBanner | null>(null);

    const currentPlan: UserPlan = userProfile?.plan ?? 'free';
    const isPremium = currentPlan === 'premium';
    const isCanceled = isPremium && (userProfile?.cancelAtPeriodEnd ?? false);
    const isActive = isPremium && !isCanceled;
    const formattedExpiresAt = userProfile?.planExpiresAt ? new Date(userProfile.planExpiresAt).toLocaleDateString() : 'N/A';

    const showAlert = (banner: AlertBanner) => {
        setAlert(banner);
        // Auto-dismiss success after 6s
        if (banner.type === 'success') {
            setTimeout(() => setAlert(null), 6000);
        }
    };

    const handleUpgrade = useCallback(async (card: CardData) => {
        if (isProcessing) return;
        setIsProcessing(true);
        setAlert(null);

        try {
            await mockProcessPayment(card);
            const updated = await updateUserPlan('premium');
            onPlanChange(updated);
            setView('overview');
            showAlert({
                type: 'success',
                title: 'Welcome to Premium! 🎉',
                message: 'Your subscription is active. Enjoy unlimited forecasts and all premium features.',
            });
        } catch (err: any) {
            showAlert({
                type: 'error',
                title: 'Payment failed',
                message: err.message ?? 'An unexpected error occurred. Please try again.',
            });
        } finally {
            setIsProcessing(false);
        }
    }, [isProcessing, onPlanChange]);

    const handleCancel = useCallback(async () => {
        if (isProcessing) return;
        setIsProcessing(true);
        setAlert(null);

        try {
            const updated = await updateUserPlan('free');
            onPlanChange(updated);
            setView('overview');
            showAlert({
                type: 'success',
                title: 'Subscription cancelled',
                message: 'You have been downgraded to the Free plan. Your data and forecasts are preserved.',
            });
        } catch (err: any) {
            showAlert({
                type: 'error',
                title: 'Cancellation failed',
                message: err.message ?? 'Could not cancel your subscription. Please try again.',
            });
        } finally {
            setIsProcessing(false);
        }
    }, [isProcessing, onPlanChange]);

    const handleReactivate = useCallback(async () => {
        if (isProcessing) return;
        setIsProcessing(true);
        setAlert(null);

        try {
            const updated = await updateUserPlan('premium');
            onPlanChange(updated);
            showAlert({
                type: 'success',
                title: 'Subscription reactivated',
                message: 'Your Premium access has been fully restored and will renew automatically.',
            });
        } catch (err: any) {
            showAlert({
                type: 'error',
                title: 'Reactivation failed',
                message: err.message ?? 'Please try again.',
            });
        } finally {
            setIsProcessing(false);
        }
    }, [isProcessing, onPlanChange]);

    return (
        <div className="space-y-8">
            {/* Header */}
            <div className="flex items-start justify-between">
                <div>
                    <h2 className="text-xl font-semibold text-gray-900">Subscription</h2>
                    <p className="mt-1 text-sm text-gray-500">Manage your plan and billing.</p>
                </div>
                <PlanBadge plan={currentPlan} />
            </div>

            {/* Alert banner */}
            {alert && (
                <div className={`rounded-xl border p-4 flex items-start gap-3 ${
                    alert.type === 'success'
                        ? 'bg-green-50 border-green-200'
                        : 'bg-red-50 border-red-200'
                }`}>
                    {alert.type === 'success' ? (
                        <svg className="w-5 h-5 text-green-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M9 12l2 2 4-4m6 2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                    ) : (
                        <svg className="w-5 h-5 text-red-500 shrink-0 mt-0.5" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M10 14l2-2m0 0l2-2m-2 2l-2-2m2 2l2 2m7-2a9 9 0 11-18 0 9 9 0 0118 0z" />
                        </svg>
                    )}
                    <div>
                        <p className={`text-sm font-semibold ${alert.type === 'success' ? 'text-green-800' : 'text-red-800'}`}>
                            {alert.title}
                        </p>
                        <p className={`text-sm mt-0.5 ${alert.type === 'success' ? 'text-green-700' : 'text-red-700'}`}>
                            {alert.message}
                        </p>
                    </div>
                    <button
                        onClick={() => setAlert(null)}
                        className={`ml-auto p-1 rounded transition-colors ${alert.type === 'success' ? 'text-green-400 hover:text-green-600' : 'text-red-400 hover:text-red-600'}`}
                    >
                        <svg className="w-4 h-4" fill="none" viewBox="0 0 24 24" stroke="currentColor">
                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M6 18L18 6M6 6l12 12" />
                        </svg>
                    </button>
                </div>
            )}

            {/* Plan cards — always visible */}
            {view === 'overview' && (
                <>
                    <div className="grid grid-cols-2 gap-4">
                        {/* Free plan */}
                        <div className={`rounded-xl border-2 p-5 transition-all ${!isPremium ? 'border-anizai-blue-400 bg-anizai-blue-50' : 'border-gray-200 bg-white'}`}>
                            <div className="flex items-center justify-between mb-1">
                                <h3 className="font-semibold text-gray-900">Free</h3>
                                {!isPremium && (
                                    <span className="text-xs font-semibold text-anizai-blue-600 bg-anizai-blue-100 px-2 py-0.5 rounded-full">Current</span>
                                )}
                            </div>
                            <p className="text-2xl font-bold text-gray-900 mb-4">$0<span className="text-sm font-normal text-gray-400">/mo</span></p>
                            <ul className="space-y-2">
                                {PLAN_FEATURES.free.map(f => (
                                    <li key={f} className="flex items-start gap-2 text-sm text-gray-600">
                                        <svg className="w-4 h-4 text-gray-400 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                        </svg>
                                        {f}
                                    </li>
                                ))}
                            </ul>
                        </div>

                        {/* Premium plan */}
                        <div className={`rounded-xl border-2 p-5 relative overflow-hidden transition-all ${isActive ? 'border-anizai-blue-500 bg-gradient-to-br from-anizai-blue-50 to-anizai-purple-50' : isCanceled ? 'border-amber-400 bg-amber-50' : 'border-gray-200 bg-white'}`}>
                            {isActive && (
                                <div className="absolute top-0 right-0 bg-gradient-to-l from-anizai-teal-500 to-anizai-blue-500 text-white text-xs font-bold px-3 py-1 rounded-bl-lg">
                                    Active
                                </div>
                            )}
                            {isCanceled && (
                                <div className="absolute top-0 right-0 bg-amber-500 text-white text-xs font-bold px-3 py-1 rounded-bl-lg">
                                    Canceled
                                </div>
                            )}
                            <div className="flex items-center justify-between mb-1">
                                <h3 className="font-semibold text-gray-900">Premium</h3>
                                {!isPremium && (
                                    <span className="text-xs font-semibold text-anizai-purple-600 bg-anizai-purple-100 px-2 py-0.5 rounded-full">Recommended</span>
                                )}
                            </div>
                            <div className="mb-4">
                                <p className="text-2xl font-bold text-gray-900">$19<span className="text-sm font-normal text-gray-400">/mo</span></p>
                                <p className="text-xs text-gray-500 mt-1">{isCanceled ? 'Valid until' : 'Next billing'}: {formattedExpiresAt}</p>
                            </div>
                            <ul className="space-y-2">
                                {PLAN_FEATURES.premium.map(f => (
                                    <li key={f} className="flex items-start gap-2 text-sm text-gray-600">
                                        <svg className="w-4 h-4 text-anizai-blue-500 shrink-0 mt-0.5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                            <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M5 13l4 4L19 7" />
                                        </svg>
                                        {f}
                                    </li>
                                ))}
                            </ul>
                        </div>
                    </div>

                    {/* Usage meter */}
                    {!isPremium && (
                        <div className="border border-gray-200 rounded-xl p-5 space-y-3">
                            <div className="flex items-center justify-between text-sm">
                                <span className="font-medium text-gray-700">Monthly Forecasts Used</span>
                                <span className="font-bold text-gray-900">
                                    {userProfile?.monthlyForecastsUsed ?? 0}
                                    <span className="text-gray-400 font-normal"> / 3</span>
                                </span>
                            </div>
                            <div className="w-full bg-gray-100 rounded-full h-2.5 overflow-hidden">
                                <div
                                    className="h-full rounded-full transition-all duration-700"
                                    style={{
                                        width: `${Math.min(100, ((userProfile?.monthlyForecastsUsed ?? 0) / 3) * 100)}%`,
                                        background: (userProfile?.monthlyForecastsUsed ?? 0) >= 3
                                            ? 'linear-gradient(90deg, #f97316, #ef4444)'
                                            : 'linear-gradient(90deg, #14b8a6, #3b82f6)',
                                    }}
                                />
                            </div>
                            {(userProfile?.monthlyForecastsUsed ?? 0) >= 3 && (
                                <p className="text-xs text-red-600 font-medium">
                                    You've reached your monthly limit. Upgrade to Premium for unlimited forecasts.
                                </p>
                            )}
                        </div>
                    )}

                    {/* CTA buttons */}
                    {!isPremium ? (
                        <button
                            onClick={() => { setAlert(null); setView('payment-form'); }}
                            className="w-full h-12 flex items-center justify-center gap-2 text-sm font-semibold text-white rounded-xl bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 hover:opacity-90 focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:ring-offset-2 transition-opacity shadow-md"
                        >
                            <svg className="w-4 h-4" fill="currentColor" viewBox="0 0 20 20">
                                <path d="M9.049 2.927c.3-.921 1.603-.921 1.902 0l1.07 3.292a1 1 0 00.95.69h3.462c.969 0 1.371 1.24.588 1.81l-2.8 2.034a1 1 0 00-.364 1.118l1.07 3.292c.3.921-.755 1.688-1.54 1.118l-2.8-2.034a1 1 0 00-1.175 0l-2.8 2.034c-.784.57-1.838-.197-1.539-1.118l1.07-3.292a1 1 0 00-.364-1.118L2.98 8.72c-.783-.57-.38-1.81.588-1.81h3.461a1 1 0 00.951-.69l1.07-3.292z" />
                            </svg>
                            Upgrade to Premium
                        </button>
                    ) : isCanceled ? (
                        <div className="pt-3 border-t border-gray-100">
                            <div className="bg-amber-100/50 border border-amber-200 rounded-lg p-3 text-center mb-3">
                                <p className="text-sm font-semibold text-amber-800">Subscription Canceled</p>
                                <p className="text-xs text-amber-700 mt-1">
                                    Your Premium access will remain valid until <span className="font-bold">{formattedExpiresAt}</span>.
                                </p>
                            </div>
                            <button
                                onClick={handleReactivate}
                                disabled={isProcessing}
                                className="w-full h-11 flex items-center justify-center text-sm font-semibold text-white rounded-lg bg-anizai-blue-600 hover:bg-anizai-blue-700 focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:ring-offset-2 transition-colors shadow-sm disabled:opacity-60"
                            >
                                {isProcessing ? 'Reactivating...' : 'Reactivate Subscription'}
                            </button>
                        </div>
                    ) : (
                        <div className="flex items-center justify-between pt-2 border-t border-gray-100">
                            <p className="text-sm text-gray-500">Want to downgrade?</p>
                            <button
                                onClick={() => { setAlert(null); setView('cancel-confirm'); }}
                                className="text-sm font-medium text-red-500 hover:text-red-700 underline transition-colors"
                            >
                                Cancel subscription
                            </button>
                        </div>
                    )}
                </>
            )}

            {/* Payment form */}
            {view === 'payment-form' && (
                <div className="space-y-5">
                    <div className="flex items-center gap-3">
                        <button
                            onClick={() => setView('overview')}
                            disabled={isProcessing}
                            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 disabled:opacity-50 transition-colors"
                        >
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                            </svg>
                        </button>
                        <h3 className="font-semibold text-gray-900">Payment Details</h3>
                    </div>
                    <div className="bg-gray-50 border border-gray-200 rounded-xl p-4 flex items-center justify-between">
                        <div>
                            <p className="text-sm font-semibold text-gray-900">Anizai Premium</p>
                            <p className="text-xs text-gray-500 mt-0.5">Billed monthly • Next billing: {formattedExpiresAt}</p>
                        </div>
                        <p className="text-lg font-bold text-gray-900">$19<span className="text-sm font-normal text-gray-400">/mo</span></p>
                    </div>
                    <PaymentForm
                        onSuccess={handleUpgrade}
                        onCancel={() => setView('overview')}
                        isProcessing={isProcessing}
                    />
                </div>
            )}

            {/* Cancel confirm */}
            {view === 'cancel-confirm' && (
                <div className="space-y-5">
                    <div className="flex items-center gap-3">
                        <button
                            onClick={() => setView('overview')}
                            disabled={isProcessing}
                            className="p-1.5 rounded-lg text-gray-400 hover:text-gray-600 hover:bg-gray-100 disabled:opacity-50 transition-colors"
                        >
                            <svg className="w-5 h-5" fill="none" stroke="currentColor" viewBox="0 0 24 24">
                                <path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M15 19l-7-7 7-7" />
                            </svg>
                        </button>
                        <h3 className="font-semibold text-gray-900">Cancel Subscription</h3>
                    </div>
                    <CancelConfirm
                        onConfirm={handleCancel}
                        onDismiss={() => setView('overview')}
                        isProcessing={isProcessing}
                    />
                </div>
            )}
        </div>
    );
}
