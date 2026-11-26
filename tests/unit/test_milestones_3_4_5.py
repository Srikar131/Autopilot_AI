import asyncio
from datetime import datetime
from zoneinfo import ZoneInfo

from src.agent.graph import AutoApplyOrchestrator
from src.agent.state import ApplicationAgentState
from src.brain.qa_rag import QAResolutionEngine
from src.brain.scorer import deterministic_keyword_score
from src.ingestion.gmail_ingestor import extract_job_links_from_email_bodies


def test_ingestion_and_gmail_alert_deduplication():
    emails = [
        "New job alert! Apply here: https://boards.greenhouse.io/razorpay/jobs/998877?utm_source=email and https://jobs.lever.co/zepto/112233",
        "Duplicate link in second email: https://boards.greenhouse.io/razorpay/jobs/998877/",
    ]
    extracted = extract_job_links_from_email_bodies(emails)
    assert len(extracted) == 2
    portals = {j["portal"] for j in extracted}
    assert portals == {"greenhouse", "lever"}


def test_jd_scorer_high_match_vs_blocker():
    good_jd = "We are hiring an AI Engineer experienced in Python, FastAPI, LangGraph, RAG, Supabase pgvector, and LLM agents."
    res_good = deterministic_keyword_score(good_jd)
    assert res_good.match_score >= 85.0
    assert res_good.hard_blocker_detected is False
    assert len(res_good.selected_project_titles) == 3

    blocked_jd = "Senior Principal Architect. Requires 12+ years of experience and US Citizens Only."
    res_blocked = deterministic_keyword_score(blocked_jd)
    assert res_blocked.hard_blocker_detected is True
    assert res_blocked.match_score < 50.0


def test_qa_rag_confidence_calibration():
    engine = QAResolutionEngine()

    # 1. Known factual question -> high confidence (>= 0.85)
    f_notice = engine.resolve_field({
        "field_id": "f1",
        "label_text": "What is your notice period / how soon can you join?",
        "field_type": "text",
        "options": None,
        "proposed_value": None,
        "confidence": 0.0,
        "reasoning": "",
        "bounding_box": None,
    })
    assert f_notice["confidence"] >= 0.90
    assert "15 Days" in f_notice["proposed_value"]

    # 2. Open-ended essay question -> must drop confidence below 0.85 to force HITL
    f_essay = engine.resolve_field({
        "field_id": "f2",
        "label_text": "Why do you want to work at Zepto and what excites you about our culture?",
        "field_type": "textarea",
        "options": None,
        "proposed_value": None,
        "confidence": 0.0,
        "reasoning": "",
        "bounding_box": None,
    })
    assert f_essay["confidence"] < 0.85


def test_orchestrator_autonomous_vs_hitl_pause_and_resume():
    orchestrator = AutoApplyOrchestrator()
    morning_ist = datetime(2026, 11, 2, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))

    # Case A: All factual fields -> Autonomous APPLIED
    state_auto: ApplicationAgentState = {
        "job_id": "job-101",
        "portal": "greenhouse",
        "apply_url": "https://boards.greenhouse.io/acme/jobs/101",
        "company_name": "Acme AI",
        "role_title": "AI Engineer",
        "tailored_pdf_path": "output/resumes/job-101/Srikar_Vaka_Resume.pdf",
        "is_dream_company": False,
        "current_step_index": 1,
        "extracted_fields": [
            {
                "field_id": "notice_period",
                "label_text": "What is your notice period?",
                "field_type": "text",
                "options": None,
                "proposed_value": None,
                "confidence": 0.0,
                "reasoning": "",
                "bounding_box": None,
            }
        ],
        "low_confidence_fields": [],
        "security_blocker_detected": False,
        "human_resolution": None,
        "final_status": "IN_PROGRESS",
    }
    res_auto = asyncio.run(orchestrator.evaluate_and_step("thread-auto-1", state_auto, now=morning_ist))
    assert res_auto["final_status"] == "APPLIED"

    # Case B: Contains subjective essay question -> Pauses in PAUSED_HITL, then resumes on Telegram human override
    state_hitl: ApplicationAgentState = {
        **state_auto,
        "job_id": "job-202",
        "company_name": "Zepto",
        "extracted_fields": [
            {
                "field_id": "why_zepto",
                "label_text": "Why do you want to work at Zepto?",
                "field_type": "textarea",
                "options": None,
                "proposed_value": None,
                "confidence": 0.0,
                "reasoning": "",
                "bounding_box": {"x": 100, "y": 220, "width": 400, "height": 90},
            }
        ],
    }
    res_paused = asyncio.run(orchestrator.evaluate_and_step("thread-hitl-2", state_hitl, now=morning_ist))
    assert res_paused["final_status"] == "PAUSED_HITL"
    assert len(res_paused["low_confidence_fields"]) == 1

    # Simulate user replying on Telegram with an edited answer
    res_resumed = asyncio.run(
        orchestrator.resume_from_human(
            thread_id="thread-hitl-2",
            action="APPROVE_OR_EDIT",
            field_overrides={"why_zepto": "I built sub-400ms Voice AI and RAG systems and love Zepto's engineering speed."},
            save_to_qa_cache=True,
        )
    )
    assert res_resumed["final_status"] == "APPLIED"
    assert res_resumed["extracted_fields"][0]["proposed_value"].startswith("I built sub-400ms")
