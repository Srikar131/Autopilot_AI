import { useEffect, useState } from 'react';
import { Clock, Moon, RefreshCw, Sun, Monitor } from 'lucide-react';
import type { ColorSchemePreference } from '../types';

interface TopStatusBarProps {
  title: string;
  circadianOpen: boolean;
  trippedCount: number;
  isRefreshing: boolean;
  onRefresh: () => void;
  colorScheme: ColorSchemePreference;
  onChangeColorScheme: (scheme: ColorSchemePreference) => void;
}

export function TopStatusBar({
  title,
  circadianOpen,
  trippedCount,
  isRefreshing,
  onRefresh,
  colorScheme,
  onChangeColorScheme,
}: TopStatusBarProps) {
  const [istClock, setIstClock] = useState(() =>
    new Intl.DateTimeFormat('en-IN', {
      timeZone: 'Asia/Kolkata',
      hour: '2-digit',
      minute: '2-digit',
      second: '2-digit',
      hour12: true,
    }).format(new Date()),
  );

  useEffect(() => {
    const timer = setInterval(() => {
      setIstClock(
        new Intl.DateTimeFormat('en-IN', {
          timeZone: 'Asia/Kolkata',
          hour: '2-digit',
          minute: '2-digit',
          second: '2-digit',
          hour12: true,
        }).format(new Date()),
      );
    }, 1000);
    return () => clearInterval(timer);
  }, []);

  return (
    <header className="card-surface flex flex-wrap items-center justify-between gap-4 border-b px-6 py-4">
      <div>
        <h1 className="text-xl font-bold tracking-tight">{title}</h1>
      </div>

      <div className="flex flex-wrap items-center gap-2.5 text-xs font-medium">
        <span className="muted-surface inline-flex items-center gap-1.5 rounded-full px-3 py-1.5">
          <Clock className="h-3.5 w-3.5 text-blue-500" aria-hidden="true" />
          <span>{istClock} IST</span>
        </span>

        <span
          className={`inline-flex items-center gap-1.5 rounded-full px-3 py-1.5 ${
            circadianOpen
              ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
              : 'bg-amber-500/15 text-amber-800 dark:text-amber-300'
          }`}
        >
          <span
            className={`h-2 w-2 rounded-full ${circadianOpen ? 'bg-emerald-500' : 'bg-amber-500'}`}
            aria-hidden="true"
          />
          {circadianOpen ? 'Circadian Window Open (08:30–21:30)' : 'Circadian Sleep Window'}
        </span>

        {trippedCount > 0 && (
          <span className="rounded-full bg-red-600/15 px-3 py-1.5 text-red-600 dark:text-red-400">
            {trippedCount} Breaker Tripped
          </span>
        )}

        <div
          role="group"
          aria-label="Theme preference"
          className="muted-surface inline-flex items-center rounded-full p-0.5"
        >
          {(
            [
              { value: 'system', label: 'System', icon: Monitor },
              { value: 'light', label: 'Light', icon: Sun },
              { value: 'dark', label: 'Dark', icon: Moon },
            ] as const
          ).map(({ value, label, icon: Icon }) => (
            <button
              key={value}
              type="button"
              onClick={() => onChangeColorScheme(value)}
              aria-pressed={colorScheme === value}
              title={`Theme: ${label}`}
              className={`inline-flex items-center gap-1 rounded-full px-2.5 py-1 text-xs transition-colors ${
                colorScheme === value ? 'bg-blue-600 text-white' : 'text-muted hover:text-[var(--text-primary)]'
              }`}
            >
              <Icon className="h-3.5 w-3.5" aria-hidden="true" />
              <span className="hidden sm:inline">{label}</span>
            </button>
          ))}
        </div>

        <button
          type="button"
          onClick={onRefresh}
          disabled={isRefreshing}
          className="muted-surface inline-flex min-h-[36px] items-center gap-1.5 rounded-full px-3 py-1.5 hover:opacity-90 disabled:opacity-60"
        >
          <RefreshCw className={`h-3.5 w-3.5 ${isRefreshing ? 'animate-spin' : ''}`} aria-hidden="true" />
          <span>Sync</span>
        </button>
      </div>
    </header>
  );
}
