import { useState, useEffect } from 'react';

const STORAGE_KEY = 'anizai_preferences';

interface UserPreferences {
    theme: 'light' | 'dark';
    compactMode: boolean;
    showExplanations: boolean;
}

function loadPreferences(): UserPreferences {
    try {
        const raw = localStorage.getItem(STORAGE_KEY);
        if (raw) return JSON.parse(raw) as UserPreferences;
    } catch {
        // ignore
    }
    return { theme: 'light', compactMode: false, showExplanations: true };
}

function savePreferences(prefs: UserPreferences) {
    localStorage.setItem(STORAGE_KEY, JSON.stringify(prefs));
}

export function PreferenceSettings() {
    const [prefs, setPrefs] = useState<UserPreferences>(loadPreferences);
    const [saved, setSaved] = useState(false);

    useEffect(() => {
        savePreferences(prefs);
        setSaved(true);
        const t = setTimeout(() => setSaved(false), 1800);
        return () => clearTimeout(t);
    }, [prefs]);

    const toggle = (key: keyof Omit<UserPreferences, 'theme'>) => {
        setPrefs(p => ({ ...p, [key]: !p[key] }));
    };

    const setTheme = (theme: UserPreferences['theme']) => {
        setPrefs(p => ({ ...p, theme }));
    };

    return (
        <div className="space-y-8">
            <div className="flex items-start justify-between">
                <div>
                    <h2 className="text-xl font-semibold text-gray-900">Preferences</h2>
                    <p className="mt-1 text-sm text-gray-500">Customize your Anizai experience.</p>
                </div>
                {saved && (
                    <span className="inline-flex items-center gap-1 text-xs text-green-600 bg-green-50 border border-green-200 rounded-full px-2.5 py-1 animate-fade-in">
                        <svg className="w-3 h-3" fill="currentColor" viewBox="0 0 20 20">
                            <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
                        </svg>
                        Saved
                    </span>
                )}
            </div>

            {/* Theme */}
            <section className="space-y-3">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Appearance</h3>
                <div className="grid grid-cols-2 gap-3">
                    {(['light', 'dark'] as const).map(t => (
                        <button
                            key={t}
                            onClick={() => setTheme(t)}
                            className={`relative flex flex-col items-center gap-2 p-4 rounded-xl border-2 transition-all ${prefs.theme === t ? 'border-anizai-blue-500 bg-anizai-blue-50' : 'border-gray-200 bg-white hover:border-gray-300'}`}
                        >
                            <div className={`w-10 h-7 rounded-md border ${t === 'dark' ? 'bg-gray-800 border-gray-700' : 'bg-white border-gray-200'}`} />
                            <span className="text-sm font-medium capitalize text-gray-700">{t}</span>
                            {prefs.theme === t && (
                                <span className="absolute top-2 right-2 w-4 h-4 bg-anizai-blue-500 rounded-full flex items-center justify-center">
                                    <svg className="w-2.5 h-2.5 text-white" fill="currentColor" viewBox="0 0 20 20">
                                        <path fillRule="evenodd" d="M16.704 4.153a.75.75 0 01.143 1.052l-8 10.5a.75.75 0 01-1.127.075l-4.5-4.5a.75.75 0 011.06-1.06l3.894 3.893 7.48-9.817a.75.75 0 011.05-.143z" clipRule="evenodd" />
                                    </svg>
                                </span>
                            )}
                        </button>
                    ))}
                </div>
                <p className="text-xs text-gray-400">Dark mode appearance is saved but not yet applied to the UI.</p>
            </section>

            {/* Toggles */}
            <section className="space-y-3">
                <h3 className="text-xs font-semibold text-gray-400 uppercase tracking-wider">Dashboard</h3>
                <div className="border border-gray-200 rounded-xl divide-y divide-gray-100 overflow-hidden">
                    <ToggleRow
                        label="Compact dashboard view"
                        description="Reduce padding and card sizes for a denser layout."
                        checked={prefs.compactMode}
                        onChange={() => toggle('compactMode')}
                    />
                    <ToggleRow
                        label="Show forecasting explanations"
                        description="Display detailed AI reasoning alongside each probability."
                        checked={prefs.showExplanations}
                        onChange={() => toggle('showExplanations')}
                    />
                </div>
            </section>
        </div>
    );
}

interface ToggleRowProps {
    label: string;
    description: string;
    checked: boolean;
    onChange: () => void;
}

function ToggleRow({ label, description, checked, onChange }: ToggleRowProps) {
    return (
        <div className="flex items-center justify-between px-5 py-4 gap-6">
            <div className="min-w-0">
                <p className="text-sm font-medium text-gray-900">{label}</p>
                <p className="text-xs text-gray-500 mt-0.5">{description}</p>
            </div>
            <button
                type="button"
                role="switch"
                aria-checked={checked}
                onClick={onChange}
                className={`relative flex-shrink-0 inline-flex h-6 w-11 items-center rounded-full transition-colors focus:outline-none focus:ring-2 focus:ring-anizai-blue-500 focus:ring-offset-2 ${checked ? 'bg-anizai-blue-500' : 'bg-gray-200'}`}
            >
                <span
                    className={`inline-block h-4 w-4 transform rounded-full bg-white shadow-sm transition-transform duration-200 ${checked ? 'translate-x-6' : 'translate-x-1'}`}
                />
            </button>
        </div>
    );
}
