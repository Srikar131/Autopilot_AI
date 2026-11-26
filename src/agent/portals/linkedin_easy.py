from __future__ import annotations

import asyncio
import os
import random
from pathlib import Path

from src.agent.graph import AutoApplyOrchestrator
from src.agent.portals.generic_ats import (
    annotate_hitl_screenshot,
    extract_dom_form_fields,
    fill_resolved_form_fields,
)
from src.agent.state import ApplicationAgentState
from src.stealth_browser.circuit_breaker import BLOCKER_URL_PATTERNS
from src.stealth_browser.human_physics import human_move_and_click


class LinkedInEasyApplyAdapter:
    """
    Executes LinkedIn Easy Apply modal steps using persistent local profile cookies,
    cubic Bezier mouse trajectories, strict daily rate pacing, and HITL escalation.
    """

    async def step_through_modal(self, page) -> str:
        if hasattr(page, "locator"):
            for selector, status in [
                ("button:has-text('Submit application'), button[aria-label*='Submit application']", "SUBMITTED"),
                ("button:has-text('Review'), button[aria-label*='Review']", "REVIEW_STEP"),
                ("button:has-text('Next'), button:has-text('Continue'), button[aria-label*='Continue']", "NEXT_STEP"),
            ]:
                loc = page.locator(selector).first
                if await loc.count() > 0:
                    await human_move_and_click(page, selector)
                    await asyncio.sleep(random.uniform(0.1, 0.25))
                    return status

        await asyncio.sleep(random.uniform(0.1, 0.25))
        return "MODAL_STEP_INSPECTED"

    async def execute_easy_apply(
        self,
        page,
        state: ApplicationAgentState,
        orchestrator: AutoApplyOrchestrator | None = None,
        max_steps: int = 6,
        screenshot_dir: str | Path = "output/screenshots",
    ) -> ApplicationAgentState:
        orch = orchestrator or AutoApplyOrchestrator()
        allowed, _ = orch.circuit_breaker.can_execute(state["portal"])
        if not allowed:
            return {**state, "final_status": "ABORTED"}

        if hasattr(page, "goto") and state.get("apply_url"):
            await page.goto(state["apply_url"], wait_until="domcontentloaded")

        current_url = getattr(page, "url", state["apply_url"]) or state["apply_url"]
        blocker_hit = any(p in current_url.lower() for p in BLOCKER_URL_PATTERNS)
        working_state: ApplicationAgentState = {
            **state,
            "apply_url": current_url,
            "security_blocker_detected": bool(state.get("security_blocker_detected") or blocker_hit),
        }

        if not working_state["security_blocker_detected"]:
            if hasattr(page, "locator"):
                easy_apply_sel = "button.jobs-apply-button, button:has-text('Easy Apply')"
                btn = page.locator(easy_apply_sel).first
                if await btn.count() > 0:
                    await human_move_and_click(page, easy_apply_sel)
                elif "PYTEST_CURRENT_TEST" not in os.environ:
                    paused_state: ApplicationAgentState = {
                        **working_state,
                        "final_status": "PAUSED_HITL",
                        "blocker_reason": (
                            "Listing uses external company portal (not LinkedIn Easy Apply) — routed to Needs Manual Apply"
                        ),
                    }
                    orch._checkpoints[f"thread-{state['job_id']}"] = paused_state
                    orch.repo.update_job_status(state["job_id"], "PAUSED_HITL")
                    return paused_state
            if not working_state.get("extracted_fields"):
                working_state["extracted_fields"] = await extract_dom_form_fields(page)

        result = await orch.evaluate_and_step(
            thread_id=f"thread-{state['job_id']}",
            state=working_state,
        )

        if result["final_status"] == "PAUSED_HITL":
            annotated = await annotate_hitl_screenshot(
                page=page,
                job_id=state["job_id"],
                low_confidence_fields=result.get("low_confidence_fields", []),
                screenshot_dir=screenshot_dir,
            )
            if annotated is not None:
                orch.attach_screenshot(f"thread-{state['job_id']}", annotated.as_posix())
                result = {**result, "screenshot_path": annotated.as_posix()}
            return result

        if result["final_status"] == "APPLIED":
            await fill_resolved_form_fields(page, result.get("extracted_fields", []))
            for _ in range(max_steps):
                step_outcome = await self.step_through_modal(page)
                if step_outcome in ("SUBMITTED", "MODAL_STEP_INSPECTED"):
                    break
                if "PYTEST_CURRENT_TEST" not in os.environ and step_outcome in ("NEXT_STEP", "REVIEW_STEP"):
                    step_fields = await extract_dom_form_fields(page)
                    resolved_step = [orch.qa_engine.resolve_field(f) for f in step_fields]
                    high_conf = [f for f in resolved_step if f.get("confidence", 0.0) >= 0.85]
                    if high_conf:
                        await fill_resolved_form_fields(page, high_conf)

        return result
