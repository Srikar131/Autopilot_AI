from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

from fastapi.testclient import TestClient

from src import main as app_main
from src.db.profile_store import ProfileVaultStore
from src.db.supabase_client import SupabaseRepository
from src.ingestion.ats_dork_scraper import harvest_ats_search_dorks
from src.ingestion.company_registry import BUILTIN_ATS_COMPANIES, CompanyATSRegistry
from src.ingestion.jd_enrichment import (
    build_profile_search_queries,
    extract_external_job_id,
    hydrate_job_details,
    is_role_relevant_to_profile,
)
from src.ingestion.jobspy_worker import normalize_scraped_jobs
from src.pipeline_discovery import DiscoveryPipeline


def test_extract_external_job_id_across_all_portals():
    assert (
        extract_external_job_id("https://www.linkedin.com/jobs/view/ai-engineer-4123456789")
        == "4123456789"
    )
    assert (
        extract_external_job_id("https://www.naukri.com/job-listings-genai-engineer-bengaluru-1234567890")
        == "1234567890"
    )
    assert (
        extract_external_job_id("https://boards.greenhouse.io/razorpay/jobs/998877")
        == "998877"
    )
    assert (
        extract_external_job_id("https://jobs.lever.co/zepto/abc12345-def6-7890-abcd-1234567890ab")
        == "abc12345-def6-7890-abcd-1234567890ab"
    )
    assert (
        extract_external_job_id("https://jobs.ashbyhq.com/anthropic/11112222-3333-4444-5555-666677778888")
        == "11112222-3333-4444-5555-666677778888"
    )
    assert (
        extract_external_job_id("https://company.wd5.myworkdayjobs.com/en-US/Careers/job/Bengaluru/AI-Eng_JR-104592")
        == "JR-104592"
    )
    assert (
        extract_external_job_id("https://www.instahyre.com/job-324511-ai-engineer-at-startup/")
        == "324511"
    )
    assert (
        extract_external_job_id("https://jobs.smartrecruiters.com/Visa/744000012345678-ai-engineer")
        == "744000012345678"
    )


def test_normalize_handles_nan_and_promotes_direct_ats_url():
    rows = [
        {
            "job_url": "https://www.indeed.com/viewjob?jk=abc123",
            "job_url_direct": "https://boards.greenhouse.io/stripe/jobs/6543210?gh_src=indeed",
            "company": float("nan"),
            "title": "AI Platform Engineer",
            "location": float("nan"),
            "description": float("nan"),
            "site": "indeed",
        }
    ]
    normalized = normalize_scraped_jobs(rows)
    assert len(normalized) == 1
    job = normalized[0]
    assert job["portal"] == "greenhouse"
    assert job["external_job_id"] == "6543210"
    assert job["company_name"] == "Unknown Company"
    assert job["jd_raw_text"] == ""
    assert job["location"] == "India"


def test_company_registry_preloaded_and_auto_expands(tmp_path: Path):
    total_builtin = sum(len(v) for v in BUILTIN_ATS_COMPANIES.values())
    assert total_builtin >= 95

    reg = CompanyATSRegistry(registry_path=tmp_path / "registry.json")
    found = reg.register_company_from_url("https://jobs.ashbyhq.com/newaicompany/abcd1234-5678-90ab")
    assert found == ("ashby", "newaicompany")
    summary = reg.summary()
    assert "newaicompany" in summary["auto_discovered"]["ashby"]
    assert "newaicompany" in summary["slugs_by_ats"]["ashby"]


def test_profile_relevance_filter_and_query_builder():
    profile = {
        "demographics": {"preferred_locations": ["Hyderabad", "Bengaluru", "Remote"]},
        "search_preferences": {"target_roles": ["GenAI Engineer", "AI Systems Engineer"]},
    }
    terms, locs = build_profile_search_queries(profile)
    assert "GenAI Engineer" in terms
    assert "Hyderabad" in locs

    assert is_role_relevant_to_profile("GenAI & RAG Engineer", "Bengaluru, India", profile) is True
    assert is_role_relevant_to_profile("Backend Python Developer", "Remote", profile) is True
    assert is_role_relevant_to_profile("Enterprise Account Executive", "Bengaluru, India", profile) is False
    assert is_role_relevant_to_profile("AI Engineer", "San Francisco, CA", profile) is False


def test_jd_hydrator_resolves_greenhouse_and_jsonld_html():
    class MockResp:
        def __init__(self, text: str):
            self.text = text
            self.status_code = 200

        def raise_for_status(self):
            return None

        def json(self):
            import json

            return json.loads(self.text)

    class MockClient:
        def get(self, url: str, timeout: float = 15.0):
            if "boards-api.greenhouse.io/v1/boards/razorpay/jobs/777111" in url:
                return MockResp(
                    '{"id": 777111, "title": "Staff AI Engineer", "location": {"name": "Bengaluru, India"}, '
                    '"content": "<p>Build Python, FastAPI, LangGraph, RAG, Supabase pgvector, and LLM pipelines.</p>"}'
                )
            if "example-careers.test" in url:
                html = """
                <html><head>
                <script type="application/ld+json">
                {
                  "@type": "JobPosting",
                  "title": "ML Platform Engineer",
                  "hiringOrganization": {"name": "ScaleTech"},
                  "jobLocation": {"address": {"addressLocality": "Hyderabad", "addressCountry": "India"}},
                  "description": "<p>Requires Python, PyTorch, FastAPI, Docker, and production LLM RAG orchestration systems.</p>"
                }
                </script>
                </head><body></body></html>
                """
                return MockResp(html)
            raise ValueError(f"Unexpected URL: {url}")

    client = MockClient()
    gh_stub = {
        "canonical_url": "https://boards.greenhouse.io/razorpay/jobs/777111",
        "url_hash": "hash-gh-777111",
        "portal": "greenhouse",
        "company_name": "Email Alert Discovery",
        "role_title": "Pending JD Fetch",
        "location": "India",
        "jd_raw_text": "Extracted via Gmail Job Alert Ingestor",
    }
    hydrated_gh = hydrate_job_details(gh_stub, client=client)
    assert hydrated_gh["external_job_id"] == "777111"
    assert hydrated_gh["role_title"] == "Staff AI Engineer"
    assert hydrated_gh["company_name"] == "Razorpay"
    assert "LangGraph" in hydrated_gh["jd_raw_text"]

    ld_stub = {
        "canonical_url": "https://example-careers.test/jobs/ml-platform-55",
        "url_hash": "hash-ld-55",
        "portal": "other_ats",
        "company_name": "Unknown Company",
        "role_title": "Pending JD Fetch",
        "location": "India",
        "jd_raw_text": "",
    }
    hydrated_ld = hydrate_job_details(ld_stub, client=client)
    assert hydrated_ld["company_name"] == "ScaleTech"
    assert hydrated_ld["role_title"] == "ML Platform Engineer"
    assert "Hyderabad" in hydrated_ld["location"]
    assert "PyTorch" in hydrated_ld["jd_raw_text"]


def test_ats_dork_scraper_extracts_and_unwraps_links():
    class MockResp:
        def __init__(self, text: str):
            self.text = text

        def raise_for_status(self):
            return None

    class MockClient:
        def get(self, url: str, params=None, timeout: float = 12.0):
            html = """
            <html><body>
              <a class="result__a" href="https://duckduckgo.com/l/?uddg=https%3A%2F%2Fboards.greenhouse.io%2Fstripe%2Fjobs%2F556677">
                AI Engineer - Stripe
              </a>
              <a class="result__a" href="https://jobs.lever.co/zepto/12345678-aaaa-bbbb-cccc-1234567890ab">
                Voice AI Engineer - Zepto
              </a>
            </body></html>
            """
            return MockResp(html)

    jobs = harvest_ats_search_dorks(
        search_terms=["AI Engineer"],
        locations=["Bengaluru"],
        max_queries=1,
        client=MockClient(),
    )
    assert len(jobs) == 2
    assert {j["portal"] for j in jobs} == {"greenhouse", "lever"}
    assert any(j["external_job_id"] == "556677" for j in jobs)


async def test_pipeline_sweeps_registry_filters_and_hydrates(monkeypatch, tmp_path: Path):
    monkeypatch.setattr("src.brain.scorer.settings.gemini_api_key", "")
    monkeypatch.setattr("src.pipeline_discovery.harvest_jobs_unauthenticated", lambda **kwargs: [])
    monkeypatch.setattr("src.pipeline_discovery.harvest_ats_search_dorks", lambda **kwargs: [])
    monkeypatch.setattr("src.pipeline_discovery.compile_tex_to_pdf", MagicMock(side_effect=FileNotFoundError()))

    reg = CompanyATSRegistry(registry_path=tmp_path / "registry.json")
    repo = SupabaseRepository(client=None)

    monkeypatch.setattr(
        "src.pipeline_discovery.harvest_direct_ats_boards",
        lambda **kwargs: normalize_scraped_jobs(
            [
                {
                    "job_url": "https://jobs.ashbyhq.com/anthropic/aaaa1111-2222-3333-4444-555566667777",
                    "company": "Anthropic",
                    "title": "Applied AI Engineer",
                    "location": "Remote",
                    "description": "Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, Docker, Postgres.",
                    "discovery_source": "ats_registry:ashby",
                },
                {
                    "job_url": "https://jobs.ashbyhq.com/anthropic/bbbb1111-2222-3333-4444-555566667777",
                    "company": "Anthropic",
                    "title": "Enterprise Account Executive",
                    "location": "Remote",
                    "description": "Quota carrying B2B software sales.",
                    "discovery_source": "ats_registry:ashby",
                },
            ]
        ),
    )

    pipe = DiscoveryPipeline(
        repo=repo,
        output_base_dir=tmp_path / "resumes",
        template_dir=Path("templates"),
        registry=reg,
    )
    summary = await pipe.run(sweep_company_registry=True, run_ats_dorks=False)
    assert summary["discovered_count"] == 1
    assert summary["ready_to_apply_count"] == 1
    job = summary["jobs"][0]
    assert job["role_title"] == "Applied AI Engineer"
    assert job["external_job_id"] == "aaaa1111-2222-3333-4444-555566667777"
    assert job["discovery_source"] == "ats_registry:ashby"


def test_registry_and_hydrate_endpoints(monkeypatch, tmp_path: Path):
    repo = SupabaseRepository(client=None)
    reg = CompanyATSRegistry(registry_path=tmp_path / "registry.json")
    store = ProfileVaultStore(vault_path=tmp_path / "vault.json")
    monkeypatch.setattr(app_main.orchestrator, "repo", repo)
    monkeypatch.setattr(app_main, "company_registry", reg)
    monkeypatch.setattr(app_main, "profile_store", store)
    monkeypatch.setattr(app_main, "OUTPUT_RESUMES_DIR", tmp_path / "resumes")
    monkeypatch.setattr("src.brain.scorer.settings.gemini_api_key", "")
    monkeypatch.setattr("src.pipeline_discovery.compile_tex_to_pdf", MagicMock(side_effect=FileNotFoundError()))

    repo.upsert_job(
        {
            "id": "job-hyd-1",
            "canonical_url": "https://boards.greenhouse.io/razorpay/jobs/101010",
            "url_hash": "hash-hyd-1",
            "external_job_id": "101010",
            "portal": "greenhouse",
            "company_name": "Email Alert Discovery",
            "role_title": "Pending JD Fetch",
            "location": "India",
            "jd_raw_text": "Extracted via Gmail Job Alert Ingestor",
            "status": "DISCOVERED",
        }
    )

    monkeypatch.setattr(
        app_main,
        "hydrate_job_details",
        lambda job, force=True: {
            **job,
            "company_name": "Razorpay",
            "role_title": "AI Systems Engineer",
            "location": "Bengaluru, India",
            "jd_raw_text": "Python, AI, Machine Learning, LLM, GenAI, RAG, FastAPI, Supabase, LangGraph, Docker.",
        },
    )

    with TestClient(app_main.app) as client:
        reg_res = client.get("/api/v1/discovery/registry")
        assert reg_res.status_code == 200
        assert reg_res.json()["total_companies"] >= 95

        add_res = client.post(
            "/api/v1/discovery/registry/add",
            json={"career_url": "https://jobs.lever.co/perplexity-custom/123"},
        )
        assert add_res.status_code == 201
        assert add_res.json()["slug"] == "perplexity-custom"

        hyd_res = client.post("/api/v1/jobs/job-hyd-1/hydrate")
        assert hyd_res.status_code == 200
        assert hyd_res.json()["job"]["status"] == "READY_TO_APPLY"
        assert hyd_res.json()["job"]["company_name"] == "Razorpay"
