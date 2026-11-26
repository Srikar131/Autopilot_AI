from __future__ import annotations

import asyncio
import importlib.util
import os
import re
import shutil
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional
from zoneinfo import ZoneInfo

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from src.agent.executor import PortalApplicationExecutor
from src.agent.graph import AutoApplyOrchestrator
from src.agent.portals.naukri_apply import NaukriPortalAdapter
from src.agent.state import ApplicationAgentState, FormFieldSpec
from src.brain.embeddings import embed_text, is_configured_secret
from src.brain.profile_planner import generate_profile_search_blueprint
from src.brain.qa_rag import QAResolutionEngine
from src.brain.scorer import deterministic_keyword_score
from src.brain.verifier_agent import verify_and_filter_jobs_conversational
from src.config import settings
from src.db.profile_store import ProfileVaultStore
from src.hitl.bot_listener import TelegramHITLBotListener
from src.hitl.image_annotator import render_hitl_preview_card
from src.ingestion.company_registry import CompanyATSRegistry
from src.ingestion.jd_enrichment import hydrate_job_details, is_role_relevant_to_profile
from src.ingestion.jobspy_worker import normalize_scraped_jobs
from src.pipeline_discovery import (
    DiscoveryPipeline,
    tailor_and_compile_resume,
)
from src.stealth_browser.browser_factory import (
    clear_portal_profile,
    inspect_portal_profiles,
    launch_stealth_context,
)
from src.stealth_browser.circuit_breaker import PortalCircuitBreaker

IST_TZ = ZoneInfo("Asia/Kolkata")
PORTALS_ALL = ["greenhouse", "lever", "ashby", "smartrecruiters", "naukri", "linkedin_easy"]
OUTPUT_RESUMES_DIR = Path("output/resumes")
OUTPUT_SCREENSHOTS_DIR = Path("output/screenshots")

circuit_breaker = PortalCircuitBreaker()
orchestrator = AutoApplyOrchestrator(circuit_breaker=circuit_breaker)
profile_store = ProfileVaultStore()
company_registry = CompanyATSRegistry()
portal_executor = PortalApplicationExecutor(orchestrator=orchestrator)
_background_tasks: set[asyncio.Task[Any]] = set()
_search_status: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "message": "Ready to start autonomous search.",
    "discovered_count": 0,
    "processed_count": 0,
    "total_to_process": 0,
    "ready_to_apply_count": 0,
    "rejected_count": 0,
    "started_at": None,
    "finished_at": None,
    "error": None,
}
_apply_status: dict[str, Any] = {
    "running": False,
    "phase": "idle",
    "message": "Ready to apply to selected listings.",
    "total_jobs": 0,
    "completed_jobs": 0,
    "applied_count": 0,
    "manual_trouble_count": 0,
    "skipped_quota_count": 0,
    "concurrency": 4,
    "started_at": None,
    "finished_at": None,
    "error": None,
}


def re_evaluate_existing_unapplied_jobs(profile: dict[str, Any]) -> int:
    """Re-evaluates unverified READY_TO_APPLY rows against current word-boundary and experience guards."""
    jobs = orchestrator.repo.list_all_jobs(limit=300)
    demoted = 0
    for job in jobs:
        if job.get("status") != "READY_TO_APPLY":
            continue
        existing_reasoning = job.get("match_reasoning") or {}
        if isinstance(existing_reasoning, dict) and existing_reasoning.get("round2_verified"):
            continue

        title = str(job.get("role_title") or "")
        location = str(job.get("location") or "")
        jd_text = str(job.get("jd_raw_text") or "")
        relevant = is_role_relevant_to_profile(title, location, profile)
        score_res = deterministic_keyword_score(jd_text, profile, role_title=title)

        if not relevant or score_res.hard_blocker_detected or score_res.match_score < settings.match_score_threshold:
            updated_reasoning = {
                **(existing_reasoning if isinstance(existing_reasoning, dict) else {}),
                **score_res.to_dict(),
                "stage": "round1_re_evaluated",
            }
            if not relevant and not updated_reasoning.get("blocker_reason"):
                updated_reasoning["blocker_reason"] = "Outside target AI/Python domain or preferred location"
            new_score = min(float(score_res.match_score), 60.0) if not relevant else score_res.match_score
            orchestrator.repo.update_job_status(
                str(job["id"]),
                "SCORED_REJECTED",
                {"match_score": new_score, "match_reasoning": updated_reasoning},
            )
            demoted += 1
    return demoted


def get_portal_daily_limit(portal: str) -> int:
    """Returns configured daily application cap for the given portal."""
    normalized = portal.strip().lower()
    if normalized == "linkedin_easy":
        return settings.max_daily_linkedin
    if normalized == "naukri":
        return settings.max_daily_naukri
    return settings.max_daily_direct_ats


async def run_scheduled_discovery(pipeline: DiscoveryPipeline | None = None) -> dict[str, Any]:
    """Scheduled zero-auth job discovery across JobSpy, ATS registry, open-web dorks, and Gmail."""
    active_profile = profile_store.load()
    prefs = active_profile.get("search_preferences", {})
    boards = prefs.get("ats_boards", {})
    pipe = pipeline or DiscoveryPipeline(
        repo=orchestrator.repo,
        profile=active_profile,
        output_base_dir=OUTPUT_RESUMES_DIR,
        registry=company_registry,
    )
    return await pipe.run(
        search_terms=prefs.get("search_terms"),
        locations=prefs.get("locations"),
        greenhouse_slugs=boards.get("greenhouse"),
        lever_slugs=boards.get("lever"),
        ashby_slugs=boards.get("ashby"),
        smartrecruiters_slugs=boards.get("smartrecruiters"),
        poll_gmail_imap=bool(settings.gmail_address and settings.gmail_app_password),
        sweep_company_registry=True,
        run_ats_dorks=True,
    )


async def run_scheduled_naukri_refresh(page: Any | None = None) -> dict[str, Any]:
    """Runs the 08:55 AM IST Naukri profile headline refresh inside a persistent stealth profile."""
    allowed, reason = circuit_breaker.can_execute("naukri")
    if not allowed:
        return {"status": "BLOCKED_BY_CIRCUIT_BREAKER", "portal": "naukri", "reason": reason}

    adapter = NaukriPortalAdapter()
    if page is not None:
        refreshed = await adapter.refresh_daily_profile_headline(page, circuit_breaker=circuit_breaker)
        return {"status": "REFRESHED" if refreshed else "SKIPPED", "portal": "naukri"}

    try:
        async with launch_stealth_context("naukri", headless=True) as context:
            browser_page = await context.new_page()
            refreshed = await adapter.refresh_daily_profile_headline(
                browser_page, circuit_breaker=circuit_breaker
            )
            return {"status": "REFRESHED" if refreshed else "SKIPPED", "portal": "naukri"}
    except Exception as exc:
        return {"status": "ERROR", "portal": "naukri", "detail": str(exc)}


async def run_application_wave(
    wave_name: str = "morning",
    portals: list[str] | None = None,
    max_per_portal: int = 5,
    now: Any | None = None,
    live_browser: bool = False,
) -> dict[str, Any]:
    """Executes a paced morning/afternoon application wave respecting daily limits and circuit breakers."""
    target_portals = portals or PORTALS_ALL
    wave_summary: dict[str, Any] = {"wave": wave_name, "portals": {}}
    active_profile = profile_store.load()

    for portal in target_portals:
        allowed, reason = circuit_breaker.can_execute(portal, now=now)
        if not allowed:
            wave_summary["portals"][portal] = {
                "status": "BLOCKED_BY_CIRCUIT_BREAKER",
                "reason": reason,
                "processed": 0,
            }
            continue

        daily_limit = get_portal_daily_limit(portal)
        queued_jobs = orchestrator.repo.list_jobs_by_status(
            portal=portal,
            status="READY_TO_APPLY",
            limit=max_per_portal,
        )

        processed = 0
        quota_exhausted = False
        for job in queued_jobs:
            quota_ok, _ = orchestrator.repo.check_and_increment_daily_quota(
                portal, daily_limit, increment=True
            )
            if not quota_ok:
                quota_exhausted = True
                break

            if live_browser:
                await portal_executor.execute_job(
                    job=job,
                    candidate_profile=active_profile,
                    live_browser=True,
                    headless=True,
                    now=now,
                )
            else:
                job_id = str(job["id"])
                state: ApplicationAgentState = {
                    "job_id": job_id,
                    "portal": portal,
                    "apply_url": job["canonical_url"],
                    "company_name": job.get("company_name", "Unknown Company"),
                    "role_title": job.get("role_title", "Software / AI Engineer"),
                    "tailored_pdf_path": job.get("tailored_pdf_path") or "",
                    "is_dream_company": bool(job.get("is_dream_company", False)),
                    "current_step_index": 0,
                    "extracted_fields": [],
                    "low_confidence_fields": [],
                    "security_blocker_detected": False,
                    "human_resolution": None,
                    "final_status": "IN_PROGRESS",
                }
                await orchestrator.evaluate_and_step(
                    thread_id=f"thread-{job_id}",
                    state=state,
                    now=now,
                )
            processed += 1

        wave_summary["portals"][portal] = {
            "status": "QUOTA_EXCEEDED" if quota_exhausted else "WAVE_EXECUTED",
            "processed": processed,
            "daily_limit": daily_limit,
        }

    return wave_summary


def configure_scheduler(sched: AsyncIOScheduler | None = None) -> AsyncIOScheduler:
    """Configures APScheduler jobs for discovery polling, 08:55 IST Naukri refresh, and application waves."""
    target = sched or AsyncIOScheduler(timezone=IST_TZ)

    target.add_job(
        run_scheduled_discovery,
        trigger=IntervalTrigger(minutes=30, timezone=IST_TZ),
        id="discovery_polling",
        replace_existing=True,
    )
    target.add_job(
        run_scheduled_naukri_refresh,
        trigger=CronTrigger(hour=8, minute=55, timezone=IST_TZ),
        id="naukri_morning_refresh",
        replace_existing=True,
    )
    target.add_job(
        run_application_wave,
        trigger=CronTrigger(hour=9, minute=15, timezone=IST_TZ),
        id="morning_application_wave",
        kwargs={"wave_name": "morning"},
        replace_existing=True,
    )
    target.add_job(
        run_application_wave,
        trigger=CronTrigger(hour=15, minute=0, timezone=IST_TZ),
        id="afternoon_application_wave",
        kwargs={"wave_name": "afternoon"},
        replace_existing=True,
    )
    return target


scheduler = configure_scheduler()


@asynccontextmanager
async def lifespan(_app: FastAPI):
    global scheduler
    if not scheduler.get_jobs():
        scheduler = configure_scheduler()
    try:
        if not scheduler.running:
            scheduler.start()
    except RuntimeError:
        pass

    bot_task: asyncio.Task[Any] | None = None
    if "PYTEST_CURRENT_TEST" not in os.environ and is_configured_secret(settings.telegram_bot_token):
        async def _resume_from_bot(
            thread_id: str,
            action: str,
            field_overrides: dict[str, str] | None,
            save_to_qa_cache: bool,
        ) -> dict[str, Any]:
            updated = await orchestrator.resume_from_human(
                thread_id=thread_id,
                action=action,
                field_overrides=field_overrides,
                save_to_qa_cache=save_to_qa_cache,
            )
            return {
                "status": "RESUMED_AND_PROCESSED",
                "job_id": updated["job_id"],
                "final_status": updated["final_status"],
            }

        listener = TelegramHITLBotListener(
            repo=orchestrator.repo,
            resume_callback=_resume_from_bot,
        )
        bot_task = asyncio.create_task(listener.start_polling())

    try:
        yield
    finally:
        if bot_task is not None and not bot_task.done():
            bot_task.cancel()
            try:
                await bot_task
            except BaseException:
                pass
        if scheduler.running:
            scheduler.shutdown(wait=False)
        scheduler = configure_scheduler()


app = FastAPI(title="AutoApply-AI Local Control Plane", version="1.0.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://127.0.0.1:5173",
        "http://localhost:5173",
        "http://127.0.0.1:8420",
        "http://localhost:8420",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


class BatchTriggerRequest(BaseModel):
    portal: str
    max_jobs: int = 10
    supervised_mode: bool = True
    execute_now: bool = False
    live_browser: bool = False


class HITLResumeRequest(BaseModel):
    langgraph_thread_id: str
    action: str = "APPROVE_OR_EDIT"
    field_overrides: Optional[Dict[str, str]] = None
    save_to_qa_cache: bool = True
    live_browser_submit: bool = False


class CircuitBreakerTripRequest(BaseModel):
    portal: str
    reason: str = "Manual or automated anomaly detected"


class CircuitBreakerResetRequest(BaseModel):
    portal: str


class JobIngestRequest(BaseModel):
    canonical_url: str
    company_name: str
    role_title: str
    location: str = "Bengaluru, India"
    jd_raw_text: str
    is_dream_company: Optional[bool] = None


class JobApplyRequest(BaseModel):
    live_browser: bool = False
    headless: bool = False
    force_supervised_hitl: bool = False
    sample_fields: Optional[List[FormFieldSpec]] = None


class DiscoveryRunRequest(BaseModel):
    search_terms: Optional[List[str]] = None
    locations: Optional[List[str]] = None
    email_bodies: Optional[List[str]] = None
    greenhouse_slugs: Optional[List[str]] = None
    lever_slugs: Optional[List[str]] = None
    ashby_slugs: Optional[List[str]] = None
    smartrecruiters_slugs: Optional[List[str]] = None
    poll_gmail_imap: bool = False
    sweep_company_registry: bool = True
    run_ats_dorks: bool = True
    results_wanted: int = 10


class RegistryAddRequest(BaseModel):
    career_url: Optional[str] = None
    ats: Optional[str] = None
    slug: Optional[str] = None


class QACacheCreateRequest(BaseModel):
    question_normalized: str
    field_type: str = "text"
    canonical_answer: str
    portal_context: str = "general"


class QAResolvePreviewRequest(BaseModel):
    label_text: str
    field_type: str = "text"
    options: Optional[List[str]] = None


class StartAutonomousSearchRequest(BaseModel):
    results_wanted: int = 10
    use_ephemeral_chromium: bool = True
    sweep_company_registry: bool = True
    run_ats_dorks: bool = True
    poll_gmail_imap: bool = False
    background: bool = False


class ChatVerifyRequest(BaseModel):
    message: str
    scope: str = "all_discovered"


class ApplyVerifiedBatchRequest(BaseModel):
    job_ids: Optional[List[str]] = None
    live_browser: bool = False
    headless: bool = True
    concurrency: int = 4
    background: bool = False


class ClearJobsRequest(BaseModel):
    keep_applied: bool = True


@app.get("/health")
async def health_check():
    return {
        "status": "healthy",
        "circadian_window_open": circuit_breaker.is_within_circadian_window(),
        "tripped_portals": circuit_breaker.tripped_portals,
        "scheduled_jobs": [job.id for job in scheduler.get_jobs()],
    }


@app.get("/api/v1/system/readiness")
async def get_system_readiness():
    """Returns end-to-end readiness diagnostics for DB, LLM, Telegram, PDF engine, browser stealth, and profile."""
    supabase_health = orchestrator.repo.check_health()
    profile = profile_store.load()
    tectonic_path = shutil.which("tectonic")
    has_reportlab = importlib.util.find_spec("reportlab") is not None
    has_patchright = importlib.util.find_spec("patchright") is not None
    has_camoufox = importlib.util.find_spec("camoufox") is not None

    return {
        "supabase": {
            **supabase_health,
            "url_configured": is_configured_secret(settings.supabase_url),
            "service_key_configured": is_configured_secret(settings.supabase_service_key),
            "database_url_configured": is_configured_secret(settings.database_url),
        },
        "llm": {
            "gemini_configured": is_configured_secret(settings.gemini_api_key),
            "anthropic_configured": is_configured_secret(settings.anthropic_api_key),
        },
        "telegram": {
            "bot_token_configured": is_configured_secret(settings.telegram_bot_token),
            "allowed_chat_id": settings.telegram_allowed_chat_id,
            "allowed_chat_id_configured": bool(
                settings.telegram_allowed_chat_id and settings.telegram_allowed_chat_id != 0
            ),
        },
        "pdf_engine": {
            "active_engine": "tectonic" if tectonic_path else ("reportlab" if has_reportlab else "minimal"),
            "tectonic_installed": bool(tectonic_path),
            "reportlab_installed": has_reportlab,
        },
        "browser_stealth": {
            "default_engine": settings.default_browser_engine,
            "patchright_installed": has_patchright,
            "camoufox_installed": has_camoufox,
            "profiles": inspect_portal_profiles(PORTALS_ALL),
        },
        "profile_vault": {
            "is_placeholder_contact": profile_store.is_placeholder_contact(profile),
            "candidate_name": profile.get("demographics", {}).get("full_name", ""),
            "email": profile.get("demographics", {}).get("email", ""),
        },
        "gmail_imap_configured": bool(
            settings.gmail_address.strip() and settings.gmail_app_password.strip()
        ),
        "frontend_built": Path("frontend/dist/index.html").exists(),
        "circadian_window_open": circuit_breaker.is_within_circadian_window(),
    }


@app.get("/api/v1/dashboard/overview")
async def get_dashboard_overview():
    """Returns funnel KPIs, portal daily quotas, scheduler metadata, and recent events."""
    jobs = orchestrator.repo.list_all_jobs(limit=500)
    status_counts: dict[str, int] = {}
    portal_counts: dict[str, int] = {}
    scores: list[float] = []
    for job in jobs:
        st = str(job.get("status", "DISCOVERED"))
        pt = str(job.get("portal", "other_ats"))
        status_counts[st] = status_counts.get(st, 0) + 1
        portal_counts[pt] = portal_counts.get(pt, 0) + 1
        if job.get("match_score") is not None:
            scores.append(float(job["match_score"]))

    portal_limits = {p: get_portal_daily_limit(p) for p in PORTALS_ALL}
    quotas = orchestrator.repo.get_daily_quotas_summary(portal_limits)
    threads = orchestrator.list_threads()
    paused_threads = [t for t in threads if t.get("final_status") == "PAUSED_HITL"]

    sched_jobs = []
    for j in scheduler.get_jobs():
        sched_jobs.append(
            {
                "id": j.id,
                "next_run_time": j.next_run_time.isoformat() if j.next_run_time else None,
                "trigger": str(j.trigger),
            }
        )

    return {
        "total_jobs": len(jobs),
        "status_counts": status_counts,
        "portal_counts": portal_counts,
        "average_match_score": round(sum(scores) / len(scores), 1) if scores else 0.0,
        "paused_hitl_count": len(paused_threads),
        "daily_quotas": quotas,
        "circadian_window_open": circuit_breaker.is_within_circadian_window(),
        "tripped_portals": circuit_breaker.tripped_portals,
        "scheduler_jobs": sched_jobs,
        "recent_events": orchestrator.repo.list_application_events(limit=20),
    }


@app.get("/api/v1/jobs")
async def list_jobs(
    portal: Optional[str] = Query(default=None),
    status: Optional[str] = Query(default=None),
    search: Optional[str] = Query(default=None),
    dream_only: bool = Query(default=False),
    limit: int = Query(default=100, ge=1, le=500),
):
    jobs = orchestrator.repo.list_all_jobs(
        portal=portal,
        status=status,
        search=search,
        dream_only=dream_only,
        limit=limit,
    )
    return {"count": len(jobs), "jobs": jobs}


@app.get("/api/v1/jobs/applied-today")
async def get_applied_today_jobs():
    today_ist = datetime.now(IST_TZ).date().isoformat()
    all_applied = orchestrator.repo.list_all_jobs(status="APPLIED", limit=500)
    today_jobs: list[dict[str, Any]] = []
    for job in all_applied:
        raw_ts = str(job.get("applied_at") or "")
        if not raw_ts:
            continue
        try:
            dt = datetime.fromisoformat(raw_ts.replace("Z", "+00:00"))
            dt_ist = dt.astimezone(IST_TZ) if dt.tzinfo else dt.replace(tzinfo=IST_TZ)
            if dt_ist.date().isoformat() == today_ist:
                today_jobs.append(job)
        except Exception:
            if raw_ts.startswith(today_ist):
                today_jobs.append(job)

    return {
        "date_ist": today_ist,
        "today_count": len(today_jobs),
        "total_applied_count": len(all_applied),
        "today_jobs": today_jobs,
        "all_applied_jobs": all_applied,
    }


@app.get("/api/v1/jobs/manual-queue")
async def get_manual_apply_queue():
    paused_jobs = orchestrator.repo.list_all_jobs(status="PAUSED_HITL", limit=200)
    failed_jobs = orchestrator.repo.list_all_jobs(status="FAILED_CIRCUIT_BREAKER", limit=100)
    threads_by_job = {str(t.get("job_id")): t for t in orchestrator.list_threads()}

    combined: dict[str, dict[str, Any]] = {}
    for job in [*paused_jobs, *failed_jobs]:
        jid = str(job["id"])
        thread = threads_by_job.get(jid) or {}
        reasoning = job.get("match_reasoning") or {}
        trouble_reason = (
            thread.get("blocker_reason")
            or (reasoning.get("trouble_reason") if isinstance(reasoning, dict) else None)
            or (
                "Dream Company human approval required"
                if job.get("is_dream_company")
                else "Requires human verification or manual submission"
            )
        )
        screenshot_file = OUTPUT_SCREENSHOTS_DIR / f"{jid}_annotated.png"
        combined[jid] = {
            **job,
            "thread_id": thread.get("thread_id") or f"thread-{jid}",
            "trouble_reason": trouble_reason,
            "extracted_fields": thread.get("extracted_fields") or [],
            "low_confidence_fields": thread.get("low_confidence_fields") or [],
            "screenshot_url": (
                f"/api/v1/hitl/screenshots/{jid}"
                if (screenshot_file.exists() or thread.get("final_status") == "PAUSED_HITL")
                else None
            ),
        }

    for jid, thread in threads_by_job.items():
        if thread.get("final_status") == "PAUSED_HITL" and jid not in combined:
            job_rec = orchestrator.repo.get_job(jid) or {
                "id": jid,
                "canonical_url": thread.get("apply_url", ""),
                "portal": thread.get("portal", "other_ats"),
                "company_name": thread.get("company_name", "Unknown Company"),
                "role_title": thread.get("role_title", "Role"),
                "location": "India",
                "is_dream_company": bool(thread.get("is_dream_company", False)),
                "status": "PAUSED_HITL",
                "tailored_pdf_path": thread.get("tailored_pdf_path"),
            }
            combined[jid] = {
                **job_rec,
                "thread_id": thread.get("thread_id"),
                "trouble_reason": thread.get("blocker_reason") or "Requires human verification or manual apply",
                "extracted_fields": thread.get("extracted_fields") or [],
                "low_confidence_fields": thread.get("low_confidence_fields") or [],
                "screenshot_url": f"/api/v1/hitl/screenshots/{jid}",
            }

    items = list(combined.values())
    return {"count": len(items), "jobs": items}


@app.post("/api/v1/jobs/clear")
async def clear_discovered_jobs(req: ClearJobsRequest):
    deleted = await asyncio.to_thread(orchestrator.repo.clear_jobs, req.keep_applied)
    if req.keep_applied:
        orchestrator._checkpoints = {
            k: v
            for k, v in orchestrator._checkpoints.items()
            if v.get("final_status") == "APPLIED"
        }
    else:
        orchestrator._checkpoints.clear()
    _update_search_status(
        {
            "running": False,
            "phase": "idle",
            "discovered_count": 0,
            "processed_count": 0,
            "total_to_process": 0,
            "ready_to_apply_count": 0,
            "rejected_count": 0,
            "error": None,
            "message": f"Cleared {deleted} listings from queue.",
        }
    )
    return {"status": "CLEARED", "deleted_count": deleted, "keep_applied": req.keep_applied}


@app.get("/api/v1/jobs/{job_id}")
async def get_job_detail(job_id: str):
    job = orchestrator.repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    events = orchestrator.repo.list_application_events(job_id=job_id, limit=25)
    return {"job": job, "events": events}


@app.post("/api/v1/jobs/ingest", status_code=201)
async def ingest_single_job(req: JobIngestRequest):
    normalized = normalize_scraped_jobs(
        [
            {
                "job_url": req.canonical_url,
                "company": req.company_name,
                "title": req.role_title,
                "location": req.location,
                "description": req.jd_raw_text,
            }
        ]
    )
    if not normalized:
        raise HTTPException(status_code=400, detail="Valid canonical_url is required.")
    job_payload = normalized[0]
    if req.is_dream_company is not None:
        job_payload["is_dream_company"] = req.is_dream_company

    pipe = DiscoveryPipeline(
        repo=orchestrator.repo,
        profile=profile_store.load(),
        output_base_dir=OUTPUT_RESUMES_DIR,
    )
    upserted = await pipe.process_job(job_payload)
    return {"job": upserted}


@app.post("/api/v1/jobs/seed-sample")
async def seed_sample_jobs():
    """Seeds realistic sample roles across portals so every pipeline & HITL path can be tested."""
    samples = normalize_scraped_jobs(
        [
            {
                "job_url": "https://boards.greenhouse.io/razorpay/jobs/900101",
                "company": "Razorpay",
                "title": "AI Platform Engineer",
                "location": "Bengaluru, India",
                "description": (
                    "Building production GenAI, Python, FastAPI, LangGraph, RAG, Supabase pgvector, "
                    "and Docker microservices for intelligent developer workflows."
                ),
            },
            {
                "job_url": "https://jobs.lever.co/zepto/900202",
                "company": "Zepto",
                "title": "Conversational Voice AI Engineer",
                "location": "Bengaluru, India",
                "description": (
                    "Seeking Voice AI and LLM Agent Engineer fluent in Python, FastAPI, LangGraph, "
                    "RAG, PostgreSQL, and low-latency streaming audio systems."
                ),
            },
            {
                "job_url": "https://jobs.ashbyhq.com/anthropic/900303",
                "company": "Anthropic",
                "title": "Applied AI Systems Engineer",
                "location": "Remote",
                "description": (
                    "Architect reliable multi-agent LLM workflows using Python, FastAPI, Docker, "
                    "PostgreSQL, RAG evaluation harnesses, and safe tool orchestration."
                ),
            },
            {
                "job_url": "https://www.linkedin.com/jobs/view/900404",
                "company": "FinSight Labs",
                "title": "GenAI & RAG Backend Engineer",
                "location": "Hyderabad, India",
                "description": (
                    "Looking for Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, "
                    "Supabase, Postgres, LangGraph, and GCP experience."
                ),
            },
            {
                "job_url": "https://www.naukri.com/job-listings-legacy-architect-900505",
                "company": "Legacy Defense Corp",
                "title": "Principal Security Architect",
                "location": "Remote",
                "description": "Requires 12+ years of experience and US Citizens Only with active clearance.",
            },
        ]
    )
    pipe = DiscoveryPipeline(
        repo=orchestrator.repo,
        profile=profile_store.load(),
        output_base_dir=OUTPUT_RESUMES_DIR,
    )
    summary = await pipe.run(seed_jobs=samples, search_terms=[], locations=[])
    return summary


@app.post("/api/v1/jobs/{job_id}/apply")
async def apply_to_single_job(job_id: str, req: JobApplyRequest):
    job = orchestrator.repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")

    portal = str(job.get("portal", "other_ats"))
    allowed, reason = circuit_breaker.can_execute(portal)
    if not allowed:
        return {"status": "BLOCKED_BY_CIRCUIT_BREAKER", "reason": reason, "job": job}

    daily_limit = get_portal_daily_limit(portal)
    quota_ok, count = orchestrator.repo.check_and_increment_daily_quota(portal, daily_limit, increment=True)
    if not quota_ok:
        return {
            "status": "BLOCKED_BY_DAILY_QUOTA",
            "portal": portal,
            "current_count": count,
            "daily_limit": daily_limit,
        }

    result = await portal_executor.execute_job(
        job=job,
        candidate_profile=profile_store.load(),
        live_browser=req.live_browser,
        headless=req.headless,
        force_supervised_hitl=req.force_supervised_hitl,
        sample_fields=req.sample_fields,
        screenshot_dir=OUTPUT_SCREENSHOTS_DIR,
    )
    updated_job = orchestrator.repo.get_job(job_id) or job
    return {
        "thread_id": f"thread-{job_id}",
        "final_status": result["final_status"],
        "state": result,
        "job": updated_job,
    }


@app.get("/api/v1/jobs/{job_id}/resume/pdf")
async def get_job_resume_pdf(job_id: str, download: bool = Query(default=False)):
    job = orchestrator.repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    pdf_raw = job.get("tailored_pdf_path")
    if not pdf_raw:
        raise HTTPException(status_code=404, detail="No tailored PDF compiled for this job.")
    pdf_path = Path(pdf_raw)
    if not pdf_path.exists():
        raise HTTPException(status_code=404, detail=f"PDF file missing on disk: {pdf_raw}")

    profile = profile_store.load()
    raw_name = str(profile.get("demographics", {}).get("full_name") or "Candidate")
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_name).strip("_") or "Candidate"
    safe_comp = re.sub(r"[^A-Za-z0-9_-]+", "_", str(job.get("company_name") or "Role")).strip("_") or "Role"
    filename = f"{safe_name}_{safe_comp}_Resume.pdf"
    disp = "attachment" if download else "inline"
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disp}; filename="{filename}"'},
    )


@app.get("/api/v1/jobs/{job_id}/resume/tex")
async def get_job_resume_tex(job_id: str):
    job = orchestrator.repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    pdf_raw = job.get("tailored_pdf_path")
    if not pdf_raw:
        raise HTTPException(status_code=404, detail="No tailored LaTeX generated for this job.")
    tex_path = Path(pdf_raw).with_suffix(".tex")
    if not tex_path.exists():
        raise HTTPException(status_code=404, detail=f"TeX file missing on disk: {tex_path}")
    return {
        "job_id": job_id,
        "tex_path": tex_path.as_posix(),
        "tex_content": tex_path.read_text(encoding="utf-8"),
    }


@app.get("/api/v1/discovery/registry")
async def get_company_registry():
    return company_registry.summary()


@app.post("/api/v1/discovery/registry/add", status_code=201)
async def add_company_registry_entry(req: RegistryAddRequest):
    if req.career_url:
        parsed = company_registry.register_company_from_url(req.career_url)
        if not parsed:
            raise HTTPException(
                status_code=400,
                detail="Could not extract a supported ATS slug (Greenhouse, Lever, Ashby, SmartRecruiters) from URL.",
            )
        return {"status": "REGISTERED", "ats": parsed[0], "slug": parsed[1], "summary": company_registry.summary()}
    if not req.ats or not req.slug:
        raise HTTPException(status_code=400, detail="Provide either career_url or both ats and slug.")
    try:
        res = company_registry.add_slug(req.ats, req.slug, source="manual")
        return {"status": "REGISTERED", **res, "summary": company_registry.summary()}
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post("/api/v1/jobs/{job_id}/mark-applied")
async def mark_job_applied_manually(job_id: str):
    job = orchestrator.repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    now_ist = datetime.now(IST_TZ).isoformat()
    reasoning = job.get("match_reasoning") or {}
    updated_reasoning = {
        **(reasoning if isinstance(reasoning, dict) else {}),
        "applied_mode": "manual_confirmed",
    }
    orchestrator.repo.update_job_status(
        job_id,
        "APPLIED",
        {"applied_at": now_ist, "match_reasoning": updated_reasoning},
    )
    thread_id = f"thread-{job_id}"
    if thread_id in orchestrator._checkpoints:
        orchestrator._checkpoints[thread_id] = {
            **orchestrator._checkpoints[thread_id],
            "final_status": "APPLIED",
            "updated_at": now_ist,
        }
    orchestrator.repo.record_application_event(
        {
            "job_id": job_id,
            "langgraph_thread_id": thread_id,
            "event_type": "APPLIED",
        }
    )
    updated_job = orchestrator.repo.get_job(job_id) or {**job, "status": "APPLIED", "applied_at": now_ist}
    return {"status": "APPLIED", "job": updated_job}


@app.get("/api/v1/pipeline/blueprint")
async def get_or_build_profile_blueprint(refresh: bool = Query(default=False)):
    profile = profile_store.load()
    prefs = profile.get("search_preferences") or {}
    cached = prefs.get("latest_blueprint")
    if cached and not refresh:
        return {"blueprint": cached, "cached": True}

    blueprint = await generate_profile_search_blueprint(profile)
    profile["search_preferences"] = {**prefs, "latest_blueprint": blueprint}
    await asyncio.to_thread(profile_store.save, profile)
    return {"blueprint": blueprint, "cached": False}


@app.get("/api/v1/pipeline/search-status")
async def get_autonomous_search_status():
    return dict(_search_status)


def _update_search_status(patch: dict[str, Any]) -> None:
    _search_status.update(patch)


async def _execute_autonomous_search(
    req: StartAutonomousSearchRequest,
    profile: dict[str, Any],
    blueprint: dict[str, Any],
) -> dict[str, Any]:
    try:
        _update_search_status(
            {
                "running": True,
                "phase": "cleaning",
                "message": "Agent 1 cleaning prior queue & syncing search blueprint...",
                "error": None,
            }
        )
        await asyncio.to_thread(re_evaluate_existing_unapplied_jobs, profile)

        _update_search_status(
            {
                "phase": "crawling",
                "message": "Agent 1 crawling LinkedIn, Indeed, Ephemeral Naukri/LinkedIn, ATS boards & search dorks...",
            }
        )
        prefs = profile.get("search_preferences") or {}
        boards = prefs.get("ats_boards") or {}
        pipe = DiscoveryPipeline(
            repo=orchestrator.repo,
            profile=profile,
            output_base_dir=OUTPUT_RESUMES_DIR,
            registry=company_registry,
        )
        discovery = await pipe.run(
            search_terms=blueprint.get("search_terms"),
            locations=blueprint.get("locations"),
            results_wanted=req.results_wanted,
            greenhouse_slugs=boards.get("greenhouse"),
            lever_slugs=boards.get("lever"),
            ashby_slugs=boards.get("ashby"),
            smartrecruiters_slugs=boards.get("smartrecruiters"),
            poll_gmail_imap=req.poll_gmail_imap and bool(settings.gmail_address and settings.gmail_app_password),
            sweep_company_registry=req.sweep_company_registry,
            run_ats_dorks=req.run_ats_dorks,
            use_ephemeral_chromium=req.use_ephemeral_chromium,
            blueprint=blueprint,
            on_progress=_update_search_status,
        )
        _update_search_status(
            {
                "running": False,
                "phase": "completed",
                "discovered_count": discovery["discovered_count"],
                "processed_count": discovery["discovered_count"],
                "total_to_process": discovery["discovered_count"],
                "ready_to_apply_count": discovery["ready_to_apply_count"],
                "rejected_count": discovery["rejected_count"],
                "finished_at": datetime.now(IST_TZ).isoformat(),
                "error": None,
                "message": (
                    f"Completed: {discovery['discovered_count']} relevant jobs processed "
                    f"({discovery['ready_to_apply_count']} Ready to Apply)."
                ),
            }
        )
        return discovery
    except Exception as exc:
        _update_search_status(
            {
                "running": False,
                "phase": "error",
                "finished_at": datetime.now(IST_TZ).isoformat(),
                "error": str(exc),
                "message": f"Autonomous search failed: {exc}",
            }
        )
        if not req.background:
            raise
        return {
            "discovered_count": _search_status.get("discovered_count", 0),
            "ready_to_apply_count": _search_status.get("ready_to_apply_count", 0),
            "rejected_count": _search_status.get("rejected_count", 0),
            "jobs": [],
        }


@app.post("/api/v1/pipeline/start-autonomous-search")
async def start_autonomous_search(req: StartAutonomousSearchRequest):
    """Agent 0 (Profile Summary & Blueprint) -> Agent 1 (Multi-Source + Ephemeral Guest Search)."""
    profile = profile_store.load()
    prefs = profile.get("search_preferences") or {}

    if req.background and _search_status.get("running"):
        cached_bp = prefs.get("latest_blueprint") or await generate_profile_search_blueprint(profile, allow_llm=False)
        return {
            "status": "ALREADY_RUNNING",
            "blueprint": cached_bp,
            "search_status": dict(_search_status),
            "discovery": {
                "discovered_count": _search_status.get("discovered_count", 0),
                "ready_to_apply_count": _search_status.get("ready_to_apply_count", 0),
                "rejected_count": _search_status.get("rejected_count", 0),
            },
        }

    if req.background:
        try:
            blueprint = await asyncio.wait_for(generate_profile_search_blueprint(profile), timeout=4.0)
        except Exception:
            blueprint = await generate_profile_search_blueprint(profile, allow_llm=False)
    else:
        blueprint = await generate_profile_search_blueprint(profile)

    profile["search_preferences"] = {**prefs, "latest_blueprint": blueprint}
    await asyncio.to_thread(profile_store.save, profile)

    if req.background:
        _update_search_status(
            {
                "running": True,
                "phase": "starting",
                "message": "Agent 0 generated your blueprint — starting Agent 1 crawlers...",
                "discovered_count": 0,
                "processed_count": 0,
                "total_to_process": 0,
                "ready_to_apply_count": 0,
                "rejected_count": 0,
                "started_at": datetime.now(IST_TZ).isoformat(),
                "finished_at": None,
                "error": None,
            }
        )
        task = asyncio.create_task(_execute_autonomous_search(req, profile, blueprint))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {
            "status": "STARTED",
            "blueprint": blueprint,
            "search_status": dict(_search_status),
            "discovery": {"discovered_count": 0, "ready_to_apply_count": 0, "rejected_count": 0},
        }

    _update_search_status(
        {
            "running": True,
            "phase": "starting",
            "message": "Running synchronous autonomous search...",
            "discovered_count": 0,
            "processed_count": 0,
            "total_to_process": 0,
            "ready_to_apply_count": 0,
            "rejected_count": 0,
            "started_at": datetime.now(IST_TZ).isoformat(),
            "finished_at": None,
            "error": None,
        }
    )
    discovery = await _execute_autonomous_search(req, profile, blueprint)
    return {"blueprint": blueprint, "discovery": discovery}


@app.post("/api/v1/agent/chat-verify")
async def chat_verify_discovered_jobs(req: ChatVerifyRequest):
    """Agent 2 — Conversational AI filter & Round-2 verifier over discovered roles."""
    profile = profile_store.load()
    await asyncio.to_thread(re_evaluate_existing_unapplied_jobs, profile)
    prefs = profile.get("search_preferences") or {}
    blueprint = prefs.get("latest_blueprint") or await generate_profile_search_blueprint(profile)

    all_jobs = await asyncio.to_thread(orchestrator.repo.list_all_jobs, limit=250)
    if req.scope == "ready_only":
        candidates = [j for j in all_jobs if j.get("status") == "READY_TO_APPLY"]
    else:
        candidates = [
            j
            for j in all_jobs
            if j.get("status") in ("DISCOVERED", "READY_TO_APPLY", "QUEUED_FOR_TAILORING", "SCORED_REJECTED")
        ]

    verification = await verify_and_filter_jobs_conversational(
        user_message=req.message,
        jobs=candidates,
        profile=profile,
        blueprint=blueprint,
    )

    verdicts = verification.get("verdicts") or {}
    verified_jobs: list[dict[str, Any]] = []
    for job in candidates:
        jid = str(job["id"])
        verdict = verdicts.get(jid)
        if not verdict:
            continue
        existing_reasoning = job.get("match_reasoning") or {}
        if not isinstance(existing_reasoning, dict):
            existing_reasoning = {}

        approved = bool(verdict.get("approved"))
        updated_reasoning = {
            **existing_reasoning,
            "stage": "round2_verified" if approved else "round2_filtered_out",
            "round2_verified": approved,
            "round2_reason": str(verdict.get("reason") or ""),
            "work_mode_detected": str(verdict.get("work_mode") or existing_reasoning.get("work_mode_detected", "unspecified")),
        }

        if approved:
            pdf_path = job.get("tailored_pdf_path")
            if not pdf_path or not Path(str(pdf_path)).exists():
                score_obj = deterministic_keyword_score(str(job.get("jd_raw_text") or ""), profile)
                compiled = tailor_and_compile_resume(
                    job_id=jid,
                    profile=profile,
                    score_result=score_obj,
                    output_base_dir=OUTPUT_RESUMES_DIR,
                )
                pdf_path = compiled.as_posix()

            orchestrator.repo.update_job_status(
                jid,
                "READY_TO_APPLY",
                {
                    "match_reasoning": updated_reasoning,
                    "tailored_pdf_path": pdf_path,
                    "match_score": verdict.get("fit_score", job.get("match_score")),
                },
            )
            verified_jobs.append(
                {
                    **job,
                    "status": "READY_TO_APPLY",
                    "match_reasoning": updated_reasoning,
                    "tailored_pdf_path": pdf_path,
                }
            )
        else:
            orchestrator.repo.update_job_status(
                jid,
                "SCORED_REJECTED",
                {"match_reasoning": updated_reasoning},
            )

    return {
        "reply": verification["assistant_reply"],
        "evaluated_count": len(candidates),
        "verified_count": len(verified_jobs),
        "rejected_count": len(verification.get("rejected_job_ids") or []),
        "verified_jobs": verified_jobs,
    }


def _update_apply_status(patch: dict[str, Any]) -> None:
    _apply_status.update(patch)


@app.get("/api/v1/orchestrator/apply-status")
async def get_apply_batch_status():
    return dict(_apply_status)


async def _execute_parallel_apply_batch(
    target_jobs: list[dict[str, Any]],
    profile: dict[str, Any],
    req: ApplyVerifiedBatchRequest,
) -> dict[str, Any]:
    max_workers = max(1, min(6, int(req.concurrency or 4)))
    total_jobs = len(target_jobs)
    _update_apply_status(
        {
            "running": True,
            "phase": "applying",
            "message": f"Running {max_workers} parallel workers across {total_jobs} listings...",
            "total_jobs": total_jobs,
            "completed_jobs": 0,
            "applied_count": 0,
            "manual_trouble_count": 0,
            "skipped_quota_count": 0,
            "concurrency": max_workers,
            "error": None,
        }
    )

    if total_jobs == 0:
        _update_apply_status(
            {
                "running": False,
                "phase": "completed",
                "finished_at": datetime.now(IST_TZ).isoformat(),
                "message": "No jobs selected to apply.",
            }
        )
        return {
            "total_attempted": 0,
            "applied_count": 0,
            "manual_trouble_count": 0,
            "skipped_quota_count": 0,
            "results": [],
        }

    slot_queue: asyncio.Queue[int] = asyncio.Queue()
    for s in range(1, max_workers + 1):
        slot_queue.put_nowait(s)

    applied_count = 0
    manual_trouble_count = 0
    skipped_quota_count = 0
    completed_jobs = 0
    results: list[dict[str, Any]] = []

    async def _run_worker(job: dict[str, Any]) -> dict[str, Any]:
        slot = await slot_queue.get()
        jid = str(job["id"])
        portal = str(job.get("portal") or "other_ats")
        company = str(job.get("company_name") or "Company")
        try:
            allowed, reason = circuit_breaker.can_execute(portal)
            if not allowed:
                reasoning = job.get("match_reasoning") or {}
                trouble_reasoning = {**(reasoning if isinstance(reasoning, dict) else {}), "trouble_reason": reason}
                await asyncio.to_thread(
                    orchestrator.repo.update_job_status,
                    jid,
                    "PAUSED_HITL",
                    {"match_reasoning": trouble_reasoning},
                )
                return {"job_id": jid, "final_status": "PAUSED_HITL", "reason": reason}

            daily_limit = get_portal_daily_limit(portal)
            quota_ok, _ = await asyncio.to_thread(
                orchestrator.repo.check_and_increment_daily_quota,
                portal,
                daily_limit,
                True,
            )
            if not quota_ok:
                return {"job_id": jid, "final_status": "SKIPPED_DAILY_QUOTA", "reason": "Daily portal quota reached"}

            if "PYTEST_CURRENT_TEST" not in os.environ:
                print(f"[Agent 3 | Worker #{slot}] Applying to {company} ({portal})...", flush=True)

            exec_state = await portal_executor.execute_job(
                job=job,
                candidate_profile=profile,
                live_browser=req.live_browser,
                headless=req.headless,
                screenshot_dir=OUTPUT_SCREENSHOTS_DIR,
                worker_slot=slot,
            )
            final_status = exec_state["final_status"]
            if final_status != "APPLIED":
                trouble = exec_state.get("blocker_reason") or (
                    "Dream Company approval required"
                    if exec_state.get("is_dream_company")
                    else "Requires quick confirmation or manual portal click"
                )
                reasoning = job.get("match_reasoning") or {}
                trouble_reasoning = {**(reasoning if isinstance(reasoning, dict) else {}), "trouble_reason": trouble}
                await asyncio.to_thread(
                    orchestrator.repo.update_job_status,
                    jid,
                    "PAUSED_HITL",
                    {"match_reasoning": trouble_reasoning},
                )
            return {
                "job_id": jid,
                "final_status": final_status,
                "reason": exec_state.get("blocker_reason"),
            }
        except Exception as exc:
            reasoning = job.get("match_reasoning") or {}
            trouble_reasoning = {**(reasoning if isinstance(reasoning, dict) else {}), "trouble_reason": str(exc)}
            await asyncio.to_thread(
                orchestrator.repo.update_job_status,
                jid,
                "PAUSED_HITL",
                {"match_reasoning": trouble_reasoning},
            )
            return {"job_id": jid, "final_status": "PAUSED_HITL", "reason": str(exc)}
        finally:
            slot_queue.put_nowait(slot)

    try:
        tasks = [asyncio.create_task(_run_worker(job)) for job in target_jobs]
        for fut in asyncio.as_completed(tasks):
            res = await fut
            results.append(res)
            completed_jobs += 1
            st = res["final_status"]
            if st == "APPLIED":
                applied_count += 1
            elif st == "SKIPPED_DAILY_QUOTA":
                skipped_quota_count += 1
            else:
                manual_trouble_count += 1

            _update_apply_status(
                {
                    "completed_jobs": completed_jobs,
                    "applied_count": applied_count,
                    "manual_trouble_count": manual_trouble_count,
                    "skipped_quota_count": skipped_quota_count,
                    "message": (
                        f"Processed {completed_jobs}/{total_jobs} jobs — "
                        f"{applied_count} applied, {manual_trouble_count} routed to Needs Manual Apply..."
                    ),
                }
            )

        _update_apply_status(
            {
                "running": False,
                "phase": "completed",
                "finished_at": datetime.now(IST_TZ).isoformat(),
                "message": (
                    f"Batch complete: {applied_count} applied, "
                    f"{manual_trouble_count} routed to Needs Manual Apply."
                ),
            }
        )
        return {
            "total_attempted": total_jobs,
            "applied_count": applied_count,
            "manual_trouble_count": manual_trouble_count,
            "skipped_quota_count": skipped_quota_count,
            "results": results,
        }
    except Exception as exc:
        _update_apply_status(
            {
                "running": False,
                "phase": "error",
                "finished_at": datetime.now(IST_TZ).isoformat(),
                "error": str(exc),
                "message": f"Batch apply failed: {exc}",
            }
        )
        if not req.background:
            raise
        return {
            "total_attempted": total_jobs,
            "applied_count": applied_count,
            "manual_trouble_count": manual_trouble_count,
            "skipped_quota_count": skipped_quota_count,
            "results": results,
        }


@app.post("/api/v1/orchestrator/apply-verified-batch")
async def apply_verified_batch(req: ApplyVerifiedBatchRequest):
    """Agent 3 — Parallel worker pool applying to selected / Round-2 verified jobs."""
    if req.background and _apply_status.get("running"):
        return {
            "status": "ALREADY_RUNNING",
            "apply_status": dict(_apply_status),
            "total_attempted": _apply_status.get("total_jobs", 0),
            "applied_count": _apply_status.get("applied_count", 0),
            "manual_trouble_count": _apply_status.get("manual_trouble_count", 0),
            "skipped_quota_count": _apply_status.get("skipped_quota_count", 0),
            "results": [],
        }

    profile = profile_store.load()
    if req.job_ids:
        target_jobs = [
            j
            for jid in req.job_ids
            if (j := await asyncio.to_thread(orchestrator.repo.get_job, jid)) is not None
        ]
    else:
        ready_jobs = await asyncio.to_thread(orchestrator.repo.list_all_jobs, status="READY_TO_APPLY", limit=200)
        round2_only = [
            j
            for j in ready_jobs
            if isinstance(j.get("match_reasoning"), dict) and j["match_reasoning"].get("round2_verified")
        ]
        target_jobs = round2_only or ready_jobs

    if req.background:
        _update_apply_status(
            {
                "running": True,
                "phase": "starting",
                "total_jobs": len(target_jobs),
                "completed_jobs": 0,
                "applied_count": 0,
                "manual_trouble_count": 0,
                "skipped_quota_count": 0,
                "concurrency": max(1, min(6, int(req.concurrency or 4))),
                "started_at": datetime.now(IST_TZ).isoformat(),
                "finished_at": None,
                "error": None,
                "message": f"Starting {max(1, min(6, int(req.concurrency or 4)))} parallel apply workers...",
            }
        )
        task = asyncio.create_task(_execute_parallel_apply_batch(target_jobs, profile, req))
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
        return {
            "status": "STARTED",
            "apply_status": dict(_apply_status),
            "total_attempted": len(target_jobs),
            "applied_count": 0,
            "manual_trouble_count": 0,
            "skipped_quota_count": 0,
            "results": [],
        }

    return await _execute_parallel_apply_batch(target_jobs, profile, req)


@app.post("/api/v1/jobs/{job_id}/hydrate")
async def hydrate_and_rescore_job(job_id: str):
    job = orchestrator.repo.get_job(job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
    hydrated = hydrate_job_details(job, force=True)
    pipe = DiscoveryPipeline(
        repo=orchestrator.repo,
        profile=profile_store.load(),
        output_base_dir=OUTPUT_RESUMES_DIR,
        registry=company_registry,
    )
    upserted = await pipe.process_job(hydrated, hydrate_missing_jds=False)
    return {"status": "HYDRATED", "job": upserted}


@app.post("/api/v1/browser/warmup/{portal}")
async def warmup_browser_profile(portal: str, duration_seconds: int = Query(default=180, ge=15, le=600)):
    norm = portal.strip().lower()
    login_urls = {
        "linkedin_easy": "https://www.linkedin.com/login",
        "naukri": "https://www.naukri.com/nlogin/login",
        "instahyre": "https://www.instahyre.com/candidate/login/",
        "greenhouse": "https://boards.greenhouse.io/",
        "lever": "https://jobs.lever.co/",
        "ashby": "https://jobs.ashbyhq.com/",
        "smartrecruiters": "https://jobs.smartrecruiters.com/",
    }
    target_url = login_urls.get(norm, "https://www.google.com")

    async def _run_warmup() -> None:
        try:
            async with launch_stealth_context(norm, headless=False) as context:
                page = context.pages[0] if context.pages else await context.new_page()
                await page.goto(target_url, wait_until="domcontentloaded")
                elapsed = 0.0
                while elapsed < duration_seconds:
                    if page.is_closed():
                        break
                    await asyncio.sleep(1.5)
                    elapsed += 1.5
        except Exception:
            return

    task = asyncio.create_task(_run_warmup())
    _background_tasks.add(task)
    task.add_done_callback(_background_tasks.discard)
    return {
        "status": "WARMUP_OPENED",
        "portal": norm,
        "login_url": target_url,
        "duration_seconds": duration_seconds,
    }


@app.delete("/api/v1/browser/profile/{portal}")
async def reset_browser_profile(portal: str):
    norm = portal.strip().lower()
    return await asyncio.to_thread(clear_portal_profile, norm)


@app.post("/api/v1/discovery/run")
async def run_manual_discovery(req: DiscoveryRunRequest):
    active_profile = profile_store.load()
    prefs = active_profile.get("search_preferences", {})
    boards = prefs.get("ats_boards", {})
    pipe = DiscoveryPipeline(
        repo=orchestrator.repo,
        profile=active_profile,
        output_base_dir=OUTPUT_RESUMES_DIR,
        registry=company_registry,
    )
    summary = await pipe.run(
        search_terms=req.search_terms,
        locations=req.locations,
        email_bodies=req.email_bodies,
        results_wanted=req.results_wanted,
        greenhouse_slugs=req.greenhouse_slugs if req.greenhouse_slugs is not None else boards.get("greenhouse"),
        lever_slugs=req.lever_slugs if req.lever_slugs is not None else boards.get("lever"),
        ashby_slugs=req.ashby_slugs if req.ashby_slugs is not None else boards.get("ashby"),
        smartrecruiters_slugs=req.smartrecruiters_slugs
        if req.smartrecruiters_slugs is not None
        else boards.get("smartrecruiters"),
        poll_gmail_imap=req.poll_gmail_imap,
        sweep_company_registry=req.sweep_company_registry,
        run_ats_dorks=req.run_ats_dorks,
    )
    return summary


@app.post("/api/v1/orchestrator/trigger-batch", status_code=202)
async def trigger_batch(req: BatchTriggerRequest):
    allowed, reason = circuit_breaker.can_execute(req.portal)
    if not allowed:
        return {"status": "BLOCKED_BY_CIRCUIT_BREAKER", "reason": reason}

    daily_limit = get_portal_daily_limit(req.portal)
    quota_ok, current_count = orchestrator.repo.check_and_increment_daily_quota(
        req.portal, daily_limit, increment=False
    )
    if not quota_ok:
        return {
            "status": "BLOCKED_BY_DAILY_QUOTA",
            "portal": req.portal,
            "current_count": current_count,
            "max_daily_limit": daily_limit,
        }

    if req.execute_now:
        wave = await run_application_wave(
            wave_name="manual_batch",
            portals=[req.portal],
            max_per_portal=req.max_jobs,
            live_browser=req.live_browser,
        )
        return {
            "status": "BATCH_EXECUTED",
            "portal": req.portal,
            "max_jobs": req.max_jobs,
            "supervised_mode": req.supervised_mode,
            "daily_limit": daily_limit,
            "wave": wave,
        }

    return {
        "status": "BATCH_QUEUED",
        "portal": req.portal,
        "max_jobs": req.max_jobs,
        "supervised_mode": req.supervised_mode,
        "daily_limit": daily_limit,
        "current_count": current_count,
    }


@app.get("/api/v1/hitl/threads")
async def list_hitl_threads():
    threads = orchestrator.list_threads()
    enriched = []
    for item in threads:
        job_id = str(item.get("job_id", ""))
        screenshot_file = Path(item.get("screenshot_path") or (OUTPUT_SCREENSHOTS_DIR / f"{job_id}_annotated.png"))
        has_preview = screenshot_file.exists() or item.get("final_status") == "PAUSED_HITL"
        enriched.append(
            {
                **item,
                "screenshot_url": f"/api/v1/hitl/screenshots/{job_id}" if has_preview else None,
            }
        )
    return {"count": len(enriched), "threads": enriched}


@app.get("/api/v1/hitl/screenshots/{job_id}")
async def get_hitl_screenshot(job_id: str):
    path = OUTPUT_SCREENSHOTS_DIR / f"{job_id}_annotated.png"
    if not path.exists():
        thread = orchestrator._checkpoints.get(f"thread-{job_id}")
        if not thread:
            raise HTTPException(status_code=404, detail="No annotated HITL screenshot found for job.")
        render_hitl_preview_card(
            output_path=path,
            company_name=thread.get("company_name", "Unknown Company"),
            role_title=thread.get("role_title", "Role"),
            portal=thread.get("portal", "ats"),
            low_confidence_fields=thread.get("low_confidence_fields", []),
            is_dream=bool(thread.get("is_dream_company")),
            security_blocker=bool(thread.get("security_blocker_detected")),
        )
    return FileResponse(path=str(path), media_type="image/png")


@app.post("/api/v1/hitl/resume")
async def resume_hitl_thread(req: HITLResumeRequest):
    try:
        updated_state = await orchestrator.resume_from_human(
            thread_id=req.langgraph_thread_id,
            action=req.action,
            field_overrides=req.field_overrides,
            save_to_qa_cache=req.save_to_qa_cache,
        )
        if req.live_browser_submit and updated_state["final_status"] == "APPLIED":
            await portal_executor.submit_resumed_in_browser(updated_state, profile_store.load())
        return {
            "status": "RESUMED_AND_PROCESSED",
            "job_id": updated_state["job_id"],
            "final_status": updated_state["final_status"],
            "qa_cache_entries_added": len(req.field_overrides or {}),
        }
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@app.post("/api/v1/circuit-breaker/trip")
async def trip_circuit_breaker(req: CircuitBreakerTripRequest):
    circuit_breaker.tripped_portals[req.portal] = req.reason
    return {
        "status": "CIRCUIT_BREAKER_TRIPPED",
        "portal": req.portal,
        "reason": req.reason,
    }


@app.post("/api/v1/circuit-breaker/reset")
async def reset_circuit_breaker(req: CircuitBreakerResetRequest):
    if req.portal.strip().lower() == "all":
        cleared = list(circuit_breaker.tripped_portals.keys())
        circuit_breaker.tripped_portals.clear()
        return {"status": "CIRCUIT_BREAKER_RESET", "cleared_portals": cleared}
    removed = circuit_breaker.tripped_portals.pop(req.portal, None)
    return {
        "status": "CIRCUIT_BREAKER_RESET",
        "portal": req.portal,
        "was_tripped": removed is not None,
    }


@app.post("/api/v1/scheduler/trigger/{job_name}")
async def trigger_scheduled_job(job_name: str):
    if job_name == "discovery_polling":
        result = await run_scheduled_discovery()
    elif job_name == "naukri_morning_refresh":
        result = await run_scheduled_naukri_refresh()
    elif job_name == "morning_application_wave":
        result = await run_application_wave(wave_name="morning")
    elif job_name == "afternoon_application_wave":
        result = await run_application_wave(wave_name="afternoon")
    else:
        raise HTTPException(status_code=404, detail=f"Unknown scheduled job: {job_name}")
    return {"status": "TRIGGERED", "job_name": job_name, "result": result}


@app.get("/api/v1/profile")
async def get_profile_vault():
    profile = profile_store.load()
    return {
        "profile": profile,
        "is_placeholder_contact": profile_store.is_placeholder_contact(profile),
    }


@app.put("/api/v1/profile")
async def update_profile_vault(payload: dict[str, Any]):
    cleaned = dict(payload)
    prefs = dict(cleaned.get("search_preferences") or {})
    prefs["latest_blueprint"] = None
    prefs["ephemeral_cookie_epoch"] = datetime.now(IST_TZ).isoformat()
    cleaned["search_preferences"] = prefs

    saved = await asyncio.to_thread(profile_store.save, cleaned)
    orchestrator.qa_engine.profile = saved
    try:
        await preview_profile_resume()
    except Exception:
        pass
    return {
        "status": "SAVED",
        "profile": saved,
        "is_placeholder_contact": profile_store.is_placeholder_contact(saved),
    }


@app.post("/api/v1/profile/preview-resume")
async def preview_profile_resume():
    profile = profile_store.load()
    prefs = profile.get("search_preferences") or {}
    roles = [str(r) for r in (prefs.get("target_roles") or []) if str(r).strip()]
    skills = [str(s) for s in (profile.get("skills_years") or {}).keys() if str(s).strip()]
    stacks = [str(p.get("tech_stack") or "") for p in (profile.get("projects") or []) if p.get("tech_stack")]
    sample_jd = " ".join([*roles, *skills, *stacks]).strip() or (
        "Experienced professional skilled in analytics, execution, communication, and cross-functional delivery."
    )
    primary_role = roles[0] if roles else ""
    score_res = deterministic_keyword_score(sample_jd, profile, role_title=primary_role)
    pdf_path = await asyncio.to_thread(
        tailor_and_compile_resume,
        job_id="profile-preview",
        profile=profile,
        score_result=score_res,
        output_base_dir=OUTPUT_RESUMES_DIR,
    )
    tex_path = pdf_path.with_suffix(".tex")
    return {
        "pdf_url": "/api/v1/profile/preview-resume/pdf",
        "download_url": "/api/v1/profile/preview-resume/pdf?download=true",
        "tex_content": tex_path.read_text(encoding="utf-8") if tex_path.exists() else "",
    }


@app.get("/api/v1/profile/preview-resume/pdf")
async def get_profile_preview_pdf(download: bool = Query(default=False)):
    pdf_path = OUTPUT_RESUMES_DIR / "profile-preview" / "Srikar_Vaka_Resume.pdf"
    if download or not pdf_path.exists():
        await preview_profile_resume()
    profile = profile_store.load()
    raw_name = str(profile.get("demographics", {}).get("full_name") or "Candidate")
    safe_name = re.sub(r"[^A-Za-z0-9_-]+", "_", raw_name).strip("_") or "Candidate"
    filename = f"{safe_name}_Resume.pdf"
    disp = "attachment" if download else "inline"
    return FileResponse(
        path=str(pdf_path),
        media_type="application/pdf",
        headers={"Content-Disposition": f'{disp}; filename="{filename}"'},
    )


@app.get("/api/v1/qa-cache")
async def list_qa_cache_entries(limit: int = Query(default=100, ge=1, le=500)):
    items = orchestrator.repo.list_qa_cache(limit=limit)
    return {"count": len(items), "items": items}


@app.post("/api/v1/qa-cache", status_code=201)
async def create_qa_cache_entry(req: QACacheCreateRequest):
    emb = embed_text(req.question_normalized)
    entry = orchestrator.repo.insert_qa_cache(
        question_normalized=req.question_normalized,
        field_type=req.field_type,
        canonical_answer=req.canonical_answer,
        question_embedding=emb,
        portal_context=req.portal_context,
        source="human_verified",
    )
    sanitized = {k: v for k, v in entry.items() if k != "question_embedding"}
    return {"item": sanitized}


@app.delete("/api/v1/qa-cache/{item_id}")
async def delete_qa_cache_entry(item_id: str):
    removed = orchestrator.repo.delete_qa_cache(item_id)
    if not removed:
        raise HTTPException(status_code=404, detail=f"qa_cache entry not found: {item_id}")
    return {"status": "DELETED", "id": item_id}


@app.post("/api/v1/qa-cache/resolve-preview")
async def preview_qa_resolution(req: QAResolvePreviewRequest):
    engine = QAResolutionEngine(repo=orchestrator.repo, profile=profile_store.load())
    field: FormFieldSpec = {
        "field_id": "preview_field",
        "label_text": req.label_text,
        "field_type": req.field_type,  # type: ignore[typeddict-item]
        "options": req.options,
        "proposed_value": None,
        "confidence": 0.0,
        "reasoning": "",
        "bounding_box": None,
    }
    resolved = engine.resolve_field(field)
    return {
        "resolved": resolved,
        "will_trigger_hitl": resolved["confidence"] < settings.confidence_threshold,
        "confidence_threshold": settings.confidence_threshold,
    }


FRONTEND_DIST = Path("frontend/dist")
if FRONTEND_DIST.is_dir():
    assets_dir = FRONTEND_DIST / "assets"
    if assets_dir.is_dir():
        app.mount("/assets", StaticFiles(directory=str(assets_dir)), name="frontend-assets")

    @app.get("/", include_in_schema=False)
    async def serve_spa_root():
        return FileResponse(str(FRONTEND_DIST / "index.html"))

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_spa_fallback(full_path: str):
        if full_path.startswith(("api/", "health", "docs", "redoc", "openapi.json")):
            raise HTTPException(status_code=404, detail="Not Found")
        candidate = FRONTEND_DIST / full_path
        if candidate.is_file():
            return FileResponse(str(candidate))
        return FileResponse(str(FRONTEND_DIST / "index.html"))
