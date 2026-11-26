from datetime import datetime
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from src import main as app_main
from src.db.supabase_client import SupabaseRepository


def test_scheduler_registers_required_ist_jobs():
    jobs = {job.id: job for job in app_main.scheduler.get_jobs()}
    assert set(jobs.keys()) == {
        "discovery_polling",
        "naukri_morning_refresh",
        "morning_application_wave",
        "afternoon_application_wave",
    }

    assert str(jobs["naukri_morning_refresh"].trigger.timezone) == "Asia/Kolkata"
    assert "hour='8'" in str(jobs["naukri_morning_refresh"].trigger)
    assert "minute='55'" in str(jobs["naukri_morning_refresh"].trigger)

    assert "hour='9'" in str(jobs["morning_application_wave"].trigger)
    assert "minute='15'" in str(jobs["morning_application_wave"].trigger)

    assert "hour='15'" in str(jobs["afternoon_application_wave"].trigger)
    assert "minute='0'" in str(jobs["afternoon_application_wave"].trigger)


@pytest.mark.asyncio
async def test_application_wave_respects_daily_quota_and_circuit_breaker(monkeypatch):
    repo = SupabaseRepository(client=None)
    monkeypatch.setattr(app_main.orchestrator, "repo", repo)
    app_main.circuit_breaker.tripped_portals.clear()
    monkeypatch.setattr(app_main.settings, "max_daily_linkedin", 1)

    for idx in range(2):
        repo.upsert_job(
            {
                "id": f"li-job-{idx}",
                "canonical_url": f"https://www.linkedin.com/jobs/view/{100 + idx}",
                "url_hash": f"hash-{idx}",
                "portal": "linkedin_easy",
                "company_name": "TestCorp",
                "role_title": "AI Engineer",
                "location": "Bengaluru",
                "jd_raw_text": "Python AI",
                "is_dream_company": False,
                "status": "READY_TO_APPLY",
                "tailored_pdf_path": "output/resumes/test.pdf",
            }
        )

    morning_ist = datetime(2026, 11, 2, 9, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    wave_res = await app_main.run_application_wave(
        wave_name="morning",
        portals=["linkedin_easy"],
        max_per_portal=5,
        now=morning_ist,
    )
    portal_res = wave_res["portals"]["linkedin_easy"]
    assert portal_res["processed"] == 1
    assert portal_res["status"] == "QUOTA_EXCEEDED"
    assert portal_res["daily_limit"] == 1


def test_fastapi_control_plane_endpoints(monkeypatch):
    repo = SupabaseRepository(client=None)
    monkeypatch.setattr(app_main.orchestrator, "repo", repo)
    app_main.circuit_breaker.tripped_portals.clear()
    monkeypatch.setattr(app_main.circuit_breaker, "is_within_circadian_window", lambda now=None: True)

    with TestClient(app_main.app) as client:
        health = client.get("/health")
        assert health.status_code == 200
        health_data = health.json()
        assert health_data["status"] == "healthy"
        assert set(health_data["scheduled_jobs"]) == {
            "discovery_polling",
            "naukri_morning_refresh",
            "morning_application_wave",
            "afternoon_application_wave",
        }

        batch = client.post(
            "/api/v1/orchestrator/trigger-batch",
            json={"portal": "greenhouse", "max_jobs": 5, "supervised_mode": True},
        )
        assert batch.status_code == 202
        assert batch.json()["status"] == "BATCH_QUEUED"

        trip = client.post(
            "/api/v1/circuit-breaker/trip",
            json={"portal": "greenhouse", "reason": "Test anomaly"},
        )
        assert trip.status_code == 200
        assert trip.json()["status"] == "CIRCUIT_BREAKER_TRIPPED"

        blocked = client.post(
            "/api/v1/orchestrator/trigger-batch",
            json={"portal": "greenhouse", "max_jobs": 5, "supervised_mode": True},
        )
        assert blocked.status_code == 202
        assert blocked.json()["status"] == "BLOCKED_BY_CIRCUIT_BREAKER"

        app_main.circuit_breaker.tripped_portals.clear()

        missing_resume = client.post(
            "/api/v1/hitl/resume",
            json={"langgraph_thread_id": "non-existent-thread", "action": "APPROVE_OR_EDIT"},
        )
        assert missing_resume.status_code == 404

        app_main.orchestrator._checkpoints["thread-api-1"] = {
            "job_id": "job-api-1",
            "portal": "greenhouse",
            "apply_url": "https://boards.greenhouse.io/acme/jobs/1",
            "company_name": "Acme",
            "role_title": "AI Engineer",
            "tailored_pdf_path": "output/resumes/job-api-1/resume.pdf",
            "is_dream_company": False,
            "current_step_index": 0,
            "extracted_fields": [
                {
                    "field_id": "why_acme",
                    "label_text": "Why Acme?",
                    "field_type": "textarea",
                    "options": None,
                    "proposed_value": "",
                    "confidence": 0.65,
                    "reasoning": "Needs human review",
                    "bounding_box": None,
                }
            ],
            "low_confidence_fields": [],
            "security_blocker_detected": False,
            "human_resolution": None,
            "final_status": "PAUSED_HITL",
        }
        resumed = client.post(
            "/api/v1/hitl/resume",
            json={
                "langgraph_thread_id": "thread-api-1",
                "action": "APPROVE_OR_EDIT",
                "field_overrides": {"why_acme": "I build scalable RAG and Voice AI systems."},
                "save_to_qa_cache": True,
            },
        )
        assert resumed.status_code == 200
        assert resumed.json()["final_status"] == "APPLIED"
        assert resumed.json()["qa_cache_entries_added"] == 1
