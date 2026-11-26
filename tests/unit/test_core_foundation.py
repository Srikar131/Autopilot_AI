from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from src.brain.latex_compiler import escape_latex, render_tailored_tex
from src.ingestion.deduplicator import canonicalize_job_url
from src.stealth_browser.circuit_breaker import PortalCircuitBreaker
from src.stealth_browser.human_physics import generate_bezier_trajectory


def test_bezier_trajectory_endpoints():
    pts = generate_bezier_trajectory((10.0, 20.0), (450.0, 620.0), steps=30)
    assert len(pts) == 30
    assert pts[0] == (10.0, 20.0)
    assert pts[-1] == (450.0, 620.0)


def test_circuit_breaker_circadian_and_url():
    cb = PortalCircuitBreaker()
    morning_ist = datetime(2026, 11, 2, 10, 30, tzinfo=ZoneInfo("Asia/Kolkata"))
    night_ist = datetime(2026, 11, 2, 23, 15, tzinfo=ZoneInfo("Asia/Kolkata"))

    assert cb.can_execute("naukri", now=morning_ist)[0] is True
    assert cb.can_execute("naukri", now=night_ist)[0] is False

    # Trip via security challenge URL
    tripped = cb.inspect_url_for_blockers("linkedin_easy", "https://www.linkedin.com/checkpoint/challenge/123")
    assert tripped is True
    assert cb.can_execute("linkedin_easy", now=morning_ist)[0] is False


def test_latex_escaping_and_rendering(tmp_path: Path):
    assert escape_latex("Improved accuracy by 24% & reduced $ cost_1") == r"Improved accuracy by 24\% \& reduced \$ cost\_1"
    out_tex = tmp_path / "tailored.tex"
    ctx = {
        "candidate": {
            "name": "Srikar Vaka",
            "location": "Hyderabad, India",
            "email": "srikar@example.com",
            "phone": "+91-XXXXXXXXXX",
        },
        "tailored_summary": escape_latex("AI/ML Engineer specializing in RAG, Multi-Agent Systems & Voice AI."),
        "skills": {
            "AI & ML": "PyTorch, LangGraph, Gemini API, Claude API, ElevenLabs, Cartesia",
            "Backend & Data": "Python, FastAPI, Supabase (pgvector), PostgreSQL, Docker",
        },
        "selected_projects": [
            {
                "title": "Project Avishkar",
                "tech_stack": "Supabase, Postgres, Edge Functions, Gemini API",
                "bullets": [
                    escape_latex("Architected low-latency backend with Supabase Postgres & Edge Functions reducing query latency by 35%.")
                ],
            }
        ],
        "education": {
            "degree": "B.Tech in Computer Science (AI & ML)",
            "institution": "SRM Institute of Technology",
            "graduation_year": "2026",
        },
    }
    rendered_path = render_tailored_tex(ctx, out_tex, template_dir=Path("templates"))
    content = rendered_path.read_text(encoding="utf-8")
    assert "Srikar Vaka" in content
    assert r"35\%" in content


def test_url_deduplication():
    u1, h1 = canonicalize_job_url("https://boards.greenhouse.io/razorpay/jobs/12345/?utm_source=linkedin")
    u2, h2 = canonicalize_job_url("https://boards.greenhouse.io/razorpay/jobs/12345")
    assert u1 == u2
    assert h1 == h2
