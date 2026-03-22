import { ArrowLeft } from 'lucide-react';
import { Button } from './ui/button';

interface SiteHeaderProps {
  onBack?: () => void;
  onGetStarted?: () => void;
  backLabel?: string;
}

export function SiteHeader({ onBack, onGetStarted, backLabel = 'Back' }: SiteHeaderProps) {
  return (
    <div className="sticky top-0 z-30">
      <div className="h-[2px] w-full bg-gradient-to-r from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500" />
      <div className="border-b border-gray-200/80 bg-white/80 backdrop-blur">
        <div className="max-w-6xl mx-auto px-6 py-3.5 flex items-center justify-between gap-4">
          <div className="flex items-center gap-4">
            {onBack ? (
              <button
                onClick={onBack}
                className="group inline-flex items-center gap-2 rounded-full border border-gray-200/80 bg-white/70 px-3 py-1.5 text-sm text-gray-600 shadow-sm transition-all hover:border-gray-300 hover:text-gray-900 hover:shadow"
              >
                <span className="flex h-6 w-6 items-center justify-center rounded-full bg-gray-100 text-gray-600 transition-colors group-hover:bg-gray-200 group-hover:text-gray-900">
                  <ArrowLeft className="h-4 w-4" />
                </span>
                <span className="font-medium">{backLabel}</span>
              </button>
            ) : null}

            <div className="flex items-center gap-2">
              <span className="relative flex h-9 w-9 items-center justify-center rounded-xl bg-gradient-to-br from-anizai-teal-500 via-anizai-blue-500 to-anizai-purple-500 p-[1px] shadow-sm">
                <span className="flex h-full w-full items-center justify-center rounded-[10px] bg-white">
                  <img src="/logo-brain.png" alt="Anizai" className="h-5 w-5" />
                </span>
              </span>
              <span className="text-lg font-semibold tracking-tight text-gray-900">Anizai</span>
            </div>
          </div>

          {onGetStarted ? (
            <Button variant="primary" size="sm" onClick={onGetStarted} className="px-4">
              Get started
            </Button>
          ) : (
            <div className="hidden sm:block" aria-hidden="true" />
          )}
        </div>
      </div>
    </div>
  );
}
