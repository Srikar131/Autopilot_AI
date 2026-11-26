import { useCallback, useEffect, useRef, useState } from 'react';
import { api } from './api/client';
import { AppliedTodayTab } from './components/AppliedTodayTab';
import { HitlDeskTab } from './components/HitlDeskTab';
import { JobsPipelineTab } from './components/JobsPipelineTab';
import { ProfileVaultTab } from './components/ProfileVaultTab';
import { SafetySystemTab } from './components/SafetySystemTab';
import { SidebarNav } from './components/SidebarNav';
import { TopStatusBar } from './components/TopStatusBar';
import type {
  AppliedTodayResponse,
  ColorSchemePreference,
  DashboardOverview,
  JobRecord,
  ManualQueueJob,
  NavTab,
  QaCacheEntry,
  SearchStatus,
  SystemReadiness,
} from './types';

const TAB_TITLES: Record<NavTab, string> = {
  profile: 'Step 1 — Profile Blueprint & Autonomous Search',
  jobs: 'Step 2 — Discover & AI Round-2 Verify',
  hitl: 'Step 3 — Needs Manual Apply & HITL Desk',
  applied: 'Step 4 — Applied Today',
  safety: 'Step 5 — Accounts, Q&A Memory & Anti-Ban',
};

function readInitialColorScheme(): ColorSchemePreference {
  try {
    const stored = localStorage.getItem('color-scheme');
    if (stored === 'light' || stored === 'dark' || stored === 'system') return stored;
  } catch {
    // ignore storage restriction
  }
  return 'light';
}

export function App() {
  const [activeTab, setActiveTab] = useState<NavTab>('profile');
  const [overview, setOverview] = useState<DashboardOverview | null>(null);
  const [readiness, setReadiness] = useState<SystemReadiness | null>(null);
  const [jobs, setJobs] = useState<JobRecord[]>([]);
  const [manualJobs, setManualJobs] = useState<ManualQueueJob[]>([]);
  const [appliedData, setAppliedData] = useState<AppliedTodayResponse | null>(null);
  const [qaEntries, setQaEntries] = useState<QaCacheEntry[]>([]);
  const [searchStatus, setSearchStatus] = useState<SearchStatus | null>(null);
  const [isRefreshing, setIsRefreshing] = useState(false);
  const [toast, setToast] = useState<{ msg: string; isError: boolean } | null>(null);
  const [colorScheme, setColorScheme] = useState<ColorSchemePreference>(readInitialColorScheme);
  const wasRunningRef = useRef(false);

  const showToast = useCallback((msg: string, isError = false) => {
    setToast({ msg, isError });
    setTimeout(() => setToast(null), 5000);
  }, []);

  const refreshAll = useCallback(async () => {
    setIsRefreshing(true);
    try {
      const [ov, rd, jb, mq, ap, qa, st] = await Promise.all([
        api.getOverview(),
        api.getReadiness(),
        api.listJobs(),
        api.getManualQueue(),
        api.getAppliedToday(),
        api.listQaCache(),
        api.getSearchStatus(),
      ]);
      setOverview(ov);
      setReadiness(rd);
      setJobs(jb.jobs);
      setManualJobs(mq.jobs);
      setAppliedData(ap);
      setQaEntries(qa.items);
      setSearchStatus(st);
      wasRunningRef.current = st.running;
    } catch (err) {
      showToast(err instanceof Error ? err.message : 'Failed to sync with AutoApply-AI API', true);
    } finally {
      setIsRefreshing(false);
    }
  }, [showToast]);

  useEffect(() => {
    refreshAll();
  }, [refreshAll]);

  useEffect(() => {
    if (!searchStatus?.running) return;
    wasRunningRef.current = true;
    const timer = window.setInterval(async () => {
      try {
        const [st, jb] = await Promise.all([api.getSearchStatus(), api.listJobs()]);
        setSearchStatus(st);
        setJobs(jb.jobs);
        if (!st.running && wasRunningRef.current) {
          wasRunningRef.current = false;
          await refreshAll();
          showToast(st.message, Boolean(st.error));
        }
      } catch {
        // transient polling error ignored until next interval
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [searchStatus?.running, refreshAll, showToast]);

  const applyColorScheme = useCallback((pref: ColorSchemePreference) => {
    setColorScheme(pref);
    const meta = document.querySelector('meta[name="color-scheme"]');
    if (meta) {
      meta.setAttribute('content', pref === 'system' ? 'light dark' : pref);
    }
    try {
      if (pref === 'light') {
        localStorage.removeItem('color-scheme');
      } else {
        localStorage.setItem('color-scheme', pref);
      }
    } catch {
      // ignore storage restriction
    }
  }, []);

  useEffect(() => {
    const onStorage = (e: StorageEvent) => {
      if (e.key === 'color-scheme') {
        const next: ColorSchemePreference =
          e.newValue === 'light' || e.newValue === 'dark' || e.newValue === 'system'
            ? e.newValue
            : 'light';
        applyColorScheme(next);
      }
    };
    window.addEventListener('storage', onStorage);
    return () => window.removeEventListener('storage', onStorage);
  }, [applyColorScheme]);

  const trippedCount = Object.keys(overview?.tripped_portals ?? {}).length;
  const readyCount = jobs.filter((j) => j.status === 'READY_TO_APPLY').length;

  return (
    <div className="flex min-h-screen flex-col lg:flex-row">
      <SidebarNav
        activeTab={activeTab}
        onSelectTab={setActiveTab}
        readyCount={readyCount}
        manualCount={manualJobs.length}
        appliedTodayCount={appliedData?.today_count ?? 0}
        trippedCount={trippedCount}
      />

      <div className="flex min-w-0 flex-1 flex-col">
        <TopStatusBar
          title={TAB_TITLES[activeTab]}
          circadianOpen={overview?.circadian_window_open ?? true}
          trippedCount={trippedCount}
          isRefreshing={isRefreshing}
          onRefresh={refreshAll}
          colorScheme={colorScheme}
          onChangeColorScheme={applyColorScheme}
        />

        <main className="app-scroll flex-1 p-4 sm:p-6">
          <div className="mx-auto max-w-7xl">
            {activeTab === 'profile' && (
              <ProfileVaultTab
                searchStatus={searchStatus}
                onRefresh={refreshAll}
                onNavigate={setActiveTab}
                onToast={showToast}
              />
            )}
            {activeTab === 'jobs' && (
              <JobsPipelineTab
                jobs={jobs}
                searchStatus={searchStatus}
                onRefresh={refreshAll}
                onNavigate={setActiveTab}
                onToast={showToast}
              />
            )}
            {activeTab === 'hitl' && (
              <HitlDeskTab manualJobs={manualJobs} onRefresh={refreshAll} onToast={showToast} />
            )}
            {activeTab === 'applied' && (
              <AppliedTodayTab appliedData={appliedData} overview={overview} />
            )}
            {activeTab === 'safety' && (
              <SafetySystemTab
                readiness={readiness}
                overview={overview}
                qaEntries={qaEntries}
                onRefresh={refreshAll}
                onToast={showToast}
              />
            )}
          </div>
        </main>
      </div>

      {toast && (
        <div
          role="status"
          aria-live="polite"
          className={`fixed right-5 bottom-5 z-50 max-w-md rounded-xl border px-4 py-3 text-xs font-semibold shadow-lg ${
            toast.isError
              ? 'border-red-500 bg-red-600 text-white'
              : 'border-emerald-500 bg-emerald-600 text-white'
          }`}
        >
          {toast.msg}
        </div>
      )}
    </div>
  );
}

export default App;
