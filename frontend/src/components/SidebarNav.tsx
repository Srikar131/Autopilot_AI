import {
  Activity,
  CheckCircle2,
  ListFilter,
  ShieldAlert,
  ShieldCheck,
  UserCheck,
} from 'lucide-react';
import type { NavTab } from '../types';

interface SidebarNavProps {
  activeTab: NavTab;
  onSelectTab: (tab: NavTab) => void;
  readyCount: number;
  manualCount: number;
  appliedTodayCount: number;
  trippedCount: number;
}

const NAV_ITEMS: Array<{ id: NavTab; label: string; step: string; icon: typeof UserCheck }> = [
  { id: 'profile', label: 'Profile & Start Search', step: '1', icon: UserCheck },
  { id: 'jobs', label: 'Discover & AI Verify', step: '2', icon: ListFilter },
  { id: 'hitl', label: 'Needs Manual Apply', step: '3', icon: ShieldAlert },
  { id: 'applied', label: 'Applied Today', step: '4', icon: CheckCircle2 },
  { id: 'safety', label: 'Accounts, Q&A & Anti-Ban', step: '5', icon: ShieldCheck },
];

export function SidebarNav({
  activeTab,
  onSelectTab,
  readyCount,
  manualCount,
  appliedTodayCount,
  trippedCount,
}: SidebarNavProps) {
  return (
    <aside className="card-surface flex w-full shrink-0 flex-col justify-between border-b p-4 lg:w-72 lg:border-b-0 lg:border-r">
      <div>
        <div className="mb-6 flex items-center gap-3 px-2">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-600 text-white shadow-sm">
            <Activity className="h-5 w-5" aria-hidden="true" />
          </div>
          <div>
            <p className="text-sm font-bold tracking-tight">AutoApply-AI</p>
            <p className="text-muted text-xs">Autonomous Multi-Agent Pipeline</p>
          </div>
        </div>

        <nav aria-label="Primary workflow steps" className="grid grid-cols-1 gap-1.5 sm:grid-cols-2 lg:grid-cols-1">
          {NAV_ITEMS.map(({ id, label, step, icon: Icon }) => {
            const isActive = activeTab === id;
            const badge =
              id === 'jobs'
                ? readyCount
                : id === 'hitl'
                  ? manualCount
                  : id === 'applied'
                    ? appliedTodayCount
                    : id === 'safety'
                      ? trippedCount
                      : 0;

            return (
              <button
                key={id}
                type="button"
                onClick={() => onSelectTab(id)}
                aria-current={isActive ? 'page' : undefined}
                className={`flex min-h-[46px] items-center justify-between gap-2 rounded-xl px-3 py-2 text-left text-sm font-medium transition-colors ${
                  isActive ? 'bg-blue-600 text-white shadow-sm' : 'hover:bg-[var(--surface-muted)]'
                }`}
              >
                <span className="flex items-center gap-2.5">
                  <span
                    className={`flex h-5 w-5 shrink-0 items-center justify-center rounded-full text-[11px] font-bold ${
                      isActive ? 'bg-white/20 text-white' : 'bg-[var(--surface-muted)] text-[var(--text-secondary)]'
                    }`}
                  >
                    {step}
                  </span>
                  <Icon className="h-4 w-4 shrink-0" aria-hidden="true" />
                  <span className="truncate">{label}</span>
                </span>

                {badge > 0 && (
                  <span
                    className={`rounded-full px-2 py-0.5 text-xs font-bold ${
                      isActive
                        ? 'bg-white text-blue-700'
                        : id === 'hitl'
                          ? 'bg-amber-500 text-slate-950'
                          : id === 'applied'
                            ? 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300'
                            : id === 'safety'
                              ? 'bg-red-600 text-white'
                              : 'bg-blue-500/15 text-blue-700 dark:text-sky-300'
                    }`}
                  >
                    {badge}
                  </span>
                )}
              </button>
            );
          })}
        </nav>
      </div>

      <div className="muted-surface mt-6 hidden space-y-2 rounded-xl p-3.5 text-xs lg:block">
        <p className="font-semibold">Dual-Plane Anti-Ban Shield</p>
        <p className="text-muted leading-relaxed">
          <strong>Discovery:</strong> Disposable guest Chromium contexts wipe cookies after every query.
        </p>
        <p className="text-muted leading-relaxed">
          <strong>Applying:</strong> Persistent home-ISP profile opens only your Round-2 verified shortlist.
        </p>
      </div>
    </aside>
  );
}
