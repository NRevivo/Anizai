import { useState } from 'react';
import { ChevronDown } from 'lucide-react';
import type { ReasoningStep } from '../../../types';

interface ReasoningChainProps {
    steps: ReasoningStep[];
}

export function ReasoningChain({ steps }: ReasoningChainProps) {
    const [open, setOpen] = useState(false);

    if (steps.length === 0) {
        return null;
    }

    const orderedSteps = steps.slice().sort((a, b) => a.step - b.step);

    return (
        <div className="rounded-xl bg-white ring-1 ring-slate-900/[0.04]">
            <button
                type="button"
                onClick={() => setOpen((current) => !current)}
                aria-expanded={open}
                className="flex w-full items-center justify-between gap-2 rounded-xl px-4 py-3 text-left transition-colors hover:bg-slate-50"
            >
                <span className="text-sm font-medium text-slate-600">
                    Reasoning chain
                    <span className="ml-2 text-xs font-normal text-slate-400">
                        {orderedSteps.length} steps
                    </span>
                </span>
                <ChevronDown
                    className={`h-4 w-4 shrink-0 text-slate-400 transition-transform ${open ? 'rotate-180' : ''}`}
                    aria-hidden
                />
            </button>

            {open ? (
                <ol className="space-y-4 px-4 pb-4 pt-1">
                    {orderedSteps.map((step) => (
                        <li key={`${step.step}-${step.title}`} className="flex gap-3">
                            <span className="w-5 shrink-0 text-lg font-light leading-snug text-anizai-purple-500">
                                {step.step}
                            </span>
                            <div className="min-w-0">
                                <p className="text-sm font-semibold text-gray-900">{step.title}</p>
                                <p className="mt-0.5 break-words text-sm leading-relaxed text-slate-500">
                                    {step.description}
                                </p>
                            </div>
                        </li>
                    ))}
                </ol>
            ) : null}
        </div>
    );
}
