from __future__ import annotations

from datetime import datetime
from pathlib import Path
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

from fastapi.testclient import TestClient
from pypdf import PdfReader

from src import main as app_main
from src.brain.embeddings import cosine_similarity, embed_text
from src.brain.qa_rag import QAResolutionEngine
from src.db.profile_store import ProfileVaultStore
from src.db.supabase_client import SupabaseRepository
from src.ingestion.ats_scraper import harvest_direct_ats_boards
from src.pipeline_discovery import DiscoveryPipeline


def test_embeddings_are_768_dim_unit_vectors():
    v1 = embed_text("Why do you want to work at Zepto?")
    v2 = embed_text("Why do you want to work at Zepto")
    v3 = embed_text("Expected annual CTC in INR")

    assert len(v1) == 768
    assert abs(sum(x * x for x in v1) - 1.0) < 1e-3
    assert cosine_similarity(v1, v2) > 0.90
    assert cosine_similarity(v1, v3) < cosine_similarity(v1, v2)


def test_profile_store_and_dynamic_qa_resolution(tmp_path: Path):
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    initial = store.load()
    assert store.is_placeholder_contact(initial) is True

    updated = {
        **initial,
        "demographics": {
            **initial["demographics"],
            "full_name": "Aarav Sharma",
            "email": "aarav.sharma@gmail.com",
            "phone": "+91-9988776655",
            "expected_ctc_lpa": "24 LPA",
        },
    }
    saved = store.save(updated)
    assert store.is_placeholder_contact(saved) is False

    repo = SupabaseRepository(client=None)
    engine = QAResolutionEngine(repo=repo, profile=saved)
    res_ctc = engine.resolve_field(
        {
            "field_id": "exp_ctc",
            "label_text": "What is your expected CTC?",
            "field_type": "text",
            "options": None,
            "proposed_value": None,
            "confidence": 0.0,
            "reasoning": "",
            "bounding_box": None,
        }
    )
    assert res_ctc["confidence"] >= 0.90
    assert res_ctc["proposed_value"] == "24 LPA"

    # Train qa_cache and verify Tier-1 lookup hits with 0.96 confidence
    q_label = "Describe your Voice AI latency benchmarks"
    repo.insert_qa_cache(
        question_normalized=q_label,
        field_type="textarea",
        canonical_answer="Sub-400ms time-to-first-audio-byte using Cartesia and FastAPI.",
        question_embedding=embed_text(q_label),
        portal_context="lever",
    )
    res_cached = engine.resolve_field(
        {
            "field_id": "voice_bench",
            "label_text": q_label,
            "field_type": "textarea",
            "options": None,
            "proposed_value": None,
            "confidence": 0.0,
            "reasoning": "",
            "bounding_box": None,
        }
    )
    assert res_cached["confidence"] == 0.96
    assert "Sub-400ms" in str(res_cached["proposed_value"])


async def test_reportlab_fallback_pdf_contains_candidate_and_summary(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("src.brain.scorer.settings.gemini_api_key", "")
    monkeypatch.setattr("src.pipeline_discovery.compile_tex_to_pdf", MagicMock(side_effect=FileNotFoundError()))

    repo = SupabaseRepository(client=None)
    pipe = DiscoveryPipeline(
        repo=repo,
        output_base_dir=tmp_path / "resumes",
        template_dir=Path("templates"),
    )
    job = await pipe.process_job(
        {
            "canonical_url": "https://boards.greenhouse.io/razorpay/jobs/4242",
            "url_hash": "hash-reportlab-4242",
            "portal": "greenhouse",
            "company_name": "Razorpay",
            "role_title": "AI Engineer",
            "location": "Bengaluru",
            "jd_raw_text": "Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, Supabase, LangGraph, Docker.",
        }
    )
    pdf_path = Path(str(job["tailored_pdf_path"]))
    assert pdf_path.exists()
    reader = PdfReader(str(pdf_path))
    extracted = "\n".join(page.extract_text() or "" for page in reader.pages)
    assert "Srikar Vaka" in extracted
    assert "SUMMARY" in extracted


def test_ats_scraper_normalizes_greenhouse_lever_ashby():
    class MockResponse:
        def __init__(self, data):
            self._data = data

        def raise_for_status(self):
            return None

        def json(self):
            return self._data

    class MockClient:
        def get(self, url: str, timeout: float = 15.0):
            if "greenhouse.io" in url:
                return MockResponse(
                    {
                        "jobs": [
                            {
                                "absolute_url": "https://boards.greenhouse.io/razorpay/jobs/111",
                                "title": "AI Systems Engineer",
                                "location": {"name": "Bengaluru"},
                                "content": "<p>Build LLM RAG systems in Python.</p>",
                            }
                        ]
                    }
                )
            if "lever.co" in url:
                return MockResponse(
                    [
                        {
                            "hostedUrl": "https://jobs.lever.co/zepto/222",
                            "text": "Voice AI Developer",
                            "categories": {"location": "Bengaluru"},
                            "descriptionPlain": "Low latency voice pipelines.",
                        }
                    ]
                )
            return MockResponse(
                {
                    "jobs": [
                        {
                            "jobUrl": "https://jobs.ashbyhq.com/anthropic/333",
                            "title": "Applied AI Engineer",
                            "location": "Remote",
                            "descriptionPlain": "Multi-agent evaluations.",
                        }
                    ]
                }
            )

    jobs = harvest_direct_ats_boards(
        greenhouse_slugs=["razorpay"],
        lever_slugs=["zepto"],
        ashby_slugs=["anthropic"],
        client=MockClient(),
    )
    assert len(jobs) == 3
    assert {j["portal"] for j in jobs} == {"greenhouse", "lever", "ashby"}


def test_full_control_plane_e2e_api(monkeypatch, tmp_path: Path):
    repo = SupabaseRepository(client=None)
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    monkeypatch.setattr(app_main.orchestrator, "repo", repo)
    monkeypatch.setattr(app_main.orchestrator, "_checkpoints", {})
    monkeypatch.setattr(app_main, "profile_store", store)
    app_main.circuit_breaker.tripped_portals.clear()

    morning_ist = datetime(2026, 11, 2, 11, 0, tzinfo=ZoneInfo("Asia/Kolkata"))
    monkeypatch.setattr(
        app_main.circuit_breaker,
        "is_within_circadian_window",
        lambda now=None: True,
    )
    monkeypatch.setattr(app_main.circuit_breaker, "can_execute", lambda portal, now=None: (portal not in app_main.circuit_breaker.tripped_portals, "OK" if portal not in app_main.circuit_breaker.tripped_portals else "Tripped"))
    assert morning_ist.hour == 11

    resumes_dir = tmp_path / "resumes"
    screenshots_dir = tmp_path / "screenshots"
    monkeypatch.setattr(app_main, "OUTPUT_RESUMES_DIR", resumes_dir)
    monkeypatch.setattr(app_main, "OUTPUT_SCREENSHOTS_DIR", screenshots_dir)

    with TestClient(app_main.app) as client:
        readiness = client.get("/api/v1/system/readiness")
        assert readiness.status_code == 200
        assert "pdf_engine" in readiness.json()

        seeded = client.post("/api/v1/jobs/seed-sample")
        assert seeded.status_code == 200
        assert seeded.json()["discovered_count"] == 5
        assert seeded.json()["ready_to_apply_count"] == 4
        assert seeded.json()["rejected_count"] == 1

        jobs_res = client.get("/api/v1/jobs")
        assert jobs_res.status_code == 200
        jobs = jobs_res.json()["jobs"]
        assert len(jobs) == 5

        zepto_job = next(j for j in jobs if j["company_name"] == "Zepto")
        pdf_resp = client.get(f"/api/v1/jobs/{zepto_job['id']}/resume/pdf")
        assert pdf_resp.status_code == 200
        assert pdf_resp.headers["content-type"].startswith("application/pdf")

        tex_resp = client.get(f"/api/v1/jobs/{zepto_job['id']}/resume/tex")
        assert tex_resp.status_code == 200
        assert "Srikar Vaka" in tex_resp.json()["tex_content"]

        # Apply to Zepto -> triggers PAUSED_HITL (Dream Company + essay question)
        apply_resp = client.post(f"/api/v1/jobs/{zepto_job['id']}/apply", json={"live_browser": False})
        assert apply_resp.status_code == 200
        assert apply_resp.json()["final_status"] == "PAUSED_HITL"

        threads_resp = client.get("/api/v1/hitl/threads")
        assert threads_resp.status_code == 200
        assert threads_resp.json()["count"] >= 1
        thread_id = threads_resp.json()["threads"][0]["thread_id"]

        resume_resp = client.post(
            "/api/v1/hitl/resume",
            json={
                "langgraph_thread_id": thread_id,
                "action": "APPROVE_OR_EDIT",
                "field_overrides": {"why_zepto": "Sub-400ms Voice AI and FastAPI RAG systems."},
                "save_to_qa_cache": True,
            },
        )
        assert resume_resp.status_code == 200
        assert resume_resp.json()["final_status"] == "APPLIED"

        qa_list = client.get("/api/v1/qa-cache")
        assert qa_list.status_code == 200
        assert qa_list.json()["count"] >= 1
        item_id = qa_list.json()["items"][0]["id"]

        preview_res = client.post(
            "/api/v1/qa-cache/resolve-preview",
            json={"label_text": "What is your notice period?", "field_type": "text"},
        )
        assert preview_res.status_code == 200
        assert preview_res.json()["will_trigger_hitl"] is False

        del_res = client.delete(f"/api/v1/qa-cache/{item_id}")
        assert del_res.status_code == 200

        # Profile read/update & preview resume
        prof_get = client.get("/api/v1/profile")
        assert prof_get.status_code == 200
        prof_data = prof_get.json()["profile"]
        prof_data["demographics"]["email"] = "real.user@gmail.com"
        prof_data["demographics"]["phone"] = "+91-9988776655"
        prof_put = client.put("/api/v1/profile", json=prof_data)
        assert prof_put.status_code == 200
        assert prof_put.json()["is_placeholder_contact"] is False

        # Manual ingest + overview + circuit breaker reset
        ingest_resp = client.post(
            "/api/v1/jobs/ingest",
            json={
                "canonical_url": "https://boards.greenhouse.io/stripe/jobs/99999",
                "company_name": "Stripe",
                "role_title": "ML Systems Engineer",
                "location": "Bengaluru",
                "jd_raw_text": "Python, AI, Machine Learning, LLM, FastAPI, Postgres, Docker, GCP.",
            },
        )
        assert ingest_resp.status_code == 201
        assert ingest_resp.json()["job"]["is_dream_company"] is True

        overview = client.get("/api/v1/dashboard/overview")
        assert overview.status_code == 200
        assert overview.json()["total_jobs"] == 6

        client.post("/api/v1/circuit-breaker/trip", json={"portal": "naukri", "reason": "Manual test"})
        reset = client.post("/api/v1/circuit-breaker/reset", json={"portal": "naukri"})
        assert reset.status_code == 200
        assert reset.json()["was_tripped"] is True
