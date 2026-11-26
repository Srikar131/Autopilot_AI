from __future__ import annotations

import asyncio
import inspect
import os
import subprocess
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Awaitable, Callable

from src.brain.latex_compiler import (
    compile_tex_to_pdf,
    escape_latex,
    render_fallback_pdf,
    render_tailored_tex,
)
from src.brain.scorer import JDScorerResult, deterministic_keyword_score, score_job_description
from src.brain.verifier_agent import detect_work_mode
from src.config import settings
from src.db.seed_profile_vault import DEFAULT_CANDIDATE_PROFILE
from src.db.supabase_client import SupabaseRepository
from src.ingestion.ats_dork_scraper import harvest_ats_search_dorks
from src.ingestion.ats_scraper import harvest_direct_ats_boards
from src.ingestion.company_registry import CompanyATSRegistry
from src.ingestion.ephemeral_browser_scout import harvest_ephemeral_guest_jobs
from src.ingestion.gmail_ingestor import (
    extract_job_links_from_email_bodies,
    fetch_unread_gmail_alert_bodies,
)
from src.ingestion.jd_enrichment import (
    build_profile_search_queries,
    extract_external_job_id,
    hydrate_job_details,
    is_role_relevant_to_profile,
)
from src.ingestion.jobspy_worker import harvest_jobs_unauthenticated

DREAM_COMPANIES: set[str] = {
    "google",
    "deepmind",
    "openai",
    "anthropic",
    "stripe",
    "razorpay",
    "zepto",
    "cred",
    "uber",
    "microsoft",
}


def is_dream_company(company_name: str, dream_companies: set[str] | list[str] | None = None) -> bool:
    """Returns True if the normalized company name matches a configured dream company."""
    targets = dream_companies if dream_companies is not None else DREAM_COMPANIES
    lower_name = company_name.strip().lower()
    if not lower_name:
        return False
    return any(str(target).strip().lower() in lower_name for target in targets if str(target).strip())


def build_resume_latex_context(profile: dict[str, Any], score_result: JDScorerResult) -> dict[str, Any]:
    """Builds a LaTeX-escaped Jinja2 context matching templates/resume_master.tex.j2."""
    demo = profile.get("demographics", {})
    all_projects = profile.get("projects", [])

    selected_titles = {t.strip().lower() for t in score_result.selected_project_titles}
    matched_projects = [p for p in all_projects if p.get("title", "").strip().lower() in selected_titles]
    if not matched_projects:
        matched_projects = all_projects[:3]

    escaped_projects = [
        {
            "title": escape_latex(str(proj.get("title", ""))),
            "tech_stack": escape_latex(str(proj.get("tech_stack", ""))),
            "bullets": [escape_latex(str(b)) for b in proj.get("bullets", [])],
        }
        for proj in matched_projects[:3]
    ]

    skills_dict: dict[str, str] = {}
    core_skills = list(profile.get("skills_years", {}).keys())
    if core_skills:
        skills_dict["Core Expertise"] = escape_latex(", ".join(core_skills))
    if score_result.matched_skills:
        skills_dict["Role-Matched Keywords"] = escape_latex(", ".join(score_result.matched_skills))
    if not skills_dict:
        target_roles = (profile.get("search_preferences") or {}).get("target_roles") or ["General"]
        skills_dict["Focus Areas"] = escape_latex(", ".join(str(r) for r in target_roles[:4]))

    return {
        "candidate": {
            "name": escape_latex(str(demo.get("full_name", "Srikar Vaka"))),
            "location": escape_latex(str(demo.get("current_city", "Hyderabad, India"))),
            "email": escape_latex(str(demo.get("email", "srikar.vaka@example.com"))),
            "phone": escape_latex(str(demo.get("phone", "+91-9876543210"))),
        },
        "tailored_summary": escape_latex(score_result.tailored_summary),
        "skills": skills_dict,
        "selected_projects": escaped_projects,
        "education": {
            "degree": escape_latex(str(demo.get("degree", ""))),
            "institution": escape_latex(str(demo.get("university", ""))),
            "graduation_year": escape_latex(str(demo.get("graduation_year", ""))),
        },
    }


def tailor_and_compile_resume(
    job_id: str,
    profile: dict[str, Any],
    score_result: JDScorerResult,
    output_base_dir: str | Path = "output/resumes",
    template_dir: str | Path = "templates",
) -> Path:
    """
    Renders tailored LaTeX for job_id into output/resumes/<job_id>/ and compiles PDF via Tectonic.
    Falls back to a real ReportLab-rendered 1-page ATS PDF if the local tectonic CLI binary is unavailable.
    """
    output_dir = Path(output_base_dir) / str(job_id)
    tex_path = output_dir / "Srikar_Vaka_Resume.tex"
    ctx = build_resume_latex_context(profile=profile, score_result=score_result)
    render_tailored_tex(ctx, tex_path, template_dir=template_dir)

    try:
        return compile_tex_to_pdf(tex_path)
    except (FileNotFoundError, subprocess.CalledProcessError):
        pdf_path = tex_path.with_suffix(".pdf")
        return render_fallback_pdf(ctx, pdf_path)


class DiscoveryPipeline:
    """
    Zero-auth Discovery -> Profile Pre-Filter -> Deep JD Hydration -> JD Scoring
    -> Tailored LaTeX/PDF Compilation -> Supabase Upsert pipeline.
    """

    def __init__(
        self,
        repo: SupabaseRepository | None = None,
        profile: dict[str, Any] | None = None,
        output_base_dir: str | Path = "output/resumes",
        template_dir: str | Path = "templates",
        match_threshold: float | None = None,
        scorer_fn: Callable[[str, dict[str, Any] | None], Awaitable[JDScorerResult]] | None = None,
        registry: CompanyATSRegistry | None = None,
    ) -> None:
        self.repo = repo or SupabaseRepository()
        self.profile = profile or DEFAULT_CANDIDATE_PROFILE
        self.output_base_dir = Path(output_base_dir)
        self.template_dir = Path(template_dir)
        self.match_threshold = (
            match_threshold if match_threshold is not None else settings.match_score_threshold
        )
        self.scorer_fn = scorer_fn or score_job_description
        self.registry = registry or CompanyATSRegistry()

    def discover_jobs(
        self,
        search_terms: list[str] | None = None,
        locations: list[str] | None = None,
        email_bodies: list[str] | None = None,
        results_wanted: int = 25,
        greenhouse_slugs: list[str] | None = None,
        lever_slugs: list[str] | None = None,
        ashby_slugs: list[str] | None = None,
        smartrecruiters_slugs: list[str] | None = None,
        poll_gmail_imap: bool = False,
        sweep_company_registry: bool = False,
        run_ats_dorks: bool = False,
    ) -> list[dict[str, Any]]:
        """Combines JobSpy, Gmail alerts, open-web ATS dorks, and public ATS registry boards."""
        derived_terms, derived_locs = build_profile_search_queries(self.profile)
        eff_terms = search_terms if search_terms is not None else derived_terms
        eff_locs = locations if locations is not None else derived_locs

        jobspy_jobs = harvest_jobs_unauthenticated(
            search_terms=eff_terms,
            locations=eff_locs,
            results_wanted=results_wanted,
        )

        bodies = list(email_bodies or [])
        if poll_gmail_imap:
            bodies.extend(fetch_unread_gmail_alert_bodies())
        email_jobs = extract_job_links_from_email_bodies(bodies)

        dork_jobs = (
            harvest_ats_search_dorks(search_terms=eff_terms, locations=eff_locs)
            if run_ats_dorks
            else []
        )

        gh = list(greenhouse_slugs or [])
        lv = list(lever_slugs or [])
        ab = list(ashby_slugs or [])
        sr = list(smartrecruiters_slugs or [])
        if sweep_company_registry:
            reg_slugs = self.registry.get_all_slugs()
            gh = sorted({*gh, *reg_slugs.get("greenhouse", [])})
            lv = sorted({*lv, *reg_slugs.get("lever", [])})
            ab = sorted({*ab, *reg_slugs.get("ashby", [])})
            sr = sorted({*sr, *reg_slugs.get("smartrecruiters", [])})

        raw_ats_jobs = (
            harvest_direct_ats_boards(
                greenhouse_slugs=gh,
                lever_slugs=lv,
                ashby_slugs=ab,
                smartrecruiters_slugs=sr,
            )
            if (gh or lv or ab or sr)
            else []
        )
        ats_jobs = (
            [
                j
                for j in raw_ats_jobs
                if is_role_relevant_to_profile(j.get("role_title", ""), j.get("location", ""), self.profile)
            ]
            if sweep_company_registry
            else raw_ats_jobs
        )

        deduped: list[dict[str, Any]] = []
        seen_hashes: set[str] = set()
        for job in [*jobspy_jobs, *email_jobs, *dork_jobs, *ats_jobs]:
            url_hash = job.get("url_hash")
            if not url_hash or url_hash in seen_hashes:
                continue
            seen_hashes.add(url_hash)
            deduped.append(job)
        return deduped

    async def process_job(
        self,
        job: dict[str, Any],
        hydrate_missing_jds: bool = True,
        use_fast_scorer: bool = False,
    ) -> dict[str, Any]:
        """Hydrates missing JDs, scores against Profile Vault, compiles PDF, and upserts to DB off the event loop."""
        working_job = await asyncio.to_thread(hydrate_job_details, job) if hydrate_missing_jds else dict(job)
        jd_text = str(working_job.get("jd_raw_text") or "")
        role_title = str(working_job.get("role_title") or "Software / AI Engineer")

        if use_fast_scorer and self.scorer_fn is score_job_description:
            score_result = deterministic_keyword_score(jd_text, self.profile, role_title=role_title)
        elif "role_title" in inspect.signature(self.scorer_fn).parameters:
            score_result = await self.scorer_fn(jd_text, self.profile, role_title)  # type: ignore[call-arg]
        else:
            score_result = await self.scorer_fn(jd_text, self.profile)

        job_id = str(working_job.get("id") or working_job["url_hash"][:12])
        external_job_id = str(
            working_job.get("external_job_id")
            or extract_external_job_id(
                working_job["canonical_url"],
                raw_id=job_id,
                url_hash=working_job["url_hash"],
            )
        )
        company_name = str(working_job.get("company_name") or "Unknown Company")
        location = str(working_job.get("location") or "India")
        dream_targets = self.profile.get("dream_companies") or DREAM_COMPANIES
        dream = bool(working_job.get("is_dream_company")) or is_dream_company(
            company_name, dream_companies=dream_targets
        )

        if score_result.hard_blocker_detected or score_result.match_score < self.match_threshold:
            status = "SCORED_REJECTED"
            tailored_pdf_path: str | None = None
        else:
            pdf_path = await asyncio.to_thread(
                tailor_and_compile_resume,
                job_id=job_id,
                profile=self.profile,
                score_result=score_result,
                output_base_dir=self.output_base_dir,
                template_dir=self.template_dir,
            )
            status = "READY_TO_APPLY"
            tailored_pdf_path = pdf_path.as_posix()

        reasoning_payload = {
            **score_result.to_dict(),
            "stage": "round1_discovered",
            "round2_verified": False,
            "work_mode_detected": detect_work_mode(role_title, location, jd_text),
            "posted_at": (
                working_job.get("posted_at")
                or working_job.get("discovered_at")
                or datetime.now(timezone.utc).isoformat()
            ),
        }

        payload: dict[str, Any] = {
            "id": job_id,
            "canonical_url": working_job["canonical_url"],
            "url_hash": working_job["url_hash"],
            "external_job_id": external_job_id,
            "discovery_source": str(working_job.get("discovery_source") or working_job["portal"]),
            "portal": working_job["portal"],
            "company_name": company_name,
            "role_title": role_title,
            "location": location,
            "jd_raw_text": jd_text,
            "match_score": score_result.match_score,
            "match_reasoning": reasoning_payload,
            "is_dream_company": dream,
            "status": status,
            "tailored_pdf_path": tailored_pdf_path,
        }
        return await asyncio.to_thread(self.repo.upsert_job, payload)

    async def run(
        self,
        search_terms: list[str] | None = None,
        locations: list[str] | None = None,
        email_bodies: list[str] | None = None,
        seed_jobs: list[dict[str, Any]] | None = None,
        results_wanted: int = 25,
        greenhouse_slugs: list[str] | None = None,
        lever_slugs: list[str] | None = None,
        ashby_slugs: list[str] | None = None,
        smartrecruiters_slugs: list[str] | None = None,
        poll_gmail_imap: bool = False,
        sweep_company_registry: bool = False,
        run_ats_dorks: bool = False,
        hydrate_missing_jds: bool = True,
        use_ephemeral_chromium: bool = False,
        blueprint: dict[str, Any] | None = None,
        max_jobs_to_process: int = 75,
        llm_score_limit: int = 18,
        on_progress: Callable[[dict[str, Any]], None] | None = None,
    ) -> dict[str, Any]:
        """Executes end-to-end discovery, hydration, concurrent scoring, resume compilation, and persistence."""
        discovered = await asyncio.to_thread(
            self.discover_jobs,
            search_terms=search_terms,
            locations=locations,
            email_bodies=email_bodies,
            results_wanted=results_wanted,
            greenhouse_slugs=greenhouse_slugs,
            lever_slugs=lever_slugs,
            ashby_slugs=ashby_slugs,
            smartrecruiters_slugs=smartrecruiters_slugs,
            poll_gmail_imap=poll_gmail_imap,
            sweep_company_registry=sweep_company_registry,
            run_ats_dorks=run_ats_dorks,
        )
        scraped_jobs = list(discovered)
        seen_hashes = {j["url_hash"] for j in scraped_jobs if "url_hash" in j}

        if use_ephemeral_chromium and blueprint:
            guest_jobs = await harvest_ephemeral_guest_jobs(blueprint=blueprint)
            for gj in guest_jobs:
                h = gj.get("url_hash")
                if h and h not in seen_hashes:
                    scraped_jobs.append(gj)
                    seen_hashes.add(h)

        relevant_scraped = [
            j
            for j in scraped_jobs
            if is_role_relevant_to_profile(j.get("role_title", ""), j.get("location", ""), self.profile)
        ][:max_jobs_to_process]

        all_jobs = list(relevant_scraped)
        kept_hashes = {j["url_hash"] for j in all_jobs if "url_hash" in j}
        for seed in seed_jobs or []:
            if seed.get("url_hash") not in kept_hashes:
                all_jobs.append(seed)
                if "url_hash" in seed:
                    kept_hashes.add(seed["url_hash"])

        if "PYTEST_CURRENT_TEST" not in os.environ:
            print(
                f"[Agent 1] Discovered {len(all_jobs)} relevant jobs (filtered from {len(scraped_jobs)} raw); starting concurrent hydration & scoring...",
                flush=True,
            )

        if on_progress is not None:
            on_progress(
                {
                    "phase": "scoring",
                    "discovered_count": len(all_jobs),
                    "total_to_process": len(all_jobs),
                    "processed_count": 0,
                    "ready_to_apply_count": 0,
                    "rejected_count": 0,
                    "message": f"Discovered {len(all_jobs)} relevant jobs — hydrating JDs, scoring & building resumes...",
                }
            )

        processed: list[dict[str, Any]] = []
        ready_count = 0
        rejected_count = 0
        sem = asyncio.Semaphore(6)

        async def _process_one(idx: int, job: dict[str, Any]) -> dict[str, Any]:
            async with sem:
                return await self.process_job(
                    job,
                    hydrate_missing_jds=hydrate_missing_jds,
                    use_fast_scorer=(idx >= llm_score_limit),
                )

        tasks = [asyncio.create_task(_process_one(i, job)) for i, job in enumerate(all_jobs)]
        for fut in asyncio.as_completed(tasks):
            upserted = await fut
            processed.append(upserted)
            if upserted.get("status") == "READY_TO_APPLY":
                ready_count += 1
            else:
                rejected_count += 1
            if on_progress is not None:
                on_progress(
                    {
                        "phase": "scoring",
                        "discovered_count": len(all_jobs),
                        "total_to_process": len(all_jobs),
                        "processed_count": len(processed),
                        "ready_to_apply_count": ready_count,
                        "rejected_count": rejected_count,
                        "message": f"Scored {len(processed)}/{len(all_jobs)} jobs ({ready_count} high-fit matches)...",
                    }
                )

        if "PYTEST_CURRENT_TEST" not in os.environ:
            print(
                f"[Agent 1] Finished: {ready_count} READY_TO_APPLY, {rejected_count} filtered out.",
                flush=True,
            )

        return {
            "discovered_count": len(all_jobs),
            "ready_to_apply_count": ready_count,
            "rejected_count": rejected_count,
            "jobs": processed,
        }
