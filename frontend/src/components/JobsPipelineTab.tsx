import { useState, type FormEvent } from 'react';
import {
  Bot,
  CalendarClock,
  CheckCircle2,
  ExternalLink,
  FileCode,
  FileText,
  Globe,
  LayoutGrid,
  Plus,
  Search,
  Send,
  Sparkles,
  Star,
  Table2,
  Trash2,
  Zap,
} from 'lucide-react';
import { api } from '../api/client';
import type { JobRecord, NavTab, SearchStatus } from '../types';
import { AppDialog } from './AppDialog';

interface JobsPipelineTabProps {
  jobs: JobRecord[];
  searchStatus?: SearchStatus | null;
  onRefresh: () => Promise<void>;
  onNavigate: (tab: NavTab) => void;
  onToast: (msg: string, isError?: boolean) => void;
}

interface ChatMessage {
  role: 'user' | 'assistant';
  text: string;
}

type RecencyWindow = '24h' | '3d' | '7d' | 'all';

const QUICK_PROMPTS = [
  'Filter out the best jobs matching my saved profile (Round-2 Verify)',
  'Only keep Work From Home / Remote AI & Python roles',
  'Focus on GenAI, RAG, LangGraph, and backend roles >= 80% match',
];

function getJobTimestamp(job: JobRecord): string | null {
  return job.match_reasoning?.posted_at || job.discovered_at || null;
}

function getAgeDays(ts: string | null): number | null {
  if (!ts) return null;
  const parsed = Date.parse(ts);
  if (Number.isNaN(parsed)) return null;
  return Math.max(0, (Date.now() - parsed) / 86_400_000);
}

function matchesRecency(job: JobRecord, windowFilter: RecencyWindow): boolean {
  if (windowFilter === 'all') return true;
  const age = getAgeDays(getJobTimestamp(job));
  if (age === null) return windowFilter === '7d';
  if (windowFilter === '24h') return age <= 1.15;
  if (windowFilter === '3d') return age <= 3.15;
  return age <= 7.25;
}

function formatRecencyLabel(job: JobRecord): string {
  const age = getAgeDays(getJobTimestamp(job));
  if (age === null) return 'Recent';
  if (age < 1) return 'Today';
  const days = Math.floor(age);
  return `${days}d ago`;
}

export function JobsPipelineTab({
  jobs,
  searchStatus,
  onRefresh,
  onNavigate,
  onToast,
}: JobsPipelineTabProps) {
  const [searchQuery, setSearchQuery] = useState('');
  const [viewMode, setViewMode] = useState<'ready' | 'verified' | 'all'>('ready');
  const [recencyFilter, setRecencyFilter] = useState<RecencyWindow>('7d');
  const [portalFilter, setPortalFilter] = useState('');
  const [layoutMode, setLayoutMode] = useState<'cards' | 'table'>('cards');
  const [selectedIds, setSelectedIds] = useState<Set<string>>(new Set());

  const [liveBrowserApply, setLiveBrowserApply] = useState(true);
  const [batchBusy, setBatchBusy] = useState(false);
  const [singleBusyId, setSingleBusyId] = useState<string | null>(null);
  const [ingestOpen, setIngestOpen] = useState(false);
  const [clearDialogOpen, setClearDialogOpen] = useState(false);
  const [keepAppliedOnClear, setKeepAppliedOnClear] = useState(true);
  const [clearingBusy, setClearingBusy] = useState(false);

  const [resumeDialogJob, setResumeDialogJob] = useState<JobRecord | null>(null);
  const [resumeTab, setResumeTab] = useState<'pdf' | 'tex'>('pdf');
  const [texContent, setTexContent] = useState('');

  // Agent 2 Conversational Chat Verifier
  const [chatInput, setChatInput] = useState('');
  const [chatBusy, setChatBusy] = useState(false);
  const [messages, setMessages] = useState<ChatMessage[]>([
    {
      role: 'assistant',
      text: 'I am Agent 2 (Round-2 Verifier). Ask me to verify your recent 7-day matches or narrow down by WFH, stack, or salary, then check the jobs you want to auto-apply.',
    },
  ]);

  // Manual ingest form
  const [ingestUrl, setIngestUrl] = useState('');
  const [ingestCompany, setIngestCompany] = useState('');
  const [ingestRole, setIngestRole] = useState('');
  const [ingestLocation, setIngestLocation] = useState('Bengaluru, India');
  const [ingestJd, setIngestJd] = useState('');
  const [ingestBusy, setIngestBusy] = useState(false);

  const activeJobs = jobs.filter((j) => j.status !== 'APPLIED');
  const recent7dJobs = activeJobs.filter((j) => matchesRecency(j, '7d'));
  const round2VerifiedJobs = activeJobs.filter(
    (j) => j.status === 'READY_TO_APPLY' && j.match_reasoning?.round2_verified,
  );
  const readyJobs = activeJobs.filter((j) => j.status === 'READY_TO_APPLY');

  const filteredJobs = activeJobs.filter((job) => {
    if (!matchesRecency(job, recencyFilter)) return false;
    if (viewMode === 'verified' && !(job.status === 'READY_TO_APPLY' && job.match_reasoning?.round2_verified)) {
      return false;
    }
    if (viewMode === 'ready' && job.status !== 'READY_TO_APPLY') {
      return false;
    }
    if (portalFilter && job.portal !== portalFilter) return false;
    if (searchQuery.trim()) {
      const q = searchQuery.trim().toLowerCase();
      const hay = `${job.company_name} ${job.role_title} ${job.location} ${job.portal} ${job.external_job_id || ''}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    return true;
  });

  const activeIdSet = new Set(activeJobs.map((j) => j.id));
  const validSelectedIds = Array.from(selectedIds).filter((id) => activeIdSet.has(id));

  const toggleSelect = (jobId: string) => {
    setSelectedIds((prev) => {
      const next = new Set(prev);
      if (next.has(jobId)) {
        next.delete(jobId);
      } else {
        next.add(jobId);
      }
      return next;
    });
  };

  const selectAllVisible = () => {
    setSelectedIds(new Set(filteredJobs.map((j) => j.id)));
  };

  const selectVerifiedOnly = () => {
    setSelectedIds(new Set(round2VerifiedJobs.map((j) => j.id)));
  };

  const clearSelection = () => {
    setSelectedIds(new Set());
  };

  const runChatVerify = async (promptText: string) => {
    const cleaned = promptText.trim();
    if (!cleaned || chatBusy) return;
    setChatBusy(true);
    setMessages((prev) => [...prev, { role: 'user', text: cleaned }]);
    setChatInput('');
    try {
      const res = await api.chatVerifyJobs({ message: cleaned, scope: 'all_discovered' });
      setMessages((prev) => [...prev, { role: 'assistant', text: res.reply }]);
      setSelectedIds(new Set(res.verified_jobs.map((j) => j.id)));
      setViewMode('verified');
      onToast(
        `Round-2 Verified: ${res.verified_count} shortlisted & auto-selected (${res.rejected_count} filtered out).`,
      );
      await onRefresh();
    } catch (err) {
      const msg = err instanceof Error ? err.message : 'Verification failed';
      setMessages((prev) => [...prev, { role: 'assistant', text: `Error: ${msg}` }]);
      onToast(msg, true);
    } finally {
      setChatBusy(false);
    }
  };

  const handleApplySelected = async () => {
    if (validSelectedIds.length === 0) {
      onToast('Select one or more job listings first using the checkboxes.', true);
      return;
    }
    setBatchBusy(true);
    try {
      const res = await api.applyVerifiedBatch({
        job_ids: validSelectedIds,
        live_browser: liveBrowserApply,
        headless: false,
      });
      setSelectedIds(new Set());
      onToast(
        `Batch complete: ${res.applied_count} applied, ${res.manual_trouble_count} routed to Needs Manual Apply.`,
      );
      await onRefresh();
      if (res.manual_trouble_count > 0 && res.applied_count === 0) {
        onNavigate('hitl');
      } else if (res.applied_count > 0) {
        onNavigate('applied');
      }
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Batch apply failed', true);
    } finally {
      setBatchBusy(false);
    }
  };

  const handleApplySingle = async (job: JobRecord) => {
    setSingleBusyId(job.id);
    try {
      const res = await api.applyVerifiedBatch({
        job_ids: [job.id],
        live_browser: liveBrowserApply,
        headless: false,
      });
      if (res.applied_count > 0) {
        onToast(`Applied to ${job.company_name} — ${job.role_title}!`);
      } else {
        onToast(`${job.company_name} moved to Needs Manual Apply (Dream Company / portal login / extra field).`);
      }
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Single apply failed', true);
    } finally {
      setSingleBusyId(null);
    }
  };

  const handleClearListingsConfirm = async () => {
    setClearingBusy(true);
    try {
      const res = await api.clearJobs(keepAppliedOnClear);
      setSelectedIds(new Set());
      setClearDialogOpen(false);
      onToast(
        `Cleared ${res.deleted_count} listings${res.keep_applied ? ' (kept Applied history safe)' : ''}.`,
      );
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to clear listings', true);
    } finally {
      setClearingBusy(false);
    }
  };

  const handleSeedDemo = async () => {
    try {
      const res = await api.seedSampleJobs();
      onToast(`Added ${res.discovered_count} sample jobs so you can test Agent 2 verification & apply.`);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Seed failed', true);
    }
  };

  const handleOpenResume = async (job: JobRecord) => {
    setResumeDialogJob(job);
    setResumeTab('pdf');
    setTexContent('Loading LaTeX source...');
    try {
      const res = await api.getJobResumeTex(job.id);
      setTexContent(res.tex_content);
    } catch {
      setTexContent('% LaTeX source unavailable for this job.');
    }
  };

  const handleIngestSubmit = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!e.currentTarget.checkValidity()) {
      e.currentTarget.reportValidity();
      return;
    }
    setIngestBusy(true);
    try {
      const res = await api.ingestJob({
        canonical_url: ingestUrl.trim(),
        company_name: ingestCompany.trim(),
        role_title: ingestRole.trim(),
        location: ingestLocation.trim() || 'India',
        jd_raw_text: ingestJd.trim(),
      });
      onToast(`Added ${res.job.company_name} (${res.job.match_score ?? 0}% match)`);
      setIngestUrl('');
      setIngestCompany('');
      setIngestRole('');
      setIngestJd('');
      setIngestOpen(false);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Job ingest failed', true);
    } finally {
      setIngestBusy(false);
    }
  };

  return (
    <div className="space-y-5">
      {/* Dribbble-style KPI Strip */}
      <section className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <div className="card-surface rounded-2xl p-4">
          <p className="text-muted text-xs font-medium">Past 7 Days Found</p>
          <p className="mt-1 text-2xl font-bold tracking-tight">{recent7dJobs.length}</p>
        </div>
        <div className="card-surface rounded-2xl p-4">
          <p className="text-muted text-xs font-medium">High Match (&ge;80%)</p>
          <p className="mt-1 text-2xl font-bold tracking-tight text-emerald-600 dark:text-emerald-400">
            {readyJobs.length}
          </p>
        </div>
        <div className="card-surface rounded-2xl p-4">
          <p className="text-muted text-xs font-medium">Round-2 AI Verified</p>
          <p className="mt-1 text-2xl font-bold tracking-tight text-blue-600 dark:text-sky-400">
            {round2VerifiedJobs.length}
          </p>
        </div>
        <div className="card-surface rounded-2xl p-4">
          <p className="text-muted text-xs font-medium">Selected for Auto-Apply</p>
          <p className="mt-1 text-2xl font-bold tracking-tight">{validSelectedIds.length}</p>
        </div>
      </section>

      {/* Top Action Header */}
      <section className="card-surface flex flex-wrap items-center justify-between gap-4 rounded-2xl p-5">
        <div>
          <h2 className="text-lg font-semibold">Recent Job Matches &amp; Auto-Apply</h2>
          <p className="text-muted text-xs">
            Crawled within the past 7 days · Check only the roles you want submitted, then click Auto-Apply Selected.
          </p>
        </div>

        <div className="flex flex-wrap items-center gap-2.5">
          <label className="brand-check-wrap muted-surface flex min-h-[40px] cursor-pointer items-center gap-2 rounded-xl px-3 text-xs font-medium">
            <input
              type="checkbox"
              className="brand-check"
              checked={liveBrowserApply}
              onChange={(e) => setLiveBrowserApply(e.target.checked)}
            />
            <span className="inline-flex items-center gap-1.5">
              <Globe className="h-3.5 w-3.5 text-emerald-500" aria-hidden="true" />
              Use Live Stealth Browser
            </span>
          </label>

          <button
            type="button"
            onClick={() => setIngestOpen(true)}
            className="muted-surface inline-flex min-h-[40px] items-center gap-1.5 rounded-xl px-3.5 text-xs font-semibold hover:opacity-90"
          >
            <Plus className="h-4 w-4" aria-hidden="true" />
            Add URL
          </button>

          <button
            type="button"
            onClick={() => setClearDialogOpen(true)}
            disabled={activeJobs.length === 0}
            className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl border border-red-500/40 bg-red-500/10 px-3.5 text-xs font-semibold text-red-700 hover:bg-red-500/20 disabled:opacity-50 dark:text-red-300"
          >
            <Trash2 className="h-4 w-4" aria-hidden="true" />
            Clear Listings
          </button>

          <button
            type="button"
            disabled={batchBusy || validSelectedIds.length === 0}
            onClick={handleApplySelected}
            className="inline-flex min-h-[42px] items-center gap-2 rounded-xl bg-emerald-600 px-5 text-xs font-semibold text-white shadow-sm hover:bg-emerald-700 disabled:opacity-50"
          >
            <Zap className="h-4 w-4" aria-hidden="true" />
            {batchBusy ? 'Auto-Applying...' : `Auto-Apply Selected (${validSelectedIds.length})`}
          </button>
        </div>
      </section>

      {searchStatus?.running && (
        <section className="card-surface space-y-2 rounded-2xl border-blue-500/40 bg-blue-600/10 p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <label
              htmlFor="discovery-progress-bar"
              className="text-xs font-semibold text-blue-700 dark:text-sky-300"
            >
              {searchStatus.message}
            </label>
            <span className="text-muted text-xs font-medium">
              {searchStatus.total_to_process > 0
                ? `${searchStatus.processed_count} / ${searchStatus.total_to_process} (${searchStatus.ready_to_apply_count} high-fit)`
                : searchStatus.phase}
            </span>
          </div>
          <progress
            id="discovery-progress-bar"
            className="discovery-progress h-2.5 w-full"
            max={searchStatus.total_to_process > 0 ? searchStatus.total_to_process : undefined}
            value={searchStatus.total_to_process > 0 ? searchStatus.processed_count : undefined}
          />
        </section>
      )}

      {/* Main Split: Jobs Workspace (Left) + Agent 2 Chat Copilot (Right) */}
      <div className="grid grid-cols-1 gap-5 xl:grid-cols-12">
        <div className="space-y-4 xl:col-span-8">
          <search className="card-surface space-y-3 rounded-2xl p-4">
            <form onSubmit={(e) => e.preventDefault()} className="flex flex-wrap items-center gap-3">
              <div className="flex flex-wrap gap-1.5">
                {(
                  [
                    { id: 'ready', label: `High Match (${readyJobs.length})` },
                    { id: 'verified', label: `Verified (${round2VerifiedJobs.length})` },
                    { id: 'all', label: `All (${activeJobs.length})` },
                  ] as const
                ).map((pill) => (
                  <button
                    key={pill.id}
                    type="button"
                    onClick={() => setViewMode(pill.id)}
                    className={`rounded-xl px-3 py-1.5 text-xs font-semibold transition-colors ${
                      viewMode === pill.id ? 'bg-blue-600 text-white' : 'muted-surface'
                    }`}
                  >
                    {pill.label}
                  </button>
                ))}
              </div>

              <div className="relative min-w-[180px] flex-1">
                <label htmlFor="jobs-search" className="visually-hidden">
                  Filter jobs
                </label>
                <Search
                  className="text-muted pointer-events-none absolute top-1/2 left-3 h-4 w-4 -translate-y-1/2"
                  aria-hidden="true"
                />
                <input
                  id="jobs-search"
                  type="search"
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  placeholder="Search company, role, location..."
                  className="field-input pl-9"
                />
              </div>

              <div>
                <label htmlFor="portal-filter" className="visually-hidden">
                  Filter by portal
                </label>
                <select
                  id="portal-filter"
                  value={portalFilter}
                  onChange={(e) => setPortalFilter(e.target.value)}
                  className="field-input w-36"
                >
                  <option value="">All Portals</option>
                  <option value="greenhouse">Greenhouse</option>
                  <option value="lever">Lever</option>
                  <option value="ashby">Ashby</option>
                  <option value="smartrecruiters">SmartRecruiters</option>
                  <option value="linkedin_easy">LinkedIn</option>
                  <option value="naukri">Naukri</option>
                </select>
              </div>
            </form>

            {/* Recency Filter + Selection Helpers + Layout Switcher */}
            <div className="flex flex-wrap items-center justify-between gap-2 border-t border-[var(--border-subtle)] pt-3">
              <div className="flex flex-wrap items-center gap-1.5">
                <span className="text-muted mr-1 inline-flex items-center gap-1 text-xs font-medium">
                  <CalendarClock className="h-3.5 w-3.5" aria-hidden="true" />
                  Posted:
                </span>
                {(
                  [
                    { id: '24h', label: 'Past 24h' },
                    { id: '3d', label: 'Past 3 Days' },
                    { id: '7d', label: 'Past 7 Days' },
                    { id: 'all', label: 'All Saved' },
                  ] as const
                ).map((win) => (
                  <button
                    key={win.id}
                    type="button"
                    onClick={() => setRecencyFilter(win.id)}
                    className={`rounded-lg px-2.5 py-1 text-xs font-semibold transition-colors ${
                      recencyFilter === win.id ? 'bg-blue-600 text-white' : 'muted-surface'
                    }`}
                  >
                    {win.label}
                  </button>
                ))}
              </div>

              <div className="flex flex-wrap items-center gap-1.5">
                <button
                  type="button"
                  onClick={selectAllVisible}
                  disabled={filteredJobs.length === 0}
                  className="muted-surface rounded-lg px-2.5 py-1 text-xs font-semibold hover:opacity-90 disabled:opacity-50"
                >
                  Select All ({filteredJobs.length})
                </button>
                {round2VerifiedJobs.length > 0 && (
                  <button
                    type="button"
                    onClick={selectVerifiedOnly}
                    className="muted-surface rounded-lg px-2.5 py-1 text-xs font-semibold hover:opacity-90"
                  >
                    Select Verified
                  </button>
                )}
                {validSelectedIds.length > 0 && (
                  <button
                    type="button"
                    onClick={clearSelection}
                    className="muted-surface rounded-lg px-2.5 py-1 text-xs font-semibold hover:opacity-90"
                  >
                    Unselect ({validSelectedIds.length})
                  </button>
                )}

                <div role="group" aria-label="Jobs view layout" className="muted-surface inline-flex rounded-lg p-0.5">
                  <button
                    type="button"
                    onClick={() => setLayoutMode('cards')}
                    aria-pressed={layoutMode === 'cards'}
                    aria-label="Card grid layout"
                    className={`rounded-md p-1.5 ${layoutMode === 'cards' ? 'bg-blue-600 text-white' : 'text-muted'}`}
                  >
                    <LayoutGrid className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                  <button
                    type="button"
                    onClick={() => setLayoutMode('table')}
                    aria-pressed={layoutMode === 'table'}
                    aria-label="Table layout"
                    className={`rounded-md p-1.5 ${layoutMode === 'table' ? 'bg-blue-600 text-white' : 'text-muted'}`}
                  >
                    <Table2 className="h-3.5 w-3.5" aria-hidden="true" />
                  </button>
                </div>
              </div>
            </div>
          </search>

          {filteredJobs.length === 0 ? (
            <div className="card-surface rounded-2xl p-8 text-center">
              <p className="text-sm font-semibold">
                {searchStatus?.running
                  ? 'Agent 1 is crawling & scoring past-7-day jobs right now...'
                  : 'No listings match the current filters'}
              </p>
              <p className="text-muted mt-1 text-xs">
                {searchStatus?.running
                  ? 'Matches will stream in automatically every few seconds as JDs finish scoring.'
                  : activeJobs.length > 0
                    ? 'Try switching to "All" or "All Saved" recency filter above, or start a fresh search in Step 1.'
                    : 'Click "Start Autonomous Search" in Tab 1 (Profile & Start Search), or seed sample jobs to test right away.'}
              </p>
              <div className="mt-4 flex flex-wrap justify-center gap-2">
                <button
                  type="button"
                  onClick={() => onNavigate('profile')}
                  className="rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white"
                >
                  Go to Profile &amp; Start Search
                </button>
                {activeJobs.length === 0 && (
                  <button
                    type="button"
                    onClick={handleSeedDemo}
                    className="muted-surface inline-flex items-center gap-1.5 rounded-xl px-4 py-2 text-xs font-semibold"
                  >
                    <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
                    Seed Sample Roles
                  </button>
                )}
              </div>
            </div>
          ) : layoutMode === 'cards' ? (
            <div className="grid grid-cols-1 gap-4 md:grid-cols-2">
              {filteredJobs.map((job) => {
                const checked = selectedIds.has(job.id);
                const isVerified = Boolean(job.match_reasoning?.round2_verified);
                const workMode = job.match_reasoning?.work_mode_detected || 'unspecified';
                const initials = (job.company_name || 'AI').slice(0, 2).toUpperCase();

                return (
                  <article key={job.id} className="job-card flex flex-col justify-between gap-3 p-4">
                    <div className="flex items-start justify-between gap-3">
                      <div className="flex min-w-0 items-start gap-3">
                        <div className="flex h-10 w-10 shrink-0 items-center justify-center rounded-xl bg-blue-600/12 text-xs font-bold text-blue-700 dark:text-sky-300">
                          {initials}
                        </div>
                        <div className="min-w-0">
                          <div className="flex items-center gap-1.5">
                            <p className="text-muted truncate text-xs font-semibold">{job.company_name}</p>
                            {job.is_dream_company && (
                              <Star
                                className="h-3.5 w-3.5 shrink-0 fill-amber-500 text-amber-500"
                                aria-label="Dream Company"
                              />
                            )}
                          </div>
                          <h3 className="mt-0.5 line-clamp-2 text-sm font-bold leading-snug">{job.role_title}</h3>
                          <p className="text-muted mt-1 truncate text-xs">{job.location}</p>
                        </div>
                      </div>

                      <div className="shrink-0 text-right">
                        <span
                          className={`inline-block rounded-lg px-2 py-0.5 text-xs font-bold ${
                            (job.match_score ?? 0) >= 80
                              ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
                              : 'bg-amber-500/15 text-amber-800 dark:text-amber-300'
                          }`}
                        >
                          {job.match_score !== null ? `${job.match_score}%` : '—'}
                        </span>
                      </div>
                    </div>

                    <div className="flex flex-wrap items-center gap-1.5 text-[11px] font-medium">
                      <span className="muted-surface rounded-md px-2 py-0.5 uppercase">{job.portal}</span>
                      <span className="muted-surface rounded-md px-2 py-0.5">{formatRecencyLabel(job)}</span>
                      {workMode !== 'unspecified' && (
                        <span className="rounded-md bg-blue-500/12 px-2 py-0.5 text-blue-700 dark:text-sky-300">
                          {workMode.replace('_', ' ')}
                        </span>
                      )}
                      {isVerified && (
                        <span className="inline-flex items-center gap-1 rounded-md bg-emerald-500/15 px-2 py-0.5 text-emerald-700 dark:text-emerald-300">
                          <CheckCircle2 className="h-3 w-3" aria-hidden="true" />
                          Verified
                        </span>
                      )}
                    </div>

                    {job.match_reasoning?.round2_reason && (
                      <p className="text-muted line-clamp-2 text-xs">{job.match_reasoning.round2_reason}</p>
                    )}

                    <footer className="job-card-footer border-t border-[var(--border-subtle)] pt-3">
                      <label className="brand-check-wrap inline-flex cursor-pointer items-center gap-2 text-xs font-semibold">
                        <input
                          type="checkbox"
                          className="brand-check"
                          checked={checked}
                          onChange={() => toggleSelect(job.id)}
                        />
                        <span>Select</span>
                      </label>

                      <div className="flex flex-wrap items-center gap-1.5">
                        {job.tailored_pdf_path && (
                          <button
                            type="button"
                            onClick={() => handleOpenResume(job)}
                            className="muted-surface inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-semibold"
                          >
                            <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                            PDF
                          </button>
                        )}
                        <button
                          type="button"
                          disabled={singleBusyId === job.id}
                          onClick={() => handleApplySingle(job)}
                          className="inline-flex items-center gap-1 rounded-lg bg-emerald-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-emerald-700 disabled:opacity-50"
                        >
                          <Zap className="h-3.5 w-3.5" aria-hidden="true" />
                          {singleBusyId === job.id ? '...' : 'Apply'}
                        </button>
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
                    </footer>
                  </article>
                );
              })}
            </div>
          ) : (
            <div tabIndex={0} role="region" aria-labelledby="jobs-table-caption" className="table-wrapper">
              <table className="data-table responsive-jobs-table">
                <caption id="jobs-table-caption" className="visually-hidden">
                  Recent and Round-2 verified jobs
                </caption>
                <thead>
                  <tr>
                    <th>Pick</th>
                    <th>Company</th>
                    <th>Role &amp; Posted</th>
                    <th>Portal</th>
                    <th>Match</th>
                    <th>Actions</th>
                  </tr>
                </thead>
                <tbody>
                  {filteredJobs.map((job) => {
                    const checked = selectedIds.has(job.id);
                    const isVerified = Boolean(job.match_reasoning?.round2_verified);
                    return (
                      <tr key={job.id}>
                        <td>
                          <label className="brand-check-wrap inline-flex cursor-pointer items-center gap-1.5">
                            <input
                              type="checkbox"
                              className="brand-check"
                              aria-label={`Select ${job.company_name} ${job.role_title}`}
                              checked={checked}
                              onChange={() => toggleSelect(job.id)}
                            />
                            <span className="visually-hidden">Select</span>
                          </label>
                        </td>
                        <th>
                          <div className="flex items-center gap-1.5">
                            <span>{job.company_name}</span>
                            {job.is_dream_company && (
                              <Star className="h-3.5 w-3.5 fill-amber-500 text-amber-500" aria-label="Dream Company" />
                            )}
                          </div>
                        </th>
                        <td>
                          <div className="font-medium">{job.role_title}</div>
                          <div className="text-muted text-xs">
                            {job.location} · {formatRecencyLabel(job)}
                            {isVerified ? ' · Verified' : ''}
                          </div>
                        </td>
                        <td>
                          <span className="muted-surface rounded-md px-2 py-0.5 text-xs font-medium uppercase">
                            {job.portal}
                          </span>
                        </td>
                        <td>
                          <span
                            className={`font-semibold ${
                              (job.match_score ?? 0) >= 80
                                ? 'text-emerald-600 dark:text-emerald-400'
                                : 'text-amber-600 dark:text-amber-400'
                            }`}
                          >
                            {job.match_score !== null ? `${job.match_score}%` : '—'}
                          </span>
                        </td>
                        <td>
                          <div className="flex items-center gap-1.5">
                            {job.tailored_pdf_path && (
                              <button
                                type="button"
                                onClick={() => handleOpenResume(job)}
                                className="muted-surface inline-flex items-center gap-1 rounded-lg px-2.5 py-1.5 text-xs font-medium"
                              >
                                <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                                PDF
                              </button>
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
                    );
                  })}
                </tbody>
              </table>
            </div>
          )}
        </div>

        {/* Sticky Agent 2 Conversational Filter & Round-2 Verifier */}
        <aside className="card-surface flex h-fit flex-col justify-between rounded-2xl p-4 xl:sticky xl:top-6 xl:col-span-4">
          <div className="space-y-3">
            <div className="flex items-center gap-2 border-b border-[var(--border-subtle)] pb-3">
              <Bot className="h-5 w-5 text-blue-500" aria-hidden="true" />
              <div>
                <h3 className="text-sm font-semibold">Agent 2: AI Filter &amp; Verifier</h3>
                <p className="text-muted text-[11px]">Verifies &amp; auto-checks matching listings</p>
              </div>
            </div>

            <div className="flex flex-wrap gap-1.5">
              {QUICK_PROMPTS.map((q) => (
                <button
                  key={q}
                  type="button"
                  disabled={chatBusy}
                  onClick={() => runChatVerify(q)}
                  className="muted-surface rounded-lg px-2.5 py-1.5 text-left text-[11px] font-medium hover:opacity-90 disabled:opacity-50"
                >
                  {q}
                </button>
              ))}
            </div>

            <div className="app-scroll max-h-[340px] space-y-2.5 overflow-y-auto py-2">
              {messages.map((m, idx) => (
                <div
                  key={idx}
                  className={`rounded-xl p-3 text-xs leading-relaxed ${
                    m.role === 'user' ? 'ml-6 bg-blue-600 text-white' : 'muted-surface mr-4'
                  }`}
                >
                  {m.text}
                </div>
              ))}
            </div>
          </div>

          <form
            onSubmit={(e) => {
              e.preventDefault();
              runChatVerify(chatInput);
            }}
            className="mt-3 flex gap-2 border-t border-[var(--border-subtle)] pt-3"
          >
            <label htmlFor="agent-verify-input" className="visually-hidden">
              Ask Agent 2 to filter and verify discovered jobs
            </label>
            <input
              id="agent-verify-input"
              type="text"
              value={chatInput}
              onChange={(e) => setChatInput(e.target.value)}
              placeholder="e.g. Only WFH roles for my profile..."
              className="field-input flex-1 text-xs"
            />
            <button
              type="submit"
              disabled={chatBusy || !chatInput.trim()}
              aria-label="Send filter request"
              className="inline-flex min-h-[44px] items-center justify-center rounded-xl bg-blue-600 px-3.5 text-white hover:bg-blue-700 disabled:opacity-50"
            >
              <Send className="h-4 w-4" aria-hidden="true" />
            </button>
          </form>
        </aside>
      </div>

      {/* Clear Listings Confirmation Modal */}
      <AppDialog
        open={clearDialogOpen}
        onClose={() => setClearDialogOpen(false)}
        title="Clear Discovered Listings"
        titleId="clear-listings-title"
        maxWidthClass="max-w-md"
      >
        <div className="space-y-4 text-xs">
          <p className="text-muted leading-relaxed">
            This clears the current discovered/rejected job listings so you can start fresh with your latest profile
            and 7-day search settings.
          </p>
          <label className="brand-check-wrap muted-surface flex cursor-pointer items-center gap-2 rounded-xl p-3 font-medium">
            <input
              type="checkbox"
              className="brand-check"
              checked={keepAppliedOnClear}
              onChange={(e) => setKeepAppliedOnClear(e.target.checked)}
            />
            <span>Keep my Applied Today &amp; past application history safe</span>
          </label>
          <div className="flex justify-end gap-2 pt-2">
            <button
              type="button"
              onClick={() => setClearDialogOpen(false)}
              className="muted-surface rounded-xl px-4 py-2 font-semibold"
            >
              Cancel
            </button>
            <button
              type="button"
              disabled={clearingBusy}
              onClick={handleClearListingsConfirm}
              className="rounded-xl bg-red-600 px-4 py-2 font-semibold text-white hover:bg-red-700 disabled:opacity-60"
            >
              {clearingBusy ? 'Clearing...' : 'Confirm Clear'}
            </button>
          </div>
        </div>
      </AppDialog>

      {/* Manual Job Ingest Modal */}
      <AppDialog
        open={ingestOpen}
        onClose={() => setIngestOpen(false)}
        title="Add & Score Job Manually"
        titleId="ingest-modal-title"
      >
        <form onSubmit={handleIngestSubmit} className="space-y-4">
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="ingest-company" className="mb-1 block text-xs font-semibold">
                Company Name *
              </label>
              <input
                id="ingest-company"
                type="text"
                required
                autoFocus
                value={ingestCompany}
                onChange={(e) => setIngestCompany(e.target.value)}
                className="field-input"
              />
            </div>
            <div>
              <label htmlFor="ingest-role" className="mb-1 block text-xs font-semibold">
                Role Title *
              </label>
              <input
                id="ingest-role"
                type="text"
                required
                value={ingestRole}
                onChange={(e) => setIngestRole(e.target.value)}
                className="field-input"
              />
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <div>
              <label htmlFor="ingest-url" className="mb-1 block text-xs font-semibold">
                Job Application URL *
              </label>
              <input
                id="ingest-url"
                type="url"
                required
                placeholder="https://boards.greenhouse.io/company/jobs/123"
                value={ingestUrl}
                onChange={(e) => setIngestUrl(e.target.value)}
                className="field-input"
              />
            </div>
            <div>
              <label htmlFor="ingest-loc" className="mb-1 block text-xs font-semibold">
                Location
              </label>
              <input
                id="ingest-loc"
                type="text"
                value={ingestLocation}
                onChange={(e) => setIngestLocation(e.target.value)}
                className="field-input"
              />
            </div>
          </div>

          <div>
            <label htmlFor="ingest-jd" className="mb-1 block text-xs font-semibold">
              Job Description Text *
            </label>
            <textarea
              id="ingest-jd"
              required
              rows={5}
              value={ingestJd}
              onChange={(e) => setIngestJd(e.target.value)}
              className="field-input"
            />
          </div>

          <div className="flex justify-end gap-2">
            <button
              type="button"
              onClick={() => setIngestOpen(false)}
              className="muted-surface rounded-xl px-4 py-2 text-xs font-semibold"
            >
              Cancel
            </button>
            <button
              type="submit"
              disabled={ingestBusy}
              className="rounded-xl bg-blue-600 px-4 py-2 text-xs font-semibold text-white"
            >
              {ingestBusy ? 'Scoring...' : 'Add Job'}
            </button>
          </div>
        </form>
      </AppDialog>

      {/* Tailored Resume Viewer Modal */}
      <AppDialog
        open={resumeDialogJob !== null}
        onClose={() => setResumeDialogJob(null)}
        title={
          resumeDialogJob
            ? `Tailored Resume — ${resumeDialogJob.company_name} (${resumeDialogJob.role_title})`
            : 'Tailored Resume'
        }
        titleId="resume-modal-title"
        maxWidthClass="max-w-5xl"
      >
        {resumeDialogJob && (
          <div className="space-y-4">
            <div className="flex flex-wrap items-center justify-between gap-3">
              <div className="flex gap-2">
                <button
                  type="button"
                  onClick={() => setResumeTab('pdf')}
                  className={`inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-semibold ${
                    resumeTab === 'pdf' ? 'bg-blue-600 text-white' : 'muted-surface'
                  }`}
                >
                  <FileText className="h-3.5 w-3.5" aria-hidden="true" />
                  Compiled PDF
                </button>
                <button
                  type="button"
                  onClick={() => setResumeTab('tex')}
                  className={`inline-flex items-center gap-1.5 rounded-xl px-3.5 py-2 text-xs font-semibold ${
                    resumeTab === 'tex' ? 'bg-blue-600 text-white' : 'muted-surface'
                  }`}
                >
                  <FileCode className="h-3.5 w-3.5" aria-hidden="true" />
                  LaTeX Source
                </button>
              </div>
              <a
                href={`/api/v1/jobs/${resumeDialogJob.id}/resume/pdf?download=true`}
                download
                className="inline-flex items-center gap-1.5 rounded-xl bg-blue-600 px-3.5 py-2 text-xs font-semibold text-white hover:bg-blue-700"
              >
                <ExternalLink className="h-3.5 w-3.5" aria-hidden="true" />
                Download Resume PDF
              </a>
            </div>

            {resumeTab === 'pdf' ? (
              <iframe
                title="Tailored Resume PDF Preview"
                src={`/api/v1/jobs/${resumeDialogJob.id}/resume/pdf`}
                className="h-[62vh] w-full rounded-xl border border-[var(--border-subtle)] bg-white"
              />
            ) : (
              <pre className="app-scroll max-h-[62vh] overflow-auto rounded-xl border border-[var(--border-subtle)] bg-slate-950 p-4 text-xs text-slate-100">
                <code>{texContent}</code>
              </pre>
            )}
          </div>
        )}
      </AppDialog>
    </div>
  );
}
