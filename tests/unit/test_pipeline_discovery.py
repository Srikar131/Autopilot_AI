from pathlib import Path
from unittest.mock import MagicMock

import pytest

from src.db.supabase_client import SupabaseRepository
from src.ingestion.jobspy_worker import normalize_scraped_jobs
from src.pipeline_discovery import DiscoveryPipeline, is_dream_company


@pytest.mark.asyncio
async def test_pipeline_discovery_combines_sources_and_deduplicates(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        "src.pipeline_discovery.harvest_jobs_unauthenticated",
        lambda **kwargs: normalize_scraped_jobs(
            [
                {
                    "job_url": "https://boards.greenhouse.io/razorpay/jobs/12345?utm_source=jobspy",
                    "company": "Razorpay",
                    "title": "AI Engineer",
                    "location": "Bengaluru",
                    "description": (
                        "Seeking AI Engineer with Python, Machine Learning, LLM, GenAI, RAG, "
                        "FastAPI, Postgres, Supabase, LangGraph, and Docker."
                    ),
                }
            ]
        ),
    )

    repo = SupabaseRepository(client=None)
    pipeline = DiscoveryPipeline(
        repo=repo,
        output_base_dir=tmp_path / "resumes",
        template_dir=Path("templates"),
    )
    # Ensure deterministic local scoring in offline tests
    monkeypatch.setattr("src.brain.scorer.settings.gemini_api_key", "")

    emails = [
        "Alert: https://boards.greenhouse.io/razorpay/jobs/12345 and https://jobs.lever.co/acme/999"
    ]
    discovered = pipeline.discover_jobs(email_bodies=emails)
    assert len(discovered) == 2
    assert {d["portal"] for d in discovered} == {"greenhouse", "lever"}


@pytest.mark.asyncio
async def test_pipeline_scores_compiles_pdf_and_upserts_supabase(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("src.brain.scorer.settings.gemini_api_key", "")
    monkeypatch.setattr("src.pipeline_discovery.harvest_jobs_unauthenticated", lambda **kwargs: [])

    mock_table = MagicMock()
    mock_table.upsert.return_value.execute.return_value.data = None
    mock_client = MagicMock()
    mock_client.table.return_value = mock_table

    repo = SupabaseRepository(client=mock_client)
    pipeline = DiscoveryPipeline(
        repo=repo,
        output_base_dir=tmp_path / "resumes",
        template_dir=Path("templates"),
    )

    seed_jobs = normalize_scraped_jobs(
        [
            {
                "job_url": "https://boards.greenhouse.io/zepto/jobs/777",
                "company": "Zepto",
                "title": "GenAI & Voice Engineer",
                "location": "Bengaluru",
                "description": (
                    "We need Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, "
                    "Supabase, Postgres, LangGraph, Voice, Agent, Docker."
                ),
            },
            {
                "job_url": "https://jobs.lever.co/legacycorp/888",
                "company": "LegacyCorp",
                "title": "Principal Architect",
                "location": "Remote",
                "description": "Requires 12+ years of experience and US Citizens Only.",
            },
        ]
    )

    summary = await pipeline.run(seed_jobs=seed_jobs)
    assert summary["discovered_count"] == 2
    assert summary["ready_to_apply_count"] == 1
    assert summary["rejected_count"] == 1

    ready_job = next(j for j in summary["jobs"] if j["company_name"] == "Zepto")
    rejected_job = next(j for j in summary["jobs"] if j["company_name"] == "LegacyCorp")

    assert ready_job["status"] == "READY_TO_APPLY"
    assert ready_job["is_dream_company"] is True
    assert ready_job["match_score"] >= 80.0
    assert ready_job["tailored_pdf_path"] is not None
    pdf_file = Path(ready_job["tailored_pdf_path"])
    tex_file = pdf_file.with_suffix(".tex")
    assert pdf_file.exists()
    assert tex_file.exists()
    assert "Srikar Vaka" in tex_file.read_text(encoding="utf-8")

    assert rejected_job["status"] == "SCORED_REJECTED"
    assert rejected_job["tailored_pdf_path"] is None
    assert mock_client.table.call_count == 2
    assert is_dream_company("Razorpay India") is True
    assert is_dream_company("Random Startup") is False
