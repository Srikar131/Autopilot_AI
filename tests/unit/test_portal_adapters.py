from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pytest
from PIL import Image

from src.agent.graph import AutoApplyOrchestrator
from src.agent.portals.generic_ats import DirectATSAdapter
from src.agent.portals.linkedin_easy import LinkedInEasyApplyAdapter
from src.agent.portals.naukri_apply import NaukriPortalAdapter
from src.agent.state import ApplicationAgentState
from src.db.supabase_client import SupabaseRepository
from src.hitl.telegram_bot import TelegramHITLNotifier
from src.stealth_browser.circuit_breaker import PortalCircuitBreaker
from src.stealth_browser.human_physics import human_move_and_click, human_type_text


class FakeMouse:
    def __init__(self) -> None:
        self.moves: list[tuple[float, float]] = []

    async def move(self, x: float, y: float) -> None:
        self.moves.append((x, y))


class FakeKeyboard:
    def __init__(self) -> None:
        self.typed: list[tuple[str, float]] = []

    async def type(self, char: str, delay: float = 0.0) -> None:
        self.typed.append((char, delay))


class FakeLocator:
    def __init__(self, page: "FakePage", selector: str, present: bool = True) -> None:
        self.page = page
        self.selector = selector
        self.present = present
        self.first = self

    async def count(self) -> int:
        if callable(self.page.selector_present_fn):
            return 1 if self.page.selector_present_fn(self.selector, self.page.step) else 0
        return 1 if self.present else 0

    async def bounding_box(self) -> dict[str, float]:
        return {"x": 200.0, "y": 300.0, "width": 120.0, "height": 40.0}

    async def click(self) -> None:
        self.page.clicks.append(self.selector)
        if "Next" in self.selector or "Review" in self.selector:
            self.page.step += 1

    async def set_input_files(self, file_path: str) -> None:
        self.page.uploaded_files.append(file_path)

    async def select_option(self, value: str) -> None:
        self.page.selected_options.append((self.selector, value))


class FakePage:
    def __init__(
        self,
        url: str = "https://boards.greenhouse.io/acme/jobs/1",
        extracted_fields=None,
        selector_present_fn=None,
    ) -> None:
        self.url = url
        self.extracted_fields = extracted_fields
        self.selector_present_fn = selector_present_fn
        self.step = 0
        self.mouse = FakeMouse()
        self.keyboard = FakeKeyboard()
        self.clicks: list[str] = []
        self.uploaded_files: list[str] = []
        self.selected_options: list[tuple[str, str]] = []
        self.visited_urls: list[str] = []

    async def goto(self, url: str, wait_until: str = "domcontentloaded") -> None:
        self.visited_urls.append(url)
        if "redirect_to" not in url:
            self.url = url

    def locator(self, selector: str) -> FakeLocator:
        return FakeLocator(self, selector)

    async def screenshot(self, path: str) -> None:
        img = Image.new("RGB", (800, 600), color=(255, 255, 255))
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        img.save(path)


class AlwaysOpenCircuitBreaker(PortalCircuitBreaker):
    def can_execute(self, portal: str, now: datetime | None = None) -> tuple[bool, str]:
        morning = now or datetime(2026, 11, 2, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
        return super().can_execute(portal, now=morning)


def _make_orchestrator() -> AutoApplyOrchestrator:
    return AutoApplyOrchestrator(
        circuit_breaker=AlwaysOpenCircuitBreaker(),
        notifier=TelegramHITLNotifier(bot_token="", allowed_chat_id=0),
        repo=SupabaseRepository(client=None),
    )


def _base_state(job_id: str, portal: str, url: str, tmp_path: Path, **overrides) -> ApplicationAgentState:
    pdf_path = tmp_path / "resume.pdf"
    pdf_path.write_bytes(b"%PDF-1.4\n%%EOF\n")
    state: ApplicationAgentState = {
        "job_id": job_id,
        "portal": portal,
        "apply_url": url,
        "company_name": "Acme AI",
        "role_title": "AI Engineer",
        "tailored_pdf_path": str(pdf_path),
        "is_dream_company": False,
        "current_step_index": 0,
        "extracted_fields": [],
        "low_confidence_fields": [],
        "security_blocker_detected": False,
        "human_resolution": None,
        "final_status": "IN_PROGRESS",
    }
    state.update(overrides)
    return state


@pytest.mark.asyncio
async def test_human_move_and_click_and_type_use_bezier_and_gaussian():
    page = FakePage()
    pts = await human_move_and_click(page, "#submit-btn", start=(10.0, 10.0), steps=12)
    assert len(pts) == 12
    assert len(page.mouse.moves) == 12
    assert pts[-1] == (260.0, 320.0)
    assert page.clicks == ["#submit-btn"]

    await human_type_text(page, "#email", "abc")
    assert [c for c, _ in page.keyboard.typed] == ["a", "b", "c"]
    assert all(delay >= 28.0 for _, delay in page.keyboard.typed)


@pytest.mark.asyncio
async def test_direct_ats_adapter_autonomous_and_hitl_annotation(tmp_path: Path):
    adapter = DirectATSAdapter()
    orch = _make_orchestrator()

    # Case A: Autonomous submit with factual fields >= 0.85
    page_auto = FakePage(
        url="https://boards.greenhouse.io/acme/jobs/101",
        extracted_fields=[
            {
                "field_id": "notice_period",
                "label_text": "What is your notice period?",
                "field_type": "text",
                "options": None,
                "proposed_value": None,
                "confidence": 0.0,
                "reasoning": "",
                "bounding_box": {"x": 50, "y": 100, "width": 200, "height": 40},
            }
        ],
    )
    res_auto = await adapter.execute_application(
        page_auto,
        _base_state("ats-1", "greenhouse", "https://boards.greenhouse.io/acme/jobs/101", tmp_path),
        orchestrator=orch,
        screenshot_dir=tmp_path / "screenshots",
    )
    assert res_auto["final_status"] == "APPLIED"
    assert len(page_auto.uploaded_files) == 1
    assert "button[type='submit'], input[type='submit']" in page_auto.clicks

    # Case B: Low-confidence essay triggers PAUSED_HITL and red bounding box annotation
    page_hitl = FakePage(
        url="https://boards.greenhouse.io/zepto/jobs/102",
        extracted_fields=[
            {
                "field_id": "why_zepto",
                "label_text": "Why do you want to work here?",
                "field_type": "textarea",
                "options": None,
                "proposed_value": None,
                "confidence": 0.0,
                "reasoning": "",
                "bounding_box": {"x": 100, "y": 150, "width": 300, "height": 80},
            }
        ],
    )
    res_hitl = await adapter.execute_application(
        page_hitl,
        _base_state("ats-2", "greenhouse", "https://boards.greenhouse.io/zepto/jobs/102", tmp_path),
        orchestrator=orch,
        screenshot_dir=tmp_path / "screenshots",
    )
    assert res_hitl["final_status"] == "PAUSED_HITL"
    annotated_png = tmp_path / "screenshots" / "ats-2_annotated.png"
    assert annotated_png.exists()
    # Verify red highlight pixel (#FF1744 -> (255, 23, 68)) exists at border
    img = Image.open(annotated_png)
    assert img.getpixel((96, 146)) == (255, 23, 68)


@pytest.mark.asyncio
async def test_naukri_adapter_refresh_and_security_blocker(tmp_path: Path):
    adapter = NaukriPortalAdapter()
    orch = _make_orchestrator()

    profile_page = FakePage()
    refreshed = await adapter.refresh_daily_profile_headline(
        profile_page, circuit_breaker=orch.circuit_breaker
    )
    assert refreshed is True
    assert "https://www.naukri.com/mnjuser/profile" in profile_page.visited_urls
    assert len(profile_page.keyboard.typed) > 0

    blocked_page = FakePage(url="https://www.naukri.com/checkpoint/otp-verify")
    state_blocked = _base_state(
        "naukri-99",
        "naukri",
        "https://www.naukri.com/checkpoint/otp-verify",
        tmp_path,
    )
    res_blocked = await adapter.execute_application(
        blocked_page,
        state_blocked,
        orchestrator=orch,
        screenshot_dir=tmp_path / "screenshots",
    )
    assert res_blocked["final_status"] == "PAUSED_HITL"
    assert res_blocked["security_blocker_detected"] is True
    assert "naukri" in orch.circuit_breaker.tripped_portals


@pytest.mark.asyncio
async def test_linkedin_easy_apply_modal_steps_and_dream_company_hitl(tmp_path: Path):
    adapter = LinkedInEasyApplyAdapter()
    orch = _make_orchestrator()

    def selector_present(sel: str, step: int) -> bool:
        if "Easy Apply" in sel:
            return True
        if step == 0 and "Next" in sel:
            return True
        if step >= 1 and "Submit application" in sel:
            return True
        return "#notice_period" in sel

    page_li = FakePage(
        url="https://www.linkedin.com/jobs/view/555",
        extracted_fields=[
            {
                "field_id": "notice_period",
                "label_text": "What is your notice period?",
                "field_type": "text",
                "options": None,
                "proposed_value": None,
                "confidence": 0.0,
                "reasoning": "",
                "bounding_box": {"x": 60, "y": 80, "width": 200, "height": 30},
            }
        ],
        selector_present_fn=selector_present,
    )
    res_li = await adapter.execute_easy_apply(
        page_li,
        _base_state("li-1", "linkedin_easy", "https://www.linkedin.com/jobs/view/555", tmp_path),
        orchestrator=orch,
        screenshot_dir=tmp_path / "screenshots",
    )
    assert res_li["final_status"] == "APPLIED"
    assert any("Submit application" in c for c in page_li.clicks)

    # Dream company always forces PAUSED_HITL + screenshot
    dream_page = FakePage(url="https://www.linkedin.com/jobs/view/777")
    res_dream = await adapter.execute_easy_apply(
        dream_page,
        _base_state(
            "li-dream",
            "linkedin_easy",
            "https://www.linkedin.com/jobs/view/777",
            tmp_path,
            is_dream_company=True,
            company_name="Google",
        ),
        orchestrator=orch,
        screenshot_dir=tmp_path / "screenshots",
    )
    assert res_dream["final_status"] == "PAUSED_HITL"
    assert (tmp_path / "screenshots" / "li-dream_annotated.png").exists()
