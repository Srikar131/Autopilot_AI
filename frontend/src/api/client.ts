import type {
  AppliedTodayResponse,
  CandidateProfile,
  CompanyRegistrySummary,
  DashboardOverview,
  FormFieldSpec,
  HitlThread,
  JobRecord,
  ManualQueueJob,
  ProfileBlueprint,
  QaCacheEntry,
  SearchStatus,
  SystemReadiness,
} from '../types';

async function requestJson<T>(url: string, init?: RequestInit): Promise<T> {
  const resp = await fetch(url, {
    headers: { 'Content-Type': 'application/json', ...(init?.headers || {}) },
    ...init,
  });
  if (!resp.ok) {
    const bodyText = await resp.text();
    throw new Error(bodyText || `HTTP ${resp.status}`);
  }
  return resp.json() as Promise<T>;
}

export const api = {
  getReadiness: () => requestJson<SystemReadiness>('/api/v1/system/readiness'),
  getOverview: () => requestJson<DashboardOverview>('/api/v1/dashboard/overview'),

  listJobs: (params?: { portal?: string; status?: string; search?: string; dream_only?: boolean }) => {
    const qs = new URLSearchParams();
    if (params?.portal) qs.set('portal', params.portal);
    if (params?.status) qs.set('status', params.status);
    if (params?.search) qs.set('search', params.search);
    if (params?.dream_only) qs.set('dream_only', 'true');
    const suffix = qs.toString() ? `?${qs.toString()}` : '';
    return requestJson<{ count: number; jobs: JobRecord[] }>(`/api/v1/jobs${suffix}`);
  },

  seedSampleJobs: () =>
    requestJson<{ discovered_count: number; ready_to_apply_count: number; rejected_count: number }>(
      '/api/v1/jobs/seed-sample',
      { method: 'POST' },
    ),

  ingestJob: (payload: {
    canonical_url: string;
    company_name: string;
    role_title: string;
    location: string;
    jd_raw_text: string;
    is_dream_company?: boolean;
  }) => requestJson<{ job: JobRecord }>('/api/v1/jobs/ingest', { method: 'POST', body: JSON.stringify(payload) }),

  applyToJob: (
    jobId: string,
    payload: { live_browser?: boolean; force_supervised_hitl?: boolean; sample_fields?: FormFieldSpec[] } = {},
  ) =>
    requestJson<{ thread_id: string; final_status: string; job: JobRecord }>(`/api/v1/jobs/${jobId}/apply`, {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  getJobResumeTex: (jobId: string) =>
    requestJson<{ job_id: string; tex_path: string; tex_content: string }>(`/api/v1/jobs/${jobId}/resume/tex`),

  runDiscovery: (payload: {
    search_terms?: string[];
    locations?: string[];
    email_bodies?: string[];
    greenhouse_slugs?: string[];
    lever_slugs?: string[];
    ashby_slugs?: string[];
    smartrecruiters_slugs?: string[];
    poll_gmail_imap?: boolean;
    sweep_company_registry?: boolean;
    run_ats_dorks?: boolean;
    results_wanted?: number;
  }) =>
    requestJson<{ discovered_count: number; ready_to_apply_count: number; rejected_count: number }>(
      '/api/v1/discovery/run',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  hydrateJob: (jobId: string) =>
    requestJson<{ status: string; job: JobRecord }>(`/api/v1/jobs/${jobId}/hydrate`, {
      method: 'POST',
    }),

  getCompanyRegistry: () => requestJson<CompanyRegistrySummary>('/api/v1/discovery/registry'),

  addCompanyRegistry: (payload: { career_url?: string; ats?: string; slug?: string }) =>
    requestJson<{ status: string; ats: string; slug: string; summary: CompanyRegistrySummary }>(
      '/api/v1/discovery/registry/add',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  warmupBrowserProfile: (portal: string) =>
    requestJson<{ status: string; portal: string; login_url: string; duration_seconds: number }>(
      `/api/v1/browser/warmup/${portal}`,
      { method: 'POST' },
    ),

  clearBrowserProfile: (portal: string) =>
    requestJson<{ portal: string; cleared: boolean }>(`/api/v1/browser/profile/${portal}`, {
      method: 'DELETE',
    }),

  clearJobs: (keepApplied = true) =>
    requestJson<{ status: string; deleted_count: number; keep_applied: boolean }>('/api/v1/jobs/clear', {
      method: 'POST',
      body: JSON.stringify({ keep_applied: keepApplied }),
    }),

  triggerBatch: (payload: { portal: string; max_jobs: number; supervised_mode: boolean; execute_now: boolean }) =>
    requestJson<Record<string, unknown>>('/api/v1/orchestrator/trigger-batch', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  listHitlThreads: () => requestJson<{ count: number; threads: HitlThread[] }>('/api/v1/hitl/threads'),

  resumeHitlThread: (payload: {
    langgraph_thread_id: string;
    action: 'APPROVE_OR_EDIT' | 'SKIP';
    field_overrides?: Record<string, string>;
    save_to_qa_cache?: boolean;
    live_browser_submit?: boolean;
  }) =>
    requestJson<{ status: string; job_id: string; final_status: string; qa_cache_entries_added: number }>(
      '/api/v1/hitl/resume',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  tripBreaker: (portal: string, reason: string) =>
    requestJson('/api/v1/circuit-breaker/trip', {
      method: 'POST',
      body: JSON.stringify({ portal, reason }),
    }),

  resetBreaker: (portal: string) =>
    requestJson('/api/v1/circuit-breaker/reset', {
      method: 'POST',
      body: JSON.stringify({ portal }),
    }),

  triggerSchedulerJob: (jobName: string) =>
    requestJson<{ status: string; job_name: string; result: unknown }>(`/api/v1/scheduler/trigger/${jobName}`, {
      method: 'POST',
    }),

  getProfile: () => requestJson<{ profile: CandidateProfile; is_placeholder_contact: boolean }>('/api/v1/profile'),

  updateProfile: (profile: CandidateProfile) =>
    requestJson<{ status: string; profile: CandidateProfile; is_placeholder_contact: boolean }>('/api/v1/profile', {
      method: 'PUT',
      body: JSON.stringify(profile),
    }),

  previewProfileResume: () =>
    requestJson<{ pdf_url: string; tex_content: string }>('/api/v1/profile/preview-resume', { method: 'POST' }),

  listQaCache: () => requestJson<{ count: number; items: QaCacheEntry[] }>('/api/v1/qa-cache'),

  createQaCache: (payload: {
    question_normalized: string;
    field_type: string;
    canonical_answer: string;
    portal_context: string;
  }) => requestJson<{ item: QaCacheEntry }>('/api/v1/qa-cache', { method: 'POST', body: JSON.stringify(payload) }),

  deleteQaCache: (id: string) => requestJson(`/api/v1/qa-cache/${id}`, { method: 'DELETE' }),

  resolvePreview: (payload: { label_text: string; field_type: string; options?: string[] }) =>
    requestJson<{ resolved: FormFieldSpec; will_trigger_hitl: boolean; confidence_threshold: number }>(
      '/api/v1/qa-cache/resolve-preview',
      { method: 'POST', body: JSON.stringify(payload) },
    ),

  getProfileBlueprint: (refresh = false) =>
    requestJson<{ blueprint: ProfileBlueprint; cached: boolean }>(
      `/api/v1/pipeline/blueprint${refresh ? '?refresh=true' : ''}`,
    ),

  getSearchStatus: () => requestJson<SearchStatus>('/api/v1/pipeline/search-status'),

  startAutonomousSearch: (payload?: {
    results_wanted?: number;
    use_ephemeral_chromium?: boolean;
    sweep_company_registry?: boolean;
    run_ats_dorks?: boolean;
    poll_gmail_imap?: boolean;
    background?: boolean;
  }) =>
    requestJson<{
      status?: string;
      blueprint: ProfileBlueprint;
      search_status?: SearchStatus;
      discovery: { discovered_count: number; ready_to_apply_count: number; rejected_count: number };
    }>('/api/v1/pipeline/start-autonomous-search', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    }),

  chatVerifyJobs: (payload: { message: string; scope?: 'all_discovered' | 'ready_only' }) =>
    requestJson<{
      reply: string;
      evaluated_count: number;
      verified_count: number;
      rejected_count: number;
      verified_jobs: JobRecord[];
    }>('/api/v1/agent/chat-verify', {
      method: 'POST',
      body: JSON.stringify(payload),
    }),

  applyVerifiedBatch: (payload?: { job_ids?: string[]; live_browser?: boolean; headless?: boolean }) =>
    requestJson<{
      total_attempted: number;
      applied_count: number;
      manual_trouble_count: number;
      skipped_quota_count: number;
      results: Array<{ job_id: string; final_status: string; reason?: string | null }>;
    }>('/api/v1/orchestrator/apply-verified-batch', {
      method: 'POST',
      body: JSON.stringify(payload || {}),
    }),

  getManualQueue: () => requestJson<{ count: number; jobs: ManualQueueJob[] }>('/api/v1/jobs/manual-queue'),

  markJobAppliedManually: (jobId: string) =>
    requestJson<{ status: string; job: JobRecord }>(`/api/v1/jobs/${jobId}/mark-applied`, { method: 'POST' }),

  getAppliedToday: () => requestJson<AppliedTodayResponse>('/api/v1/jobs/applied-today'),
};
