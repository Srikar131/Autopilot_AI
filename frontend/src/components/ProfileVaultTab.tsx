import { useEffect, useState, type FormEvent } from 'react';
import {
  Building2,
  Download,
  Eye,
  Plus,
  Rocket,
  Save,
  Sparkles,
  Trash2,
} from 'lucide-react';
import { api } from '../api/client';
import type {
  CandidateProfile,
  CompanyRegistrySummary,
  NavTab,
  ProfileBlueprint,
  SearchStatus,
  WorkModePreference,
} from '../types';
import { AppDialog } from './AppDialog';

interface ProfileVaultTabProps {
  searchStatus?: SearchStatus | null;
  onRefresh: () => Promise<void>;
  onNavigate: (tab: NavTab) => void;
  onToast: (msg: string, isError?: boolean) => void;
}

export function ProfileVaultTab({ searchStatus, onRefresh, onNavigate, onToast }: ProfileVaultTabProps) {
  const [profile, setProfile] = useState<CandidateProfile | null>(null);
  const [blueprint, setBlueprint] = useState<ProfileBlueprint | null>(null);
  const [registry, setRegistry] = useState<CompanyRegistrySummary | null>(null);
  const [isPlaceholder, setIsPlaceholder] = useState(false);
  const [saving, setSaving] = useState(false);
  const [searching, setSearching] = useState(false);
  const [refreshingBp, setRefreshingBp] = useState(false);

  const [newSkillName, setNewSkillName] = useState('');
  const [newSkillYears, setNewSkillYears] = useState('1');
  const [newBoardUrl, setNewBoardUrl] = useState('');
  const [previewOpen, setPreviewOpen] = useState(false);
  const [previewPdfUrl, setPreviewPdfUrl] = useState('');
  const [previewTex, setPreviewTex] = useState('');

  useEffect(() => {
    Promise.all([api.getProfile(), api.getProfileBlueprint(), api.getCompanyRegistry()])
      .then(([profRes, bpRes, regRes]) => {
        setProfile(profRes.profile);
        setIsPlaceholder(profRes.is_placeholder_contact);
        setBlueprint(bpRes.blueprint);
        setRegistry(regRes);
      })
      .catch((err) => onToast(err instanceof Error ? err.message : 'Failed to load profile vault', true));
  }, [onToast]);

  if (!profile) {
    return <div className="card-surface rounded-2xl p-6 text-sm">Loading Profile &amp; Blueprint...</div>;
  }

  const updateDemo = (key: keyof CandidateProfile['demographics'], value: string | string[]) => {
    setProfile({
      ...profile,
      demographics: { ...profile.demographics, [key]: value },
    });
  };

  const updatePrefs = (patch: Partial<CandidateProfile['search_preferences']>) => {
    setProfile({
      ...profile,
      search_preferences: { ...profile.search_preferences, ...patch },
    });
  };

  const getSyncedProfile = (prof: CandidateProfile): CandidateProfile => {
    const syncedLocations = prof.demographics.preferred_locations?.length
      ? prof.demographics.preferred_locations
      : prof.search_preferences.locations;
    return {
      ...prof,
      search_preferences: { ...prof.search_preferences, locations: syncedLocations },
    };
  };

  const handleSave = async (e: FormEvent<HTMLFormElement>) => {
    e.preventDefault();
    if (!e.currentTarget.checkValidity()) {
      e.currentTarget.reportValidity();
      return;
    }
    setSaving(true);
    try {
      const res = await api.updateProfile(getSyncedProfile(profile));
      setProfile(res.profile);
      setIsPlaceholder(res.is_placeholder_contact);
      const bpRes = await api.getProfileBlueprint(true);
      setBlueprint(bpRes.blueprint);
      onToast('Profile saved and Search Blueprint refreshed.');
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to save Profile Vault', true);
    } finally {
      setSaving(false);
    }
  };

  const handleStartAutonomousSearch = async () => {
    setSearching(true);
    try {
      // Persist synced edits before launching non-blocking Agent 0 + Agent 1
      const savedRes = await api.updateProfile(getSyncedProfile(profile));
      setProfile(savedRes.profile);
      setIsPlaceholder(savedRes.is_placeholder_contact);
      const res = await api.startAutonomousSearch({
        results_wanted: 12,
        use_ephemeral_chromium: true,
        sweep_company_registry: true,
        run_ats_dorks: true,
        background: true,
      });
      setBlueprint(res.blueprint);
      onToast('Search launched! Streaming live matches into Step 2...');
      await onRefresh();
      onNavigate('jobs');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Autonomous search failed', true);
    } finally {
      setSearching(false);
    }
  };

  const handleRefreshBlueprint = async () => {
    setRefreshingBp(true);
    try {
      await api.updateProfile(getSyncedProfile(profile));
      const res = await api.getProfileBlueprint(true);
      setBlueprint(res.blueprint);
      onToast('AI Profile Summary & Search Blueprint updated.');
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Blueprint refresh failed', true);
    } finally {
      setRefreshingBp(false);
    }
  };

  const handlePreviewResume = async () => {
    try {
      const res = await api.previewProfileResume();
      setPreviewPdfUrl(`${res.pdf_url}?t=${Date.now()}`);
      setPreviewTex(res.tex_content);
      setPreviewOpen(true);
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Resume preview failed', true);
    }
  };

  const handleRegisterBoardUrl = async () => {
    if (!newBoardUrl.trim()) return;
    try {
      const res = await api.addCompanyRegistry({ career_url: newBoardUrl.trim() });
      setRegistry(res.summary);
      setNewBoardUrl('');
      onToast(`Registered ${res.slug} (${res.ats}) in Master Company Registry.`);
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Could not parse ATS URL', true);
    }
  };

  const addSkill = () => {
    if (!newSkillName.trim()) return;
    setProfile({
      ...profile,
      skills_years: { ...profile.skills_years, [newSkillName.trim()]: newSkillYears.trim() || '1' },
    });
    setNewSkillName('');
    setNewSkillYears('1');
  };

  const removeSkill = (skill: string) => {
    const next = { ...profile.skills_years };
    delete next[skill];
    setProfile({ ...profile, skills_years: next });
  };

  const targetRoles = profile.search_preferences.target_roles ?? profile.search_preferences.search_terms;
  const preferredLocs = profile.demographics.preferred_locations ?? [];
  const isSearchActive = searching || Boolean(searchStatus?.running);

  return (
    <div className="space-y-6">
      {/* Agent 0 Executive Summary + Start Autonomous Search Hero */}
      <section className="card-surface rounded-2xl border-blue-500/30 bg-gradient-to-br from-blue-600/10 via-transparent to-sky-500/10 p-6">
        <div className="flex flex-wrap items-start justify-between gap-4">
          <div className="max-w-3xl space-y-2">
            <div className="inline-flex items-center gap-1.5 rounded-full bg-blue-600/15 px-3 py-1 text-xs font-semibold text-blue-700 dark:text-sky-300">
              <Sparkles className="h-3.5 w-3.5" aria-hidden="true" />
              Agent 0: AI Profile Summary &amp; Search Blueprint
            </div>
            <h2 className="text-lg font-bold tracking-tight">
              {blueprint?.persona_headline || 'Save your profile & requirements to generate your search blueprint'}
            </h2>
            <p className="text-muted text-sm leading-relaxed">
              {blueprint?.executive_summary ||
                'Agent 0 reads your profile, work-mode preference, salary floor, and locations before crawling.'}
            </p>
          </div>

          <div className="flex flex-wrap items-center gap-2.5">
            <button
              type="button"
              onClick={handleRefreshBlueprint}
              disabled={refreshingBp}
              className="muted-surface inline-flex min-h-[44px] items-center gap-2 rounded-xl px-4 text-xs font-semibold hover:opacity-90 disabled:opacity-60"
            >
              <Sparkles className="h-4 w-4 text-blue-500" aria-hidden="true" />
              {refreshingBp ? 'Analyzing...' : 'Refresh Summary'}
            </button>

            <button
              type="button"
              onClick={handleStartAutonomousSearch}
              disabled={isSearchActive}
              className="inline-flex min-h-[44px] items-center gap-2 rounded-xl bg-blue-600 px-5 py-2.5 text-sm font-semibold text-white shadow-sm hover:bg-blue-700 disabled:opacity-60"
            >
              <Rocket className="h-4 w-4" aria-hidden="true" />
              {isSearchActive ? 'Crawling Jobs Across Web...' : 'Start Autonomous Search'}
            </button>
          </div>
        </div>
      </section>

      {isPlaceholder && (
        <div className="rounded-2xl border border-amber-500/50 bg-amber-500/10 px-4 py-3 text-xs font-medium">
          Placeholder contact detected (<code>{profile.demographics.email}</code> /{' '}
          <code>{profile.demographics.phone}</code>). Update with your real details below before live applications.
        </div>
      )}

      <form onSubmit={handleSave} className="space-y-6">
        {/* Job Search Requirements (WFH, Salary, Locations, Experience) */}
        <section className="card-surface rounded-2xl p-6">
          <div className="mb-4 flex flex-wrap items-center justify-between gap-2">
            <div>
              <h3 className="text-sm font-semibold">Job Requirements &amp; Filters</h3>
              <p className="text-muted text-xs">
                Agent 0 uses these exact requirements to craft zero-auth Naukri, LinkedIn, JobSpy, and ATS queries.
              </p>
            </div>
            <div className="flex flex-wrap items-center gap-2">
              <button
                type="button"
                onClick={handlePreviewResume}
                className="muted-surface inline-flex min-h-[38px] items-center gap-1.5 rounded-xl px-3.5 text-xs font-semibold hover:opacity-90"
              >
                <Eye className="h-4 w-4 text-blue-500" aria-hidden="true" />
                Preview Resume
              </button>
              <a
                href="/api/v1/profile/preview-resume/pdf?download=true"
                download
                className="muted-surface inline-flex min-h-[38px] items-center gap-1.5 rounded-xl px-3.5 text-xs font-semibold hover:opacity-90"
              >
                <Download className="h-4 w-4 text-emerald-500" aria-hidden="true" />
                Download PDF
              </a>
            </div>
          </div>

          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-4">
            <div>
              <label htmlFor="req-workmode" className="mb-1 block text-xs font-semibold">
                Work Mode Preference
              </label>
              <select
                id="req-workmode"
                value={profile.search_preferences.work_mode_preference || 'any'}
                onChange={(e) => updatePrefs({ work_mode_preference: e.target.value as WorkModePreference })}
                className="field-input"
              >
                <option value="any">Any (Remote / Hybrid / Onsite)</option>
                <option value="remote_wfh">Remote / Work From Home (WFH)</option>
                <option value="hybrid">Hybrid</option>
                <option value="onsite">Onsite Only</option>
              </select>
            </div>

            <div>
              <label htmlFor="req-min-sal" className="mb-1 block text-xs font-semibold">
                Minimum Salary (LPA)
              </label>
              <input
                id="req-min-sal"
                type="number"
                min={0}
                step={1}
                value={profile.search_preferences.min_salary_lpa ?? 0}
                onChange={(e) => updatePrefs({ min_salary_lpa: Number(e.target.value) || 0 })}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="req-target-sal" className="mb-1 block text-xs font-semibold">
                Target Salary (LPA)
              </label>
              <input
                id="req-target-sal"
                type="number"
                min={0}
                step={1}
                value={profile.search_preferences.target_salary_lpa ?? 18}
                onChange={(e) => updatePrefs({ target_salary_lpa: Number(e.target.value) || 0 })}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="req-exp" className="mb-1 block text-xs font-semibold">
                Experience Range (Years)
              </label>
              <input
                id="req-exp"
                type="text"
                placeholder="e.g. 0-3"
                value={profile.search_preferences.experience_years_range || '0-3'}
                onChange={(e) => updatePrefs({ experience_years_range: e.target.value })}
                className="field-input"
              />
            </div>
          </div>

          <div className="mt-4 grid grid-cols-1 gap-4 md:grid-cols-2">
            <div>
              <label htmlFor="target-roles" className="mb-1 block text-xs font-semibold">
                Target Roles (comma-separated)
              </label>
              <input
                id="target-roles"
                type="text"
                value={targetRoles.join(', ')}
                onChange={(e) => {
                  const list = e.target.value
                    .split(',')
                    .map((x) => x.trim())
                    .filter(Boolean);
                  updatePrefs({ target_roles: list, search_terms: list });
                }}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="pref-locations" className="mb-1 block text-xs font-semibold">
                Preferred Locations (comma-separated)
              </label>
              <input
                id="pref-locations"
                type="text"
                value={preferredLocs.join(', ')}
                onChange={(e) => {
                  const list = e.target.value
                    .split(',')
                    .map((x) => x.trim())
                    .filter(Boolean);
                  updateDemo('preferred_locations', list);
                  updatePrefs({ locations: list });
                }}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="must-have-kw" className="mb-1 block text-xs font-semibold">
                Must-Have Keywords (optional, comma-separated)
              </label>
              <input
                id="must-have-kw"
                type="text"
                placeholder="e.g. python, llm"
                value={(profile.search_preferences.must_have_keywords || []).join(', ')}
                onChange={(e) =>
                  updatePrefs({
                    must_have_keywords: e.target.value
                      .split(',')
                      .map((x) => x.trim())
                      .filter(Boolean),
                  })
                }
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="excluded-kw" className="mb-1 block text-xs font-semibold">
                Dealbreakers / Excluded Keywords (comma-separated)
              </label>
              <input
                id="excluded-kw"
                type="text"
                placeholder="e.g. unpaid, sales, night shift"
                value={(profile.search_preferences.excluded_keywords || []).join(', ')}
                onChange={(e) =>
                  updatePrefs({
                    excluded_keywords: e.target.value
                      .split(',')
                      .map((x) => x.trim())
                      .filter(Boolean),
                  })
                }
                className="field-input"
              />
            </div>
          </div>
        </section>

        {/* Candidate Contact & Education */}
        <section className="card-surface rounded-2xl p-6">
          <h3 className="mb-4 text-sm font-semibold">Candidate Details, Links &amp; Compensation</h3>
          <div className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
            <div>
              <label htmlFor="prof-name" className="mb-1 block text-xs font-semibold">
                Full Name *
              </label>
              <input
                id="prof-name"
                type="text"
                required
                autoComplete="name"
                value={profile.demographics.full_name}
                onChange={(e) => updateDemo('full_name', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-email" className="mb-1 block text-xs font-semibold">
                Email Address *
              </label>
              <input
                id="prof-email"
                type="email"
                required
                autoComplete="email"
                value={profile.demographics.email}
                onChange={(e) => updateDemo('email', e.target.value)}
                data-error="prof-email-err"
                className="field-input"
              />
              <div id="prof-email-err" className="field-error-msg">
                Enter a valid email address.
              </div>
            </div>

            <div>
              <label htmlFor="prof-phone" className="mb-1 block text-xs font-semibold">
                Phone Number *
              </label>
              <input
                id="prof-phone"
                type="tel"
                required
                autoComplete="tel"
                value={profile.demographics.phone}
                onChange={(e) => updateDemo('phone', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-city" className="mb-1 block text-xs font-semibold">
                Current City
              </label>
              <input
                id="prof-city"
                type="text"
                value={profile.demographics.current_city}
                onChange={(e) => updateDemo('current_city', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-notice" className="mb-1 block text-xs font-semibold">
                Notice Period
              </label>
              <input
                id="prof-notice"
                type="text"
                value={profile.demographics.notice_period_days}
                onChange={(e) => updateDemo('notice_period_days', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-relocate" className="mb-1 block text-xs font-semibold">
                Willing to Relocate
              </label>
              <select
                id="prof-relocate"
                value={profile.demographics.willing_to_relocate}
                onChange={(e) => updateDemo('willing_to_relocate', e.target.value)}
                className="field-input"
              >
                <option value="Yes">Yes</option>
                <option value="No">No</option>
              </select>
            </div>

            <div>
              <label htmlFor="prof-degree" className="mb-1 block text-xs font-semibold">
                Degree
              </label>
              <input
                id="prof-degree"
                type="text"
                value={profile.demographics.degree}
                onChange={(e) => updateDemo('degree', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-univ" className="mb-1 block text-xs font-semibold">
                University / Institution
              </label>
              <input
                id="prof-univ"
                type="text"
                value={profile.demographics.university}
                onChange={(e) => updateDemo('university', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-grad" className="mb-1 block text-xs font-semibold">
                Graduation Year
              </label>
              <input
                id="prof-grad"
                type="text"
                value={profile.demographics.graduation_year}
                onChange={(e) => updateDemo('graduation_year', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-linkedin" className="mb-1 block text-xs font-semibold">
                LinkedIn URL
              </label>
              <input
                id="prof-linkedin"
                type="url"
                value={profile.demographics.linkedin_url || ''}
                onChange={(e) => updateDemo('linkedin_url', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-github" className="mb-1 block text-xs font-semibold">
                GitHub URL
              </label>
              <input
                id="prof-github"
                type="url"
                value={profile.demographics.github_url || ''}
                onChange={(e) => updateDemo('github_url', e.target.value)}
                className="field-input"
              />
            </div>

            <div>
              <label htmlFor="prof-exp-ctc" className="mb-1 block text-xs font-semibold">
                Expected CTC (LPA)
              </label>
              <input
                id="prof-exp-ctc"
                type="text"
                placeholder="e.g. 18 LPA"
                value={profile.demographics.expected_ctc_lpa || ''}
                onChange={(e) => updateDemo('expected_ctc_lpa', e.target.value)}
                className="field-input"
              />
            </div>
          </div>
        </section>

        {/* Skills & Dream Companies */}
        <section className="card-surface rounded-2xl p-6">
          <h3 className="mb-4 text-sm font-semibold">Skills &amp; Years of Experience</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2 lg:grid-cols-3">
            {Object.entries(profile.skills_years).map(([skill, yrs]) => (
              <div key={skill} className="muted-surface flex items-center justify-between gap-2 rounded-xl p-3">
                <div className="min-w-0 flex-1">
                  <p className="truncate text-xs font-semibold">{skill}</p>
                </div>
                <input
                  aria-label={`Years of experience in ${skill}`}
                  type="text"
                  value={yrs}
                  onChange={(e) =>
                    setProfile({
                      ...profile,
                      skills_years: { ...profile.skills_years, [skill]: e.target.value },
                    })
                  }
                  className="field-input w-20 text-center text-xs"
                />
                <button
                  type="button"
                  onClick={() => removeSkill(skill)}
                  aria-label={`Remove ${skill}`}
                  className="text-muted p-1.5 hover:text-red-500"
                >
                  <Trash2 className="h-4 w-4" aria-hidden="true" />
                </button>
              </div>
            ))}
          </div>

          <div className="mt-4 flex flex-wrap items-end gap-3">
            <div className="min-w-[200px] flex-1">
              <label htmlFor="new-skill" className="mb-1 block text-xs font-semibold">
                Add Skill
              </label>
              <input
                id="new-skill"
                type="text"
                placeholder="e.g. Kubernetes"
                value={newSkillName}
                onChange={(e) => setNewSkillName(e.target.value)}
                className="field-input"
              />
            </div>
            <div className="w-28">
              <label htmlFor="new-skill-yrs" className="mb-1 block text-xs font-semibold">
                Years
              </label>
              <input
                id="new-skill-yrs"
                type="text"
                value={newSkillYears}
                onChange={(e) => setNewSkillYears(e.target.value)}
                className="field-input"
              />
            </div>
            <button
              type="button"
              onClick={addSkill}
              className="muted-surface inline-flex min-h-[44px] items-center gap-1.5 rounded-xl px-4 text-xs font-semibold"
            >
              <Plus className="h-4 w-4" aria-hidden="true" />
              Add Skill
            </button>
          </div>

          <div className="mt-5">
            <label htmlFor="dream-list" className="mb-1 block text-xs font-semibold">
              Dream Companies (comma-separated — always asks your approval before submitting)
            </label>
            <input
              id="dream-list"
              type="text"
              value={profile.dream_companies.join(', ')}
              onChange={(e) =>
                setProfile({
                  ...profile,
                  dream_companies: e.target.value
                    .split(',')
                    .map((x) => x.trim().toLowerCase())
                    .filter(Boolean),
                })
              }
              className="field-input"
            />
          </div>
        </section>

        <div className="flex flex-wrap justify-end gap-3">
          <button
            type="submit"
            disabled={saving}
            className="muted-surface inline-flex min-h-[44px] items-center gap-2 rounded-xl px-6 py-2.5 text-sm font-semibold hover:opacity-90 disabled:opacity-60"
          >
            <Save className="h-4 w-4" aria-hidden="true" />
            {saving ? 'Saving...' : 'Save Profile'}
          </button>
          <button
            type="button"
            disabled={isSearchActive}
            onClick={handleStartAutonomousSearch}
            className="inline-flex min-h-[44px] items-center gap-2 rounded-xl bg-blue-600 px-6 py-2.5 text-sm font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
          >
            <Rocket className="h-4 w-4" aria-hidden="true" />
            {isSearchActive ? 'Searching...' : 'Save & Start Autonomous Search'}
          </button>
        </div>
      </form>

      {registry && (
        <details className="card-surface rounded-2xl p-5">
          <summary className="flex cursor-pointer list-none items-center justify-between gap-2 text-sm font-semibold">
            <span className="flex items-center gap-2">
              <Building2 className="h-4 w-4 text-blue-500" aria-hidden="true" />
              Tracked Global ATS Registry ({registry.total_companies} Company Boards)
            </span>
            <span className="text-muted text-xs font-medium">Expand to view or add custom career URLs</span>
          </summary>

          <div className="mt-4 space-y-4 border-t border-[var(--border-subtle)] pt-4">
            <p className="text-muted text-xs">
              Pre-loaded boards plus any company found dynamically by JobSpy, Naukri/LinkedIn Guest Chromium, or search dorks.
            </p>
            <div className="grid grid-cols-2 gap-3 sm:grid-cols-4">
              {Object.entries(registry.counts_by_ats).map(([ats, count]) => (
                <div key={ats} className="muted-surface rounded-xl p-3 text-xs">
                  <p className="text-muted font-semibold uppercase">{ats}</p>
                  <p className="mt-1 text-xl font-bold">{count}</p>
                </div>
              ))}
            </div>

            <div className="flex flex-wrap items-end gap-3">
              <div className="min-w-[260px] flex-1">
                <label htmlFor="add-board-url" className="mb-1 block text-xs font-semibold">
                  Add Any Specific Company Career URL (Optional)
                </label>
                <input
                  id="add-board-url"
                  type="url"
                  placeholder="https://boards.greenhouse.io/company or https://jobs.ashbyhq.com/company"
                  value={newBoardUrl}
                  onChange={(e) => setNewBoardUrl(e.target.value)}
                  className="field-input"
                />
              </div>
              <button
                type="button"
                onClick={handleRegisterBoardUrl}
                className="muted-surface inline-flex min-h-[44px] items-center gap-1.5 rounded-xl px-4 text-xs font-semibold"
              >
                <Plus className="h-4 w-4" aria-hidden="true" />
                Add Company Board
              </button>
            </div>
          </div>
        </details>
      )}

      <AppDialog
        open={previewOpen}
        onClose={() => setPreviewOpen(false)}
        title="Profile Vault Live Resume Preview"
        titleId="profile-preview-title"
        maxWidthClass="max-w-5xl"
      >
        <div className="space-y-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <p className="text-muted text-xs">
              Generated directly from your saved Profile Vault details and core skills.
            </p>
            <a
              href="/api/v1/profile/preview-resume/pdf?download=true"
              download
              className="inline-flex min-h-[38px] items-center gap-1.5 rounded-xl bg-blue-600 px-4 text-xs font-semibold text-white hover:bg-blue-700"
            >
              <Download className="h-4 w-4" aria-hidden="true" />
              Download Resume PDF
            </a>
          </div>
          <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
            <iframe
              title="Live Resume PDF Preview"
              src={previewPdfUrl}
              className="h-[62vh] w-full rounded-xl border border-[var(--border-subtle)] bg-white"
            />
            <pre className="app-scroll h-[62vh] overflow-auto rounded-xl border border-[var(--border-subtle)] bg-slate-950 p-4 text-xs text-slate-100">
              <code>{previewTex}</code>
            </pre>
          </div>
        </div>
      </AppDialog>
    </div>
  );
}
