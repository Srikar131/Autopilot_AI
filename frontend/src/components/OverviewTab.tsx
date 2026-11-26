import { useState } from 'react';
import {
  AlertTriangle,
  Bot,
  CalendarClock,
  CheckCircle2,
  Play,
  Rocket,
  Sparkles,
} from 'lucide-react';
import { api } from '../api/client';
import type { DashboardOverview, NavTab, SystemReadiness } from '../types';

interface OverviewTabProps {
  overview: DashboardOverview | null;
  readiness: SystemReadiness | null;
  onRefresh: () => Promise<void>;
  onNavigate: (tab: NavTab) => void;
  onToast: (msg: string, isError?: boolean) => void;
}

export function OverviewTab({
  overview,
  readiness,
  onRefresh,
  onNavigate,
  onToast,
}: OverviewTabProps) {
  const [busyKey, setBusyKey] = useState<string | null>(null);

  const handleSeedSamples = async () => {
    setBusyKey('seed');
    try {
      const res = await api.seedSampleJobs();
      onToast(`Seeded ${res.discovered_count} jobs (${res.ready_to_apply_count} ready, ${res.rejected_count} rejected).`);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to seed sample jobs', true);
    } finally {
      setBusyKey(null);
    }
  };

  const handleTriggerScheduler = async (jobId: string) => {
    setBusyKey(jobId);
    try {
      await api.triggerSchedulerJob(jobId);
      onToast(`Executed scheduler job: ${jobId}`);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : `Failed to run ${jobId}`, true);
    } finally {
      setBusyKey(null);
    }
  };

  const statusCounts = overview?.status_counts ?? {};
  const readyCount = statusCounts.READY_TO_APPLY ?? 0;
  const pausedCount = statusCounts.PAUSED_HITL ?? overview?.paused_hitl_count ?? 0;
  const appliedCount = statusCounts.APPLIED ?? 0;
  const rejectedCount = statusCounts.SCORED_REJECTED ?? 0;

  const blockers: string[] = [];
  if (readiness?.profile_vault.is_placeholder_contact) {
    blockers.push('Candidate Profile Vault still uses sample placeholder contact details');
  }
  if (readiness && !readiness.telegram.allowed_chat_id_configured) {
    blockers.push('TELEGRAM_ALLOWED_CHAT_ID is unset (Telegram cards are in dry-run mode)');
  }
  if (readiness && readiness.supabase.url_configured && !readiness.supabase.schema_ready) {
    blockers.push('Supabase schema 001_init_schema.sql has not been applied yet (using local fallback)');
  }

  return (
    <div className="space-y-6">
      {blockers.length > 0 && (
        <section
          aria-label="Actionable setup checks"
          className="card-surface rounded-2xl border-amber-500/50 bg-amber-500/10 p-5"
        >
          <div className="flex flex-wrap items-start justify-between gap-4">
            <div className="flex items-start gap-3">
              <AlertTriangle className="mt-0.5 h-5 w-5 shrink-0 text-amber-500" aria-hidden="true" />
              <div>
                <h2 className="text-sm font-semibold">Pending Live Setup Decisions</h2>
                <ul className="mt-1.5 list-disc space-y-1 pl-4 text-xs text-[var(--text-secondary)]">
                  {blockers.map((b) => (
                    <li key={b}>{b}</li>
                  ))}
                </ul>
              </div>
            </div>
            <div className="flex flex-wrap gap-2">
              <button
                type="button"
                onClick={() => onNavigate('profile')}
                className="rounded-xl bg-amber-500 px-3.5 py-2 text-xs font-semibold text-slate-950 hover:opacity-90"
              >
                Update Profile Vault
              </button>
              <button
                type="button"
                onClick={() => onNavigate('safety')}
                className="muted-surface rounded-xl px-3.5 py-2 text-xs font-semibold hover:opacity-90"
              >
                Inspect Readiness
              </button>
            </div>
          </div>
        </section>
      )}

      <section aria-label="Pipeline KPI summary" className="grid grid-cols-1 gap-4 sm:grid-cols-2 xl:grid-cols-5">
        <div className="card-surface rounded-2xl p-5">
          <p className="text-muted text-xs font-medium uppercase tracking-wider">Total Discovered</p>
          <p className="mt-2 text-3xl font-bold">{overview?.total_jobs ?? 0}</p>
          <p className="text-muted mt-1 text-xs">Avg JD match: {overview?.average_match_score ?? 0}%</p>
        </div>

        <div className="card-surface rounded-2xl p-5">
          <p className="text-muted text-xs font-medium uppercase tracking-wider">Ready To Apply</p>
          <p className="mt-2 text-3xl font-bold text-blue-600 dark:text-sky-400">{readyCount}</p>
          <p className="text-muted mt-1 text-xs">Tailored resumes compiled</p>
        </div>

        <div className="card-surface rounded-2xl p-5">
          <p className="text-muted text-xs font-medium uppercase tracking-wider">Paused HITL</p>
          <p className="mt-2 text-3xl font-bold text-amber-600 dark:text-amber-400">{pausedCount}</p>
          <p className="text-muted mt-1 text-xs">Awaiting human review</p>
        </div>

        <div className="card-surface rounded-2xl p-5">
          <p className="text-muted text-xs font-medium uppercase tracking-wider">Applied</p>
          <p className="mt-2 text-3xl font-bold text-emerald-600 dark:text-emerald-400">{appliedCount}</p>
          <p className="text-muted mt-1 text-xs">Autonomous + HITL approved</p>
        </div>

        <div className="card-surface rounded-2xl p-5">
          <p className="text-muted text-xs font-medium uppercase tracking-wider">Filtered Out</p>
          <p className="mt-2 text-3xl font-bold">{rejectedCount}</p>
          <p className="text-muted mt-1 text-xs">Score &lt; 80 or hard blocker</p>
        </div>
      </section>

      {(overview?.total_jobs ?? 0) === 0 && (
        <section className="card-surface flex flex-wrap items-center justify-between gap-4 rounded-2xl p-6">
          <div>
            <h2 className="text-base font-semibold">Queue is currently empty</h2>
            <p className="text-muted mt-1 text-sm">
              Seed 5 realistic sample jobs across Greenhouse, Lever, Ashby, LinkedIn &amp; Naukri to test JD scoring,
              tailored PDF compilation, Dream Company HITL pauses, and <code>qa_cache</code> learning immediately.
            </p>
          </div>
          <button
            type="button"
            disabled={busyKey === 'seed'}
            onClick={handleSeedSamples}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-xl bg-blue-600 px-4 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
          >
            <Sparkles className="h-4 w-4" aria-hidden="true" />
            {busyKey === 'seed' ? 'Seeding Pipeline...' : 'Seed Demo Pipeline'}
          </button>
        </section>
      )}

      <div className="grid grid-cols-1 gap-6 xl:grid-cols-2">
        <section className="card-surface rounded-2xl p-6">
          <div className="mb-4 flex items-center justify-between gap-2">
            <div>
              <h2 className="text-base font-semibold">Daily Anti-Ban Quota Ledger</h2>
              <p className="text-muted text-xs">Strict SEC-001 caps per isolated portal profile</p>
            </div>
            {(overview?.total_jobs ?? 0) > 0 && (
              <button
                type="button"
                disabled={busyKey === 'seed'}
                onClick={handleSeedSamples}
                className="muted-surface inline-flex items-center gap-1.5 rounded-xl px-3 py-1.5 text-xs font-medium hover:opacity-90"
              >
                <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                Add Sample Jobs
              </button>
            )}
          </div>

          <div className="space-y-4">
            {(overview?.daily_quotas ?? []).map((q) => {
              const pct = q.max_daily_limit > 0 ? Math.min(100, Math.round((q.applied_count / q.max_daily_limit) * 100)) : 0;
              return (
                <div key={q.portal} className="space-y-1.5">
                  <div className="flex items-center justify-between text-xs font-medium">
                    <span className="uppercase tracking-wide">{q.portal.replace('_', ' ')}</span>
                    <span>
                      {q.applied_count} / {q.max_daily_limit} ({q.remaining} left)
                    </span>
                  </div>
                  <div className="h-2.5 w-full overflow-hidden rounded-full bg-[var(--surface-muted)]">
                    <div
                      className={`h-full rounded-full transition-all ${
                        q.circuit_breaker_tripped
                          ? 'bg-red-500'
                          : pct > 80
                            ? 'bg-amber-500'
                            : 'bg-blue-600 dark:bg-sky-400'
                      }`}
                      style={{ width: `${pct}%` }}
                    />
                  </div>
                </div>
              );
            })}
          </div>
        </section>

        <section className="card-surface rounded-2xl p-6">
          <div className="mb-4 flex items-center justify-between">
            <div>
              <h2 className="text-base font-semibold">IST Scheduler &amp; Wave Automation</h2>
              <p className="text-muted text-xs">APScheduler cron &amp; interval jobs (Asia/Kolkata)</p>
            </div>
            <CalendarClock className="text-muted h-5 w-5" aria-hidden="true" />
          </div>

          <div className="space-y-3">
            {(overview?.scheduler_jobs ?? []).map((sj) => (
              <div
                key={sj.id}
                className="muted-surface flex flex-wrap items-center justify-between gap-3 rounded-xl p-3.5"
              >
                <div>
                  <p className="text-sm font-semibold">{sj.id}</p>
                  <p className="text-muted text-xs">
                    Next run: {sj.next_run_time ? new Date(sj.next_run_time).toLocaleString() : 'Paused / On-demand'}
                  </p>
                </div>
                <button
                  type="button"
                  disabled={busyKey === sj.id}
                  onClick={() => handleTriggerScheduler(sj.id)}
                  className="inline-flex min-h-[38px] items-center gap-1.5 rounded-lg bg-blue-600 px-3 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
                >
                  <Play className="h-3.5 w-3.5" aria-hidden="true" />
                  {busyKey === sj.id ? 'Running...' : 'Run Now'}
                </button>
              </div>
            ))}
          </div>
        </section>
      </div>

      <section className="card-surface rounded-2xl p-6">
        <div className="mb-4 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <Bot className="h-5 w-5 text-blue-500" aria-hidden="true" />
            <h2 className="text-base font-semibold">Recent Agent &amp; HITL Events</h2>
          </div>
          <button
            type="button"
            onClick={() => onNavigate('jobs')}
            className="text-xs font-semibold text-blue-600 hover:underline dark:text-sky-400"
          >
            Open Jobs Queue →
          </button>
        </div>

        {(overview?.recent_events ?? []).length === 0 ? (
          <p className="text-muted text-sm">
            No execution events yet. Apply to a job from the Jobs &amp; Resumes tab or trigger a wave above.
          </p>
        ) : (
          <div className="space-y-2">
            {overview?.recent_events.slice(0, 8).map((ev) => (
              <div
                key={ev.id}
                className="muted-surface flex flex-wrap items-center justify-between gap-2 rounded-xl px-4 py-2.5 text-xs"
              >
                <div className="flex items-center gap-2.5">
                  {ev.event_type === 'APPLIED' ? (
                    <CheckCircle2 className="h-4 w-4 text-emerald-500" aria-hidden="true" />
                  ) : (
                    <Rocket className="h-4 w-4 text-amber-500" aria-hidden="true" />
                  )}
                  <span className="font-semibold">{ev.event_type}</span>
                  <span className="text-muted">Thread: {ev.langgraph_thread_id}</span>
                </div>
                <span className="text-muted">{new Date(ev.created_at).toLocaleString()}</span>
              </div>
            ))}
          </div>
        )}
      </section>
    </div>
  );
}
