from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient

from src import main as app_main
from src.agent.portals.generic_ats import fill_resolved_form_fields
from src.brain.profile_planner import generate_profile_search_blueprint
from src.brain.scorer import deterministic_keyword_score
from src.brain.verifier_agent import verify_and_filter_jobs_conversational
from src.db.profile_store import ProfileVaultStore
from src.db.supabase_client import SupabaseRepository
from src.ingestion.ats_scraper import fetch_greenhouse_board_jobs
from src.ingestion.company_registry import CompanyATSRegistry
from src.ingestion.ephemeral_browser_scout import harvest_ephemeral_guest_jobs
from src.ingestion.jd_enrichment import (
    build_profile_search_queries,
    is_role_relevant_to_profile,
    parse_and_check_recency,
)


async def test_agent0_profile_blueprint_generates_wfh_and_salary_urls(tmp_path: Path):
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    profile = store.load()
    profile["search_preferences"]["work_mode_preference"] = "remote_wfh"
    profile["search_preferences"]["min_salary_lpa"] = 15
    profile["search_preferences"]["experience_years_range"] = "1-3"
    profile["search_preferences"]["target_roles"] = ["GenAI Engineer", "AI Systems Engineer"]

    bp = await generate_profile_search_blueprint(profile)
    assert "GenAI Engineer" in bp["persona_headline"]
    assert bp["work_mode_preference"] == "remote_wfh"
    assert any("wfhType=2" in url and "ctcFilter=15to25" in url for url in bp["naukri_urls"])
    assert any("f_WT=2" in url for url in bp["linkedin_guest_urls"])


async def test_ephemeral_guest_scout_rotates_clean_contexts_and_aborts_on_captcha():
    visited_urls: list[str] = []
    contexts_cleared = 0
    contexts_closed = 0

    class FakePage:
        def __init__(self, url: str) -> None:
            self.url = url

        async def goto(self, url: str, wait_until: str = "domcontentloaded", timeout: int = 15000) -> None:
            visited_urls.append(url)
            if "blocked" in url:
                self.url = "https://www.naukri.com/captcha/challenge"
            else:
                self.url = url

        async def title(self) -> str:
            return "Verify you are human" if "captcha" in self.url else "AI Engineer Jobs"

        async def evaluate(self, _script: str):
            return [
                {
                    "job_url": "https://www.naukri.com/job-listings-genai-engineer-remote-987654321",
                    "title": "GenAI Engineer (Remote)",
                    "company": "CloudScale AI",
                    "location": "Remote",
                    "description": "Salary: 18-24 LPA | Build RAG and LLM agents.",
                    "site": "naukri",
                    "discovery_source": "ephemeral_guest:naukri",
                }
            ]

    class FakeContext:
        def __init__(self) -> None:
            self._page: FakePage | None = None

        async def clear_cookies(self) -> None:
            nonlocal contexts_cleared
            contexts_cleared += 1

        async def new_page(self) -> FakePage:
            self._page = FakePage("")
            return self._page

        async def close(self) -> None:
            nonlocal contexts_closed
            contexts_closed += 1

    class FakeBrowser:
        async def new_context(self, **_kwargs) -> FakeContext:
            return FakeContext()

        async def close(self) -> None:
            return None

    class FakePlaywright:
        def __init__(self) -> None:
            self.chromium = self

        async def launch(self, **_kwargs) -> FakeBrowser:
            return FakeBrowser()

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_args):
            return None

    bp = {
        "naukri_urls": [
            "https://www.naukri.com/ai-engineer-jobs?wfhType=2",
            "https://www.naukri.com/blocked-jobs",
        ],
        "linkedin_guest_urls": [],
    }
    jobs = await harvest_ephemeral_guest_jobs(blueprint=bp, playwright_factory=FakePlaywright)
    assert len(visited_urls) == 2
    assert contexts_cleared == 4
    assert contexts_closed == 2
    assert len(jobs) == 1
    assert jobs[0]["external_job_id"] == "987654321"
    assert jobs[0]["portal"] == "naukri"


async def test_agent2_conversational_verifier_filters_by_profile_and_wfh():
    profile = {
        "demographics": {"preferred_locations": ["Bengaluru", "Remote"]},
        "search_preferences": {"work_mode_preference": "remote_wfh", "min_salary_lpa": 15},
    }
    jobs = [
        {
            "id": "job-wfh-1",
            "company_name": "VectorAI",
            "role_title": "GenAI Engineer",
            "location": "Remote",
            "jd_raw_text": "18-24 LPA. 100% Remote WFH role building Python, FastAPI, RAG, and LangGraph systems.",
            "match_score": 92.0,
            "match_reasoning": {"hard_blocker_detected": False},
        },
        {
            "id": "job-onsite-2",
            "company_name": "OfficeOnlyCorp",
            "role_title": "Python Engineer",
            "location": "Pune",
            "jd_raw_text": "Mandatory work from office 5 days a week. In-office collaboration.",
            "match_score": 88.0,
            "match_reasoning": {"hard_blocker_detected": False},
        },
    ]

    result = await verify_and_filter_jobs_conversational(
        user_message="Only keep WFH remote GenAI roles matching my profile",
        jobs=jobs,
        profile=profile,
    )
    assert result["verified_job_ids"] == ["job-wfh-1"]
    assert result["rejected_job_ids"] == ["job-onsite-2"]


def test_end_to_end_5_step_autonomous_api_flow(monkeypatch, tmp_path: Path):
    repo = SupabaseRepository(client=None)
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    reg = CompanyATSRegistry(registry_path=tmp_path / "registry.json")

    monkeypatch.setattr(app_main.orchestrator, "repo", repo)
    monkeypatch.setattr(app_main.orchestrator, "_checkpoints", {})
    monkeypatch.setattr(app_main, "profile_store", store)
    monkeypatch.setattr(app_main, "company_registry", reg)
    monkeypatch.setattr(app_main, "OUTPUT_RESUMES_DIR", tmp_path / "resumes")
    monkeypatch.setattr(app_main, "OUTPUT_SCREENSHOTS_DIR", tmp_path / "screenshots")
    monkeypatch.setattr("src.brain.scorer.settings.gemini_api_key", "")
    monkeypatch.setattr("src.pipeline_discovery.compile_tex_to_pdf", MagicMock(side_effect=FileNotFoundError()))

    morning_ist = datetime(2026, 11, 2, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    monkeypatch.setattr(app_main.circuit_breaker, "is_within_circadian_window", lambda now=None: True)
    monkeypatch.setattr(
        app_main.circuit_breaker,
        "can_execute",
        lambda portal, now=None: (morning_ist.hour == 11, "OK"),
    )

    monkeypatch.setattr("src.pipeline_discovery.harvest_jobs_unauthenticated", lambda **kwargs: [])
    monkeypatch.setattr("src.pipeline_discovery.harvest_ats_search_dorks", lambda **kwargs: [])
    monkeypatch.setattr(
        "src.pipeline_discovery.harvest_direct_ats_boards",
        lambda **kwargs: [
            {
                "canonical_url": "https://boards.greenhouse.io/acmeai/jobs/111222",
                "url_hash": "hash-acme-111222",
                "external_job_id": "111222",
                "discovery_source": "ats_registry:greenhouse",
                "portal": "greenhouse",
                "company_name": "Acme AI",
                "role_title": "GenAI & Python Engineer",
                "location": "Remote",
                "jd_raw_text": "100% Remote WFH role. Build Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, Docker.",
                "status": "DISCOVERED",
            },
            {
                "canonical_url": "https://jobs.lever.co/zepto/333444",
                "url_hash": "hash-zepto-333444",
                "external_job_id": "333444",
                "discovery_source": "ats_registry:lever",
                "portal": "lever",
                "company_name": "Zepto",
                "role_title": "Voice AI Engineer",
                "location": "Remote",
                "jd_raw_text": "Remote role in Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, Voice AI.",
                "status": "DISCOVERED",
            },
        ],
    )

    with TestClient(app_main.app) as client:
        # Step 1: Start Autonomous Search (Agent 0 Blueprint + Agent 1 Discovery)
        start_res = client.post(
            "/api/v1/pipeline/start-autonomous-search",
            json={"use_ephemeral_chromium": False, "sweep_company_registry": True, "run_ats_dorks": False},
        )
        assert start_res.status_code == 200
        assert start_res.json()["discovery"]["discovered_count"] == 2
        assert "executive_summary" in start_res.json()["blueprint"]

        # Step 2: Agent 2 Conversational Round-2 Verify
        chat_res = client.post(
            "/api/v1/agent/chat-verify",
            json={"message": "Filter best remote AI roles according to my profile"},
        )
        assert chat_res.status_code == 200
        assert chat_res.json()["verified_count"] == 2

        # Step 3: Apply to Verified Batch -> Acme AI applies autonomously, Zepto (Dream Company) pauses for manual/HITL
        batch_res = client.post(
            "/api/v1/orchestrator/apply-verified-batch",
            json={"live_browser": False},
        )
        assert batch_res.status_code == 200
        assert batch_res.json()["applied_count"] == 1
        assert batch_res.json()["manual_trouble_count"] == 1

        # Step 4: Check Needs Manual Apply queue -> Mark Zepto Applied Manually
        manual_res = client.get("/api/v1/jobs/manual-queue")
        assert manual_res.status_code == 200
        assert manual_res.json()["count"] == 1
        zepto_item = manual_res.json()["jobs"][0]
        assert zepto_item["company_name"] == "Zepto"

        mark_res = client.post(f"/api/v1/jobs/{zepto_item['id']}/mark-applied")
        assert mark_res.status_code == 200
        assert mark_res.json()["status"] == "APPLIED"

        # Step 5: Check Applied Today tab endpoint
        today_res = client.get("/api/v1/jobs/applied-today")
        assert today_res.status_code == 200
        assert today_res.json()["today_count"] == 2
        assert today_res.json()["total_applied_count"] == 2


def test_word_boundary_experience_guard_and_city_deduplication(tmp_path: Path):
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    profile = store.load()
    profile["demographics"]["preferred_locations"] = ["Hyderabad", "Bangalore", "Remote"]
    profile["search_preferences"]["locations"] = ["Hyderabad, Telangana, India", "Bengaluru, Karnataka, India"]
    profile["search_preferences"]["experience_years_range"] = "0-2"

    # City deduplication avoids duplicate queries for Hyderabad / Bengaluru
    _, locs = build_profile_search_queries(profile)
    assert locs == ["Hyderabad", "Bangalore", "Remote"]

    # Non-AI/non-software generic Engineer role must be rejected by pre-filter
    assert is_role_relevant_to_profile("Engineer, IAM with GCP & Azure (must have)", "Hyderabad", profile) is False
    assert is_role_relevant_to_profile("Generative AI Engineer", "Hyderabad", profile) is True

    # Word-boundary matching: 'available'/'storage'/'leverage'/'maintain' must NOT trigger 'ai' or 'rag'
    wb_res = deterministic_keyword_score(
        "Maintain scalable storage and available leverage infrastructure.",
        profile,
        role_title="Infrastructure Operations Specialist",
    )
    assert "ai" not in wb_res.matched_skills
    assert "rag" not in wb_res.matched_skills
    assert wb_res.match_score < 65.0

    # Seniority/experience guard: Sr. AI Engineer blocked for 0-2 yrs profile
    sr_res = deterministic_keyword_score(
        "Maintain scalable storage and available cloud infrastructure with 6+ years experience.",
        profile,
        role_title="Sr. AI Engineer",
    )
    assert sr_res.hard_blocker_detected is True
    assert "Seniority mismatch" in sr_res.blocker_reason
    assert sr_res.match_score < 50.0

    # Valid early-career GenAI role matches cleanly via whole words & profile skills
    jr_res = deterministic_keyword_score(
        "Build production AI agents, LLM RAG workflows, FastAPI APIs, PostgreSQL pgvector, and GCP microservices.",
        profile,
        role_title="Generative AI Engineer",
    )
    assert jr_res.hard_blocker_detected is False
    assert jr_res.match_score >= 80.0
    assert {"ai", "llm", "rag", "fastapi"}.issubset(set(jr_res.matched_skills))


def test_background_search_and_status_endpoint(monkeypatch, tmp_path: Path):
    repo = SupabaseRepository(client=None)
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    reg = CompanyATSRegistry(registry_path=tmp_path / "registry.json")

    monkeypatch.setattr(app_main.orchestrator, "repo", repo)
    monkeypatch.setattr(app_main, "profile_store", store)
    monkeypatch.setattr(app_main, "company_registry", reg)
    monkeypatch.setattr(app_main, "OUTPUT_RESUMES_DIR", tmp_path / "resumes")
    monkeypatch.setattr("src.brain.scorer.settings.gemini_api_key", "")
    monkeypatch.setattr("src.pipeline_discovery.compile_tex_to_pdf", MagicMock(side_effect=FileNotFoundError()))
    monkeypatch.setattr("src.pipeline_discovery.harvest_jobs_unauthenticated", lambda **kwargs: [])
    monkeypatch.setattr("src.pipeline_discovery.harvest_ats_search_dorks", lambda **kwargs: [])
    monkeypatch.setattr(
        "src.pipeline_discovery.harvest_direct_ats_boards",
        lambda **kwargs: [
            {
                "canonical_url": "https://boards.greenhouse.io/acmeai/jobs/991100",
                "url_hash": "hash-bg-991100",
                "external_job_id": "991100",
                "discovery_source": "ats_registry:greenhouse",
                "portal": "greenhouse",
                "company_name": "Acme AI",
                "role_title": "GenAI Engineer",
                "location": "Remote",
                "jd_raw_text": "Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, Docker.",
                "status": "DISCOVERED",
            }
        ],
    )

    with TestClient(app_main.app) as client:
        res = client.post(
            "/api/v1/pipeline/start-autonomous-search",
            json={
                "use_ephemeral_chromium": False,
                "sweep_company_registry": True,
                "run_ats_dorks": False,
                "background": True,
            },
        )
        assert res.status_code == 200
        body = res.json()
        assert body["status"] in ("STARTED", "ALREADY_RUNNING")

        status_data = client.get("/api/v1/pipeline/search-status").json()
        for _ in range(25):
            if not status_data.get("running"):
                break
            time.sleep(0.05)
            status_data = client.get("/api/v1/pipeline/search-status").json()

        assert status_data["running"] is False
        assert status_data["phase"] == "completed"
        assert status_data["discovered_count"] == 1
        assert status_data["ready_to_apply_count"] == 1


def test_7_day_recency_filter_and_greenhouse_enforcement():
    now = datetime(2026, 11, 25, 10, 0, tzinfo=timezone.utc)
    ok_recent, iso_recent = parse_and_check_recency((now - timedelta(days=2)).isoformat(), max_age_days=7, now=now)
    ok_old, _ = parse_and_check_recency((now - timedelta(days=14)).isoformat(), max_age_days=7, now=now)
    ok_rel, _ = parse_and_check_recency("3 days ago", max_age_days=7, now=now)
    ok_old_rel, _ = parse_and_check_recency("30+ days ago", max_age_days=7, now=now)
    assert ok_recent is True and iso_recent is not None
    assert ok_old is False
    assert ok_rel is True
    assert ok_old_rel is False

    recent_iso = (datetime.now(timezone.utc) - timedelta(days=2)).isoformat()
    stale_iso = (datetime.now(timezone.utc) - timedelta(days=21)).isoformat()

    class MockResp:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "jobs": [
                    {
                        "id": 101,
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/101",
                        "title": "AI Engineer",
                        "location": {"name": "Remote"},
                        "updated_at": recent_iso,
                        "content": "<p>Python AI LLM</p>",
                    },
                    {
                        "id": 102,
                        "absolute_url": "https://boards.greenhouse.io/acme/jobs/102",
                        "title": "Old AI Engineer",
                        "location": {"name": "Remote"},
                        "updated_at": stale_iso,
                        "content": "<p>Python AI LLM</p>",
                    },
                ]
            }

    class MockClient:
        def get(self, _url: str, timeout: float = 15.0):
            return MockResp()

    gh_jobs = fetch_greenhouse_board_jobs("acme", client=MockClient(), max_age_days=7)
    assert len(gh_jobs) == 1
    assert gh_jobs[0]["external_job_id"] == "101"
    assert gh_jobs[0]["posted_at"] is not None


async def test_bracketed_field_id_selector_and_clear_and_download_resume(monkeypatch, tmp_path: Path):
    used_selectors: list[str] = []

    class FakeLoc:
        def __init__(self) -> None:
            self.first = self

        async def count(self) -> int:
            return 1

        async def bounding_box(self) -> dict[str, float]:
            return {"x": 10.0, "y": 10.0, "width": 100.0, "height": 30.0}

        async def click(self) -> None:
            return None

    class FakeMouse:
        async def move(self, _x: float, _y: float) -> None:
            return None

    class FakeKeyboard:
        async def type(self, _char: str, delay: float = 0.0) -> None:
            return None

    class FakePage:
        def __init__(self) -> None:
            self.mouse = FakeMouse()
            self.keyboard = FakeKeyboard()

        def locator(self, selector: str) -> FakeLoc:
            used_selectors.append(selector)
            return FakeLoc()

    await fill_resolved_form_fields(
        FakePage(),
        [
            {
                "field_id": "job_application[first_name]",
                "label_text": "First Name",
                "field_type": "text",
                "options": None,
                "proposed_value": "Ajju",
                "confidence": 0.95,
                "reasoning": "Vault",
                "bounding_box": None,
            }
        ],
    )
    assert any('[name="job_application[first_name]"]' in s for s in used_selectors)

    repo = SupabaseRepository(client=None)
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    prof = store.load()
    prof["demographics"]["full_name"] = "Ajju Bhaiya"
    store.save(prof)

    monkeypatch.setattr(app_main.orchestrator, "repo", repo)
    monkeypatch.setattr(app_main, "profile_store", store)
    monkeypatch.setattr(app_main, "OUTPUT_RESUMES_DIR", tmp_path / "resumes")
    monkeypatch.setattr("src.pipeline_discovery.compile_tex_to_pdf", MagicMock(side_effect=FileNotFoundError()))

    repo.upsert_job(
        {
            "id": "job-ready-1",
            "canonical_url": "https://boards.greenhouse.io/a/jobs/1",
            "url_hash": "h1",
            "portal": "greenhouse",
            "company_name": "Alpha",
            "role_title": "AI Engineer",
            "location": "Remote",
            "status": "READY_TO_APPLY",
        }
    )
    repo.upsert_job(
        {
            "id": "job-applied-1",
            "canonical_url": "https://boards.greenhouse.io/b/jobs/2",
            "url_hash": "h2",
            "portal": "greenhouse",
            "company_name": "Beta",
            "role_title": "GenAI Engineer",
            "location": "Remote",
            "status": "APPLIED",
        }
    )

    with TestClient(app_main.app) as client:
        dl_res = client.get("/api/v1/profile/preview-resume/pdf?download=true")
        assert dl_res.status_code == 200
        assert "attachment" in dl_res.headers.get("content-disposition", "")
        assert "Ajju_Bhaiya_Resume.pdf" in dl_res.headers.get("content-disposition", "")

        clear_keep = client.post("/api/v1/jobs/clear", json={"keep_applied": True})
        assert clear_keep.status_code == 200
        assert clear_keep.json()["deleted_count"] == 1
        remaining = client.get("/api/v1/jobs").json()["jobs"]
        assert len(remaining) == 1
        assert remaining[0]["status"] == "APPLIED"

        clear_all = client.post("/api/v1/jobs/clear", json={"keep_applied": False})
        assert clear_all.status_code == 200
        assert clear_all.json()["deleted_count"] == 1
        assert client.get("/api/v1/jobs").json()["count"] == 0
