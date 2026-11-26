from __future__ import annotations

import asyncio
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from src.agent.graph import AutoApplyOrchestrator
from src.agent.portals.generic_ats import DirectATSAdapter, fill_resolved_form_fields
from src.agent.portals.linkedin_easy import LinkedInEasyApplyAdapter
from src.agent.portals.naukri_apply import NaukriPortalAdapter
from src.agent.state import ApplicationAgentState, FormFieldSpec
from src.hitl.image_annotator import render_hitl_preview_card
from src.stealth_browser.browser_factory import inspect_portal_profiles, launch_stealth_context
from src.stealth_browser.human_physics import human_move_and_click


def _default_screening_fields_for_job(job: dict[str, Any]) -> list[FormFieldSpec]:
    """
    Provides structured default fields for dry-run / supervised simulation when no live DOM was scraped.
    """
    fields: list[FormFieldSpec] = [
        {
            "field_id": "notice_period",
            "label_text": "What is your notice period?",
            "field_type": "text",
            "options": None,
            "proposed_value": None,
            "confidence": 0.0,
            "reasoning": "",
            "bounding_box": {"x": 80, "y": 140, "width": 360, "height": 42},
        },
        {
            "field_id": "work_auth_india",
            "label_text": "Are you legally authorized to work in India?",
            "field_type": "select",
            "options": ["Yes", "No"],
            "proposed_value": None,
            "confidence": 0.0,
            "reasoning": "",
            "bounding_box": {"x": 80, "y": 210, "width": 360, "height": 42},
        },
    ]
    company = str(job.get("company_name", "")).lower()
    if any(name in company for name in ("zepto", "anthropic", "openai", "cred", "google")):
        fields.append(
            {
                "field_id": f"why_{company.split()[0]}",
                "label_text": f"Why do you want to work at {job.get('company_name', 'our company')}?",
                "field_type": "textarea",
                "options": None,
                "proposed_value": None,
                "confidence": 0.0,
                "reasoning": "",
                "bounding_box": {"x": 80, "y": 290, "width": 520, "height": 110},
            }
        )
    return fields


class PortalApplicationExecutor:
    """Routes queued jobs to their portal adapter, either in live stealth browser or dry-run evaluation."""

    def __init__(self, orchestrator: AutoApplyOrchestrator) -> None:
        self.orchestrator = orchestrator
        self.direct_ats = DirectATSAdapter()
        self.linkedin = LinkedInEasyApplyAdapter()
        self.naukri = NaukriPortalAdapter()
        self._portal_locks: dict[str, asyncio.Lock] = {}

    async def _park_trouble_job(
        self,
        thread_id: str,
        state: ApplicationAgentState,
        reason: str,
        screenshot_dir: str | Path,
    ) -> ApplicationAgentState:
        preview_path = Path(screenshot_dir) / f"{state['job_id']}_annotated.png"
        render_hitl_preview_card(
            output_path=preview_path,
            company_name=state["company_name"],
            role_title=state["role_title"],
            portal=state["portal"],
            low_confidence_fields=state.get("low_confidence_fields", []),
            is_dream=bool(state.get("is_dream_company")),
            security_blocker=True,
        )
        parked: ApplicationAgentState = {
            **state,
            "final_status": "PAUSED_HITL",
            "blocker_reason": reason,
            "screenshot_path": preview_path.as_posix(),
        }
        self.orchestrator._checkpoints[thread_id] = parked
        self.orchestrator.repo.update_job_status(state["job_id"], "PAUSED_HITL")
        self.orchestrator.repo.record_application_event(
            {
                "job_id": state["job_id"],
                "langgraph_thread_id": thread_id,
                "event_type": "PAUSED_HITL",
                "low_confidence_fields": state.get("low_confidence_fields", []),
                "screenshot_path": preview_path.as_posix(),
            }
        )
        return parked

    async def execute_job(
        self,
        job: dict[str, Any],
        candidate_profile: dict[str, Any] | None = None,
        live_browser: bool = False,
        headless: bool = False,
        force_supervised_hitl: bool = False,
        sample_fields: list[FormFieldSpec] | None = None,
        now: Any | None = None,
        screenshot_dir: str | Path = "output/screenshots",
        worker_slot: int | None = None,
    ) -> ApplicationAgentState:
        if candidate_profile is not None:
            self.orchestrator.qa_engine.profile = candidate_profile

        job_id = str(job["id"])
        portal = str(job.get("portal", "other_ats"))
        thread_id = f"thread-{job_id}"

        state: ApplicationAgentState = {
            "job_id": job_id,
            "portal": portal,
            "apply_url": str(job.get("canonical_url", "")),
            "company_name": str(job.get("company_name", "Unknown Company")),
            "role_title": str(job.get("role_title", "Software / AI Engineer")),
            "tailored_pdf_path": str(job.get("tailored_pdf_path") or ""),
            "jd_raw_text": str(job.get("jd_raw_text") or ""),
            "is_dream_company": bool(job.get("is_dream_company", False)),
            "current_step_index": 0,
            "extracted_fields": list(sample_fields)
            if sample_fields is not None
            else ([] if live_browser else _default_screening_fields_for_job(job)),
            "low_confidence_fields": [],
            "security_blocker_detected": False,
            "human_resolution": None,
            "final_status": "IN_PROGRESS",
            "force_supervised_hitl": force_supervised_hitl,
        }

        if live_browser:
            if portal in ("linkedin_easy", "naukri"):
                prof_status = inspect_portal_profiles([portal])
                if prof_status and not prof_status[0].get("session_initialized"):
                    return await self._park_trouble_job(
                        thread_id=thread_id,
                        state=state,
                        reason=f"{portal} login session not connected yet. Sign in under Accounts & Anti-Ban or apply manually.",
                        screenshot_dir=screenshot_dir,
                    )

            try:
                lock = (
                    self._portal_locks.setdefault(portal, asyncio.Lock())
                    if portal in ("linkedin_easy", "naukri", "instahyre")
                    else nullcontext()
                )
                async with lock:
                    async with launch_stealth_context(
                        portal, headless=headless, worker_slot=worker_slot
                    ) as context:
                        page = await context.new_page()
                        if portal == "linkedin_easy":
                            result = await self.linkedin.execute_easy_apply(
                                page=page,
                                state=state,
                                orchestrator=self.orchestrator,
                                screenshot_dir=screenshot_dir,
                            )
                        elif portal == "naukri":
                            result = await self.naukri.execute_application(
                                page=page,
                                state=state,
                                orchestrator=self.orchestrator,
                                screenshot_dir=screenshot_dir,
                            )
                        else:
                            result = await self.direct_ats.execute_application(
                                page=page,
                                state=state,
                                orchestrator=self.orchestrator,
                                candidate_profile=candidate_profile,
                                screenshot_dir=screenshot_dir,
                            )
                        return result
            except Exception as exc:
                return await self._park_trouble_job(
                    thread_id=thread_id,
                    state=state,
                    reason=f"Live browser automation hit trouble: {exc}",
                    screenshot_dir=screenshot_dir,
                )

        result = await self.orchestrator.evaluate_and_step(thread_id=thread_id, state=state, now=now)
        if result["final_status"] == "PAUSED_HITL" and not result.get("screenshot_path"):
            preview_path = Path(screenshot_dir) / f"{job_id}_annotated.png"
            render_hitl_preview_card(
                output_path=preview_path,
                company_name=result["company_name"],
                role_title=result["role_title"],
                portal=result["portal"],
                low_confidence_fields=result.get("low_confidence_fields", []),
                is_dream=bool(result.get("is_dream_company")),
                security_blocker=bool(result.get("security_blocker_detected")),
            )
            self.orchestrator.attach_screenshot(thread_id, preview_path.as_posix())
            result = {**result, "screenshot_path": preview_path.as_posix()}

        return result

    async def submit_resumed_in_browser(
        self,
        state: ApplicationAgentState,
        candidate_profile: dict[str, Any],
        headless: bool = False,
    ) -> ApplicationAgentState:
        """Executes final browser fill + submit for a thread approved via HITL."""
        portal = state["portal"]
        demo = candidate_profile.get("demographics", {})
        async with launch_stealth_context(portal, headless=headless) as context:
            page = await context.new_page()
            if state.get("apply_url"):
                await page.goto(state["apply_url"], wait_until="domcontentloaded")
            if portal == "linkedin_easy":
                easy_sel = "button.jobs-apply-button, button:has-text('Easy Apply')"
                if await page.locator(easy_sel).first.count() > 0:
                    await human_move_and_click(page, easy_sel)
                await fill_resolved_form_fields(page, state.get("extracted_fields", []))
                for _ in range(6):
                    outcome = await self.linkedin.step_through_modal(page)
                    if outcome in ("SUBMITTED", "MODAL_STEP_INSPECTED"):
                        break
            elif portal == "naukri":
                apply_sel = "#apply-button, button:has-text('Apply'), .apply-button"
                if await page.locator(apply_sel).first.count() > 0:
                    await human_move_and_click(page, apply_sel)
                await fill_resolved_form_fields(page, state.get("extracted_fields", []))
                submit_sel = "button:has-text('Submit'), button[type='submit']"
                if await page.locator(submit_sel).first.count() > 0:
                    await human_move_and_click(page, submit_sel)
            else:
                await self.direct_ats.fill_and_upload_resume(
                    page=page,
                    candidate_name=str(demo.get("full_name", "Srikar Vaka")),
                    email=str(demo.get("email", "srikar.vaka@example.com")),
                    phone=str(demo.get("phone", "+91-9876543210")),
                    pdf_path=Path(state["tailored_pdf_path"]),
                    linkedin_url=str(demo.get("linkedin_url", "")),
                    github_url=str(demo.get("github_url", "")),
                    portfolio_url=str(demo.get("portfolio_url", "")),
                )
                await fill_resolved_form_fields(page, state.get("extracted_fields", []))
                submit_sel = "button[type='submit'], input[type='submit']"
                if await page.locator(submit_sel).first.count() > 0:
                    await human_move_and_click(page, submit_sel)
        return state
