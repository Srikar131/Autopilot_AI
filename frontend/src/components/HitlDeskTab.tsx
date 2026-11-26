import { useState } from 'react';
import {
  AlertTriangle,
  Check,
  CheckCircle2,
  ExternalLink,
  FileText,
  SkipForward,
  Star,
} from 'lucide-react';
import { api } from '../api/client';
import type { ManualQueueJob } from '../types';

interface HitlDeskTabProps {
  manualJobs: ManualQueueJob[];
  onRefresh: () => Promise<void>;
  onToast: (msg: string, isError?: boolean) => void;
}

export function HitlDeskTab({ manualJobs, onRefresh, onToast }: HitlDeskTabProps) {
  const [overridesMap, setOverridesMap] = useState<Record<string, Record<string, string>>>({});
  const [busyJobId, setBusyJobId] = useState<string | null>(null);
  const [liveBrowserSubmit, setLiveBrowserSubmit] = useState(false);

  const getFieldValue = (job: ManualQueueJob, fieldId: string, fallback: string) => {
    return overridesMap[job.thread_id]?.[fieldId] ?? fallback;
  };

  const setFieldValue = (threadId: string, fieldId: string, value: string) => {
    setOverridesMap((prev) => ({
      ...prev,
      [threadId]: {
        ...(prev[threadId] || {}),
        [fieldId]: value,
      },
    }));
  };

  const handleResume = async (job: ManualQueueJob, action: 'APPROVE_OR_EDIT' | 'SKIP') => {
    setBusyJobId(job.id);
    try {
      const edits = overridesMap[job.thread_id] || {};
      const res = await api.resumeHitlThread({
        langgraph_thread_id: job.thread_id,
        action,
        field_overrides: action === 'SKIP' ? undefined : edits,
        save_to_qa_cache: action !== 'SKIP',
        live_browser_submit: action === 'SKIP' ? false : liveBrowserSubmit,
      });
      onToast(
        `${job.company_name} -> ${res.final_status} (${res.qa_cache_entries_added} Q&A answer saved to memory)`,
      );
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to resume thread', true);
    } finally {
      setBusyJobId(null);
    }
  };

  const handleMarkAppliedManually = async (job: ManualQueueJob) => {
    setBusyJobId(`manual-${job.id}`);
    try {
      await api.markJobAppliedManually(job.id);
      onToast(`${job.company_name} marked Applied Manually and moved to Applied Today!`);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to mark applied', true);
    } finally {
      setBusyJobId(null);
    }
  };

  return (
    <div className="space-y-6">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <h2 className="text-lg font-semibold">Needs Manual Apply &amp; Trouble Queue</h2>
          <p className="text-muted text-xs">
            Any job that hit an OTP, login wall, Dream Company guard, or low-confidence question lands here with your
            AI-drafted answers and tailored PDF so you can finish it quickly.
          </p>
        </div>
        <label className="muted-surface flex min-h-[40px] cursor-pointer items-center gap-2 rounded-xl px-3.5 text-xs font-medium">
          <input
            type="checkbox"
            checked={liveBrowserSubmit}
            onChange={(e) => setLiveBrowserSubmit(e.target.checked)}
          />
          Auto-Submit in Stealth Browser on Approve
        </label>
      </div>

      {manualJobs.length === 0 ? (
        <div className="card-surface rounded-2xl p-8 text-center">
          <CheckCircle2 className="mx-auto h-8 w-8 text-emerald-500" aria-hidden="true" />
          <p className="mt-3 text-sm font-semibold">Zero Blocked Applications</p>
          <p className="text-muted mt-1 text-xs">
            When an autonomous batch encounters a Dream Company, CAPTCHA, or complex question, it appears here for
            1-click approval or manual completion.
          </p>
        </div>
      ) : (
        <div className="space-y-5">
          {manualJobs.map((job) => (
            <article key={job.id} className="card-surface rounded-2xl p-6">
              <div className="flex flex-wrap items-start justify-between gap-4 border-b border-[var(--border-subtle)] pb-4">
                <div className="space-y-1.5">
                  <div className="flex flex-wrap items-center gap-2">
                    <h3 className="text-base font-bold">
                      {job.company_name} — {job.role_title}
                    </h3>
                    <span className="muted-surface rounded-md px-2 py-0.5 text-xs font-semibold uppercase">
                      {job.portal}
                    </span>
                    {job.is_dream_company && (
                      <span className="inline-flex items-center gap-1 rounded-full bg-amber-500/15 px-2.5 py-0.5 text-xs font-semibold text-amber-700 dark:text-amber-300">
                        <Star className="h-3 w-3 fill-current" aria-hidden="true" />
                        Dream Company
                      </span>
                    )}
                  </div>
                  <div className="inline-flex items-center gap-1.5 rounded-lg bg-amber-500/10 px-2.5 py-1 text-xs font-medium text-amber-800 dark:text-amber-300">
                    <AlertTriangle className="h-3.5 w-3.5 shrink-0" aria-hidden="true" />
                    <span>{job.trouble_reason}</span>
                  </div>
                </div>

                <div className="flex flex-wrap gap-2">
                  {job.tailored_pdf_path && (
                    <a
                      href={`/api/v1/jobs/${job.id}/resume/pdf`}
                      target="_blank"
                      rel="noreferrer"
                      className="muted-surface inline-flex min-h-[40px] items-center gap-1.5 rounded-xl px-3 text-xs font-semibold"
                    >
                      <FileText className="h-4 w-4" aria-hidden="true" />
                      Resume PDF
                    </a>
                  )}
                  <a
                    href={job.canonical_url}
                    target="_blank"
                    rel="noreferrer"
                    className="muted-surface inline-flex min-h-[40px] items-center gap-1.5 rounded-xl px-3 text-xs font-semibold"
                  >
                    <ExternalLink className="h-4 w-4" aria-hidden="true" />
                    Open Portal Page
                  </a>
                  <button
                    type="button"
                    disabled={busyJobId === `manual-${job.id}`}
                    onClick={() => handleMarkAppliedManually(job)}
                    className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl bg-blue-600 px-3.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
                  >
                    <CheckCircle2 className="h-4 w-4" aria-hidden="true" />
                    Mark Applied Manually
                  </button>
                  <button
                    type="button"
                    disabled={busyJobId === job.id}
                    onClick={() => handleResume(job, 'APPROVE_OR_EDIT')}
                    className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl bg-emerald-600 px-3.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-60"
                  >
                    <Check className="h-4 w-4" aria-hidden="true" />
                    Approve AI Draft
                  </button>
                  <button
                    type="button"
                    disabled={busyJobId === job.id}
                    onClick={() => handleResume(job, 'SKIP')}
                    className="muted-surface inline-flex min-h-[40px] items-center gap-1.5 rounded-xl px-3 text-xs font-semibold"
                  >
                    <SkipForward className="h-4 w-4" aria-hidden="true" />
                    Skip
                  </button>
                </div>
              </div>

              <div className="mt-5 grid grid-cols-1 gap-6 lg:grid-cols-2">
                <div className="space-y-3">
                  <h4 className="text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                    AI-Prepared Screening Answers ({job.extracted_fields.length})
                  </h4>
                  {job.extracted_fields.length === 0 ? (
                    <p className="muted-surface rounded-xl p-4 text-xs">
                      No unanswered screening fields. Open the portal page above to finish submitting manually, then
                      click <strong>Mark Applied Manually</strong>.
                    </p>
                  ) : (
                    job.extracted_fields.map((field) => {
                      const isLow = field.confidence < 0.85;
                      const currentVal = getFieldValue(job, field.field_id, field.proposed_value || '');
                      return (
                        <div
                          key={field.field_id}
                          className={`rounded-xl border p-3.5 ${
                            isLow
                              ? 'border-amber-500/60 bg-amber-500/5'
                              : 'border-[var(--border-subtle)] bg-[var(--surface-muted)]/40'
                          }`}
                        >
                          <div className="mb-1.5 flex items-center justify-between gap-2">
                            <label htmlFor={`${job.thread_id}-${field.field_id}`} className="text-xs font-semibold">
                              {field.label_text}
                            </label>
                            <span
                              className={`rounded-full px-2 py-0.5 text-[11px] font-bold ${
                                isLow
                                  ? 'bg-amber-500/20 text-amber-800 dark:text-amber-300'
                                  : 'bg-emerald-500/20 text-emerald-700 dark:text-emerald-300'
                              }`}
                            >
                              {(field.confidence * 100).toFixed(0)}%
                            </span>
                          </div>
                          {field.field_type === 'textarea' ? (
                            <textarea
                              id={`${job.thread_id}-${field.field_id}`}
                              rows={3}
                              value={currentVal}
                              onChange={(e) => setFieldValue(job.thread_id, field.field_id, e.target.value)}
                              className="field-input text-xs"
                            />
                          ) : (
                            <input
                              id={`${job.thread_id}-${field.field_id}`}
                              type="text"
                              value={currentVal}
                              onChange={(e) => setFieldValue(job.thread_id, field.field_id, e.target.value)}
                              className="field-input text-xs"
                            />
                          )}
                          <p className="text-muted mt-1 text-[11px]">{field.reasoning}</p>
                        </div>
                      );
                    })
                  )}
                </div>

                <div>
                  <h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-[var(--text-secondary)]">
                    Checkpoint Preview
                  </h4>
                  {job.screenshot_url ? (
                    <img
                      src={job.screenshot_url}
                      alt={`Checkpoint preview for ${job.company_name}`}
                      className="w-full rounded-xl border border-[var(--border-subtle)] object-contain"
                    />
                  ) : (
                    <div className="muted-surface rounded-xl p-6 text-center text-xs">
                      No screenshot preview captured for this entry.
                    </div>
                  )}
                </div>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  );
}
