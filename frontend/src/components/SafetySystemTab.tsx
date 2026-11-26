import { useState } from 'react';
import { CheckCircle2, Globe, RotateCcw, Shield, ShieldOff, XCircle } from 'lucide-react';
import { api } from '../api/client';
import type { DashboardOverview, QaCacheEntry, SystemReadiness } from '../types';
import { QaMemoryTab } from './QaMemoryTab';

interface SafetySystemTabProps {
  readiness: SystemReadiness | null;
  overview: DashboardOverview | null;
  qaEntries: QaCacheEntry[];
  onRefresh: () => Promise<void>;
  onToast: (msg: string, isError?: boolean) => void;
}

function StatusPill({ ok, labelOk, labelFail }: { ok: boolean; labelOk: string; labelFail: string }) {
  return (
    <span
      className={`inline-flex items-center gap-1 rounded-full px-2.5 py-0.5 text-xs font-semibold ${
        ok
          ? 'bg-emerald-500/15 text-emerald-700 dark:text-emerald-300'
          : 'bg-amber-500/15 text-amber-800 dark:text-amber-300'
      }`}
    >
      {ok ? (
        <CheckCircle2 className="h-3.5 w-3.5" aria-hidden="true" />
      ) : (
        <XCircle className="h-3.5 w-3.5" aria-hidden="true" />
      )}
      {ok ? labelOk : labelFail}
    </span>
  );
}

export function SafetySystemTab({
  readiness,
  overview,
  qaEntries,
  onRefresh,
  onToast,
}: SafetySystemTabProps) {
  const [tripPortal, setTripPortal] = useState('naukri');
  const [tripReason, setTripReason] = useState('Manual pause before session check');
  const [warmingPortal, setWarmingPortal] = useState<string | null>(null);
  const [clearingPortal, setClearingPortal] = useState<string | null>(null);

  const handleTrip = async () => {
    try {
      await api.tripBreaker(tripPortal, tripReason);
      onToast(`Tripped circuit breaker for ${tripPortal}.`);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to trip breaker', true);
    }
  };

  const handleReset = async (portal: string) => {
    try {
      await api.resetBreaker(portal);
      onToast(`Reset circuit breaker for ${portal}.`);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : 'Failed to reset breaker', true);
    }
  };

  const handleWarmup = async (portal: string) => {
    setWarmingPortal(portal);
    try {
      const res = await api.warmupBrowserProfile(portal);
      onToast(
        `Opened stealth browser for ${portal} (${res.login_url}). Sign in manually, then close the window to save cookies.`,
      );
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : `Failed to launch warmup for ${portal}`, true);
    } finally {
      setWarmingPortal(null);
    }
  };

  const handleUnlink = async (portal: string) => {
    setClearingPortal(portal);
    try {
      await api.clearBrowserProfile(portal);
      onToast(`Cleared saved login cookies for ${portal}.`);
      await onRefresh();
    } catch (err) {
      onToast(err instanceof Error ? err.message : `Failed to unlink ${portal}`, true);
    } finally {
      setClearingPortal(null);
    }
  };

  const trippedEntries = Object.entries(overview?.tripped_portals ?? {});

  return (
    <div className="space-y-8">
      <section className="card-surface rounded-2xl border-blue-500/30 bg-blue-500/5 p-5">
        <div className="flex items-start gap-3">
          <Shield className="mt-0.5 h-5 w-5 shrink-0 text-blue-500" aria-hidden="true" />
          <div className="space-y-1 text-xs">
            <h2 className="text-sm font-semibold">Dual-Plane Chromium Cookie Strategy (Anti-Ban Architecture)</h2>
            <p className="text-muted leading-relaxed">
              <strong>1. Discovery &amp; Scraping (Disposable Guest Cookies):</strong> Every search URL opens an isolated,
              cookie-empty Chromium context and wipes cookies on close. If Naukri/LinkedIn ever shows a gate, only the
              disposable guest tab closes — your real accounts are never touched.
            </p>
            <p className="text-muted leading-relaxed">
              <strong>2. Applying (Persistent Stable Profile Cookies):</strong> Never rotate cookies on logged-in accounts.
              Click <em>Open Login Browser</em> below once per portal so your persistent home-ISP profile keeps a steady
              device fingerprint, Cubic Bezier mouse physics, and strict daily caps.
            </p>
          </div>
        </div>
      </section>

      {readiness && (
        <section className="grid grid-cols-1 gap-4 md:grid-cols-2 lg:grid-cols-3">
          <div className="card-surface space-y-2 rounded-2xl p-5">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Supabase + pgvector</h3>
              <StatusPill ok={readiness.supabase.schema_ready} labelOk="Live Connected" labelFail="Local Fallback" />
            </div>
            <p className="text-muted text-xs">{readiness.supabase.detail}</p>
          </div>

          <div className="card-surface space-y-2 rounded-2xl p-5">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Gemini 2.5 Flash</h3>
              <StatusPill ok={readiness.llm.gemini_configured} labelOk="Connected" labelFail="Key Missing" />
            </div>
            <p className="text-muted text-xs">
              Powers Agent 0 Blueprint, JD Scoring, Agent 2 Round-2 Verifier &amp; Form Answering.
            </p>
          </div>

          <div className="card-surface space-y-2 rounded-2xl p-5">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-semibold">Telegram HITL Bot</h3>
              <StatusPill
                ok={readiness.telegram.bot_token_configured && readiness.telegram.allowed_chat_id_configured}
                labelOk={`Paired (${readiness.telegram.allowed_chat_id})`}
                labelFail="Chat ID Needed"
              />
            </div>
            <p className="text-muted text-xs">Dispatches 1-tap approval cards for Dream Companies &amp; OTP walls.</p>
          </div>
        </section>
      )}

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-2">
        <section className="card-surface rounded-2xl p-6">
          <h3 className="mb-1 text-sm font-semibold">Connect Portal Accounts (Persistent Browser Vault)</h3>
          <p className="text-muted mb-4 text-xs">
            Sign in once to LinkedIn &amp; Naukri here; Direct ATS boards (Greenhouse/Lever/Ashby) need zero login.
          </p>
          <div className="space-y-2.5">
            {(readiness?.browser_stealth.profiles ?? []).map((prof) => (
              <div
                key={prof.portal}
                className="muted-surface flex flex-wrap items-center justify-between gap-2 rounded-xl px-3.5 py-2.5 text-xs"
              >
                <div className="min-w-0 flex-1">
                  <p className="font-semibold uppercase">{prof.portal}</p>
                  <p className="text-muted truncate">{prof.path}</p>
                </div>
                <div className="flex items-center gap-2">
                  <StatusPill
                    ok={prof.session_initialized}
                    labelOk={`Warmed (${prof.entry_count} items)`}
                    labelFail="Not Logged In"
                  />
                  <button
                    type="button"
                    disabled={warmingPortal === prof.portal}
                    onClick={() => handleWarmup(prof.portal)}
                    className="inline-flex items-center gap-1 rounded-lg bg-blue-600 px-2.5 py-1.5 text-xs font-semibold text-white hover:bg-blue-700 disabled:opacity-60"
                  >
                    <Globe className="h-3.5 w-3.5" aria-hidden="true" />
                    {warmingPortal === prof.portal ? 'Opening...' : 'Open Login Browser'}
                  </button>
                  {prof.session_initialized && (
                    <button
                      type="button"
                      disabled={clearingPortal === prof.portal}
                      onClick={() => handleUnlink(prof.portal)}
                      className="rounded-lg border border-red-500/40 bg-red-500/10 px-2.5 py-1.5 text-xs font-semibold text-red-700 hover:bg-red-500/20 disabled:opacity-60 dark:text-red-300"
                    >
                      {clearingPortal === prof.portal ? 'Clearing...' : 'Unlink Cookies'}
                    </button>
                  )}
                </div>
              </div>
            ))}
          </div>
        </section>

        <section className="card-surface rounded-2xl p-6">
          <h3 className="mb-4 text-sm font-semibold">Emergency Portal Kill Switch (Circuit Breaker)</h3>
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-2">
            <div>
              <label htmlFor="trip-portal" className="mb-1 block text-xs font-semibold">
                Target Portal
              </label>
              <select
                id="trip-portal"
                value={tripPortal}
                onChange={(e) => setTripPortal(e.target.value)}
                className="field-input"
              >
                <option value="naukri">naukri</option>
                <option value="linkedin_easy">linkedin_easy</option>
                <option value="greenhouse">greenhouse</option>
                <option value="lever">lever</option>
                <option value="ashby">ashby</option>
                <option value="smartrecruiters">smartrecruiters</option>
              </select>
            </div>
            <div>
              <label htmlFor="trip-reason" className="mb-1 block text-xs font-semibold">
                Reason
              </label>
              <input
                id="trip-reason"
                type="text"
                value={tripReason}
                onChange={(e) => setTripReason(e.target.value)}
                className="field-input"
              />
            </div>
          </div>

          <div className="mt-4 flex flex-wrap gap-2">
            <button
              type="button"
              onClick={handleTrip}
              className="inline-flex min-h-[40px] items-center gap-1.5 rounded-xl bg-red-600 px-4 text-xs font-semibold text-white hover:bg-red-700"
            >
              <ShieldOff className="h-4 w-4" aria-hidden="true" />
              Trip Breaker
            </button>
            <button
              type="button"
              onClick={() => handleReset('all')}
              className="muted-surface inline-flex min-h-[40px] items-center gap-1.5 rounded-xl px-4 text-xs font-semibold"
            >
              <RotateCcw className="h-4 w-4" aria-hidden="true" />
              Reset All Breakers
            </button>
          </div>

          <div className="mt-4 space-y-2">
            {trippedEntries.length === 0 ? (
              <p className="text-muted text-xs">All portals are currently healthy.</p>
            ) : (
              trippedEntries.map(([portal, reason]) => (
                <div
                  key={portal}
                  className="flex flex-wrap items-center justify-between gap-2 rounded-xl border border-red-500/40 bg-red-500/10 px-3.5 py-2.5 text-xs"
                >
                  <div>
                    <strong className="uppercase">{portal}</strong>: {reason}
                  </div>
                  <button
                    type="button"
                    onClick={() => handleReset(portal)}
                    className="rounded-lg bg-red-600 px-2.5 py-1 text-xs font-semibold text-white"
                  >
                    Reset
                  </button>
                </div>
              ))
            )}
          </div>
        </section>
      </div>

      <QaMemoryTab entries={qaEntries} onRefresh={onRefresh} onToast={onToast} />
    </div>
  );
}
