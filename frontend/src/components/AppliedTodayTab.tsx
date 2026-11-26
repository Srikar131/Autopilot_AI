import { useState } from 'react';
import { CalendarCheck, ExternalLink, FileText } from 'lucide-react';
import type { AppliedTodayResponse, DashboardOverview } from '../types';

interface AppliedTodayTabProps {
  appliedData: AppliedTodayResponse | null;
  overview: DashboardOverview | null;
}

export function AppliedTodayTab({ appliedData, overview }: AppliedTodayTabProps) {
  const [showAllTime, setShowAllTime] = useState(false);

  const todayJobs = appliedData?.today_jobs ?? [];
  const allApplied = appliedData?.all_applied_jobs ?? [];
  const displayed = showAllTime ? allApplied : todayJobs;

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Applied Today ({appliedData?.date_ist || 'IST'})</h2>
          <p className="text-muted text-xs">
            Every application submitted autonomously or marked applied manually is tracked here with today&rsquo;s safety quotas.
          </p>
        </div>

        <div className="flex gap-2">
          <button
            type="button"
            onClick={() => setShowAllTime(false)}
            className={`rounded-xl px-3.5 py-2 text-xs font-semibold ${
              !showAllTime ? 'bg-blue-600 text-white' : 'muted-surface'
            }`}
          >
            Today ({appliedData?.today_count ?? 0})
          </button>
          <button
            type="button"
            onClick={() => setShowAllTime(true)}
            className={`rounded-xl px-3.5 py-2 text-xs font-semibold ${
              showAllTime ? 'bg-blue-600 text-white' : 'muted-surface'
            }`}
          >
            All Applied ({appliedData?.total_applied_count ?? 0})
          </button>
        </div>
      </div>

      {/* Daily Anti-Ban Quota Gauges */}
      <section className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
        {(overview?.daily_quotas ?? []).map((q) => {
          const pct = q.max_daily_limit > 0 ? Math.min(100, Math.round((q.applied_count / q.max_daily_limit) * 100)) : 0;
          return (
            <div key={q.portal} className="card-surface space-y-2 rounded-2xl p-4">
              <div className="flex items-center justify-between text-xs font-semibold">
                <span className="uppercase">{q.portal.replace('_', ' ')}</span>
                <span>
                  {q.applied_count} / {q.max_daily_limit}
                </span>
              </div>
              <div className="h-2 w-full overflow-hidden rounded-full bg-[var(--surface-muted)]">
                <div
                  className="h-full rounded-full bg-emerald-500 transition-all"
                  style={{ width: `${pct}%` }}
                />
              </div>
            </div>
          );
        })}
      </section>

      {displayed.length === 0 ? (
        <div className="card-surface rounded-2xl p-8 text-center">
          <CalendarCheck className="text-muted mx-auto h-8 w-8" aria-hidden="true" />
          <p className="mt-3 text-sm font-semibold">
            {showAllTime ? 'No completed applications yet' : 'No applications submitted today yet'}
          </p>
          <p className="text-muted mt-1 text-xs">
            Verify jobs in Tab 2 (Discover &amp; AI Verify) and click &ldquo;Apply to Verified&rdquo; to submit.
          </p>
        </div>
      ) : (
        <div tabIndex={0} role="region" aria-labelledby="applied-table-caption" className="table-wrapper">
          <table className="data-table">
            <caption id="applied-table-caption" className="visually-hidden">
              Submitted job applications
            </caption>
            <thead>
              <tr>
                <th>Company</th>
                <th>Role &amp; Location</th>
                <th>Portal &amp; Job ID</th>
                <th>Applied At</th>
                <th>Resume / Link</th>
              </tr>
            </thead>
            <tbody>
              {displayed.map((job) => (
                <tr key={job.id}>
                  <th>{job.company_name}</th>
                  <td>
                    <div className="font-medium">{job.role_title}</div>
                    <div className="text-muted text-xs">{job.location}</div>
                  </td>
                  <td>
                    <span className="muted-surface rounded-md px-2 py-0.5 text-xs font-semibold uppercase">
                      {job.portal}
                    </span>
                    {job.external_job_id && (
                      <div className="text-muted mt-1 text-[11px]">ID: {job.external_job_id}</div>
                    )}
                  </td>
                  <td className="text-xs">
                    {job.applied_at ? new Date(job.applied_at).toLocaleString() : 'Recorded'}
                  </td>
                  <td>
                    <div className="flex items-center gap-2">
                      {job.tailored_pdf_path && (
                        <a
                          href={`/api/v1/jobs/${job.id}/resume/pdf`}
                          target="_blank"
                          rel="noreferrer"
                          className="muted-surface inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium"
                        >
                          <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                          PDF
                        </a>
                      )}
                      <a
                        href={job.canonical_url}
                        target="_blank"
                        rel="noreferrer"
                        aria-label={`Open ${job.company_name} posting`}
                        className="muted-surface inline-flex items-center rounded-lg p-1.5"
                      >
                        <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                      </a>
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
