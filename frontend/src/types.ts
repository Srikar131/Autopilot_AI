export type NavTab = 'profile' | 'jobs' | 'hitl' | 'applied' | 'safety';

export type ColorSchemePreference = 'system' | 'light' | 'dark';

export type WorkModePreference = 'any' | 'remote_wfh' | 'hybrid' | 'onsite';

export interface FormFieldSpec {
  field_id: string;
  label_text: string;
  field_type: 'text' | 'textarea' | 'number' | 'select' | 'radio' | 'checkbox' | 'file';
  options: string[] | null;
  proposed_value: string | null;
  confidence: number;
  reasoning: string;
  bounding_box: Record<string, number> | null;
}

export interface MatchReasoning {
  match_score: number;
  matched_skills: string[];
  missing_critical_skills: string[];
  selected_project_titles: string[];
  tailored_summary: string;
  hard_blocker_detected: boolean;
  blocker_reason: string;
  stage?: string;
  round2_verified?: boolean;
  round2_reason?: string;
  work_mode_detected?: string;
  posted_at?: string | null;
  trouble_reason?: string;
}

export interface ProfileBlueprint {
  persona_headline: string;
  executive_summary: string;
  search_terms: string[];
  locations: string[];
  work_mode_preference: WorkModePreference | string;
  min_salary_lpa: number;
  target_salary_lpa: number;
  experience_years_range: string;
  must_have_keywords: string[];
  dealbreakers: string[];
  naukri_urls: string[];
  linkedin_guest_urls: string[];
}

export interface SearchStatus {
  running: boolean;
  phase: string;
  message: string;
  discovered_count: number;
  processed_count: number;
  total_to_process: number;
  ready_to_apply_count: number;
  rejected_count: number;
  started_at?: string | null;
  finished_at?: string | null;
  error?: string | null;
}

export interface ManualQueueJob extends JobRecord {
  thread_id: string;
  trouble_reason: string;
  extracted_fields: FormFieldSpec[];
  low_confidence_fields: FormFieldSpec[];
  screenshot_url: string | null;
}

export interface AppliedTodayResponse {
  date_ist: string;
  today_count: number;
  total_applied_count: number;
  today_jobs: JobRecord[];
  all_applied_jobs: JobRecord[];
}

export interface JobRecord {
  id: string;
  canonical_url: string;
  url_hash: string;
  external_job_id?: string;
  discovery_source?: string;
  portal: string;
  company_name: string;
  role_title: string;
  location: string;
  jd_raw_text: string;
  match_score: number | null;
  match_reasoning: MatchReasoning | null;
  is_dream_company: boolean;
  status: string;
  tailored_pdf_path: string | null;
  discovered_at?: string;
  applied_at?: string | null;
}

export interface CompanyRegistrySummary {
  total_companies: number;
  counts_by_ats: Record<string, number>;
  slugs_by_ats: Record<string, string[]>;
  auto_discovered: Record<string, string[]>;
  custom_added: Record<string, string[]>;
}

export interface PortalQuota {
  portal: string;
  quota_date: string;
  applied_count: number;
  max_daily_limit: number;
  remaining: number;
  circuit_breaker_tripped: boolean;
}

export interface SchedulerJobInfo {
  id: string;
  next_run_time: string | null;
  trigger: string;
}

export interface ApplicationEventRecord {
  id: string;
  job_id: string;
  langgraph_thread_id: string;
  event_type: string;
  screenshot_path?: string | null;
  created_at: string;
}

export interface DashboardOverview {
  total_jobs: number;
  status_counts: Record<string, number>;
  portal_counts: Record<string, number>;
  average_match_score: number;
  paused_hitl_count: number;
  daily_quotas: PortalQuota[];
  circadian_window_open: boolean;
  tripped_portals: Record<string, string>;
  scheduler_jobs: SchedulerJobInfo[];
  recent_events: ApplicationEventRecord[];
}

export interface PortalProfileStatus {
  portal: string;
  path: string;
  exists: boolean;
  session_initialized: boolean;
  entry_count: number;
}

export interface SystemReadiness {
  supabase: {
    client_configured: boolean;
    schema_ready: boolean;
    mode: 'supabase' | 'local_fallback';
    detail: string;
    url_configured: boolean;
    service_key_configured: boolean;
    database_url_configured: boolean;
  };
  llm: {
    gemini_configured: boolean;
    anthropic_configured: boolean;
  };
  telegram: {
    bot_token_configured: boolean;
    allowed_chat_id: number;
    allowed_chat_id_configured: boolean;
  };
  pdf_engine: {
    active_engine: string;
    tectonic_installed: boolean;
    reportlab_installed: boolean;
  };
  browser_stealth: {
    default_engine: string;
    patchright_installed: boolean;
    camoufox_installed: boolean;
    profiles: PortalProfileStatus[];
  };
  profile_vault: {
    is_placeholder_contact: boolean;
    candidate_name: string;
    email: string;
  };
  gmail_imap_configured: boolean;
  frontend_built: boolean;
  circadian_window_open: boolean;
}

export interface HitlThread {
  thread_id: string;
  job_id: string;
  portal: string;
  apply_url: string;
  company_name: string;
  role_title: string;
  tailored_pdf_path: string;
  is_dream_company: boolean;
  extracted_fields: FormFieldSpec[];
  low_confidence_fields: FormFieldSpec[];
  security_blocker_detected: boolean;
  final_status: string;
  screenshot_url: string | null;
  blocker_reason?: string | null;
  updated_at?: string;
}

export interface CandidateProject {
  title: string;
  tech_stack: string;
  bullets: string[];
}

export interface CandidateProfile {
  demographics: {
    full_name: string;
    email: string;
    phone: string;
    current_city: string;
    preferred_locations: string[];
    willing_to_relocate: string;
    notice_period_days: string;
    degree: string;
    university: string;
    graduation_year: string;
    work_authorization_india: string;
    requires_visa_sponsorship_india: string;
    linkedin_url?: string;
    github_url?: string;
    portfolio_url?: string;
    current_ctc_lpa?: string;
    expected_ctc_lpa?: string;
  };
  skills_years: Record<string, string>;
  projects: CandidateProject[];
  dream_companies: string[];
  search_preferences: {
    target_roles?: string[];
    search_terms: string[];
    locations: string[];
    work_mode_preference?: WorkModePreference | string;
    min_salary_lpa?: number;
    target_salary_lpa?: number;
    experience_years_range?: string;
    must_have_keywords?: string[];
    excluded_keywords?: string[];
    latest_blueprint?: ProfileBlueprint | null;
    ats_boards: {
      greenhouse: string[];
      lever: string[];
      ashby: string[];
      smartrecruiters?: string[];
    };
  };
}

export interface QaCacheEntry {
  id: string;
  question_normalized: string;
  field_type: string;
  canonical_answer: string;
  portal_context: string;
  source: string;
  usage_count: number;
  updated_at?: string;
}
