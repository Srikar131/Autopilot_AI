from __future__ import annotations

from typing import Any

DEFAULT_CANDIDATE_PROFILE: dict[str, Any] = {
    "demographics": {
        "full_name": "Srikar Vaka",
        "email": "srikar.vaka@example.com",
        "phone": "+91-9876543210",
        "current_city": "Hyderabad, Telangana, India",
        "preferred_locations": ["Hyderabad", "Bangalore", "Chennai", "Pune", "Remote"],
        "willing_to_relocate": "Yes",
        "notice_period_days": "Immediate / 15 Days",
        "degree": "Bachelor of Technology in Computer Science (AI & ML)",
        "university": "SRM Institute of Technology",
        "graduation_year": "2026",
        "work_authorization_india": "Yes",
        "requires_visa_sponsorship_india": "No",
    },
    "skills_years": {
        "Python": "3",
        "Machine Learning / Deep Learning": "2",
        "Generative AI / LLMs / RAG": "2",
        "FastAPI / Backend Development": "2",
        "PostgreSQL / Supabase / SQL": "2",
        "Voice AI (ElevenLabs, Cartesia)": "1.5",
        "LangGraph / Multi-Agent Systems": "1.5",
        "Docker / Cloud (GCP)": "1.5",
    },
    "projects": [
        {
            "title": "Project Avishkar — Cloud-Native AI Platform",
            "tech_stack": "Supabase Postgres, pgvector, Edge Functions, Gemini API, Python",
            "bullets": [
                "Architected scalable backend data pipelines and authentication workflows using Supabase PostgreSQL and Edge Functions.",
                "Integrated Gemini API workloads on Google Cloud Platform with vector search indexing for low-latency semantic retrieval.",
            ],
        },
        {
            "title": "Real-Time Conversational Voice AI Pipeline",
            "tech_stack": "ElevenLabs, Cartesia AI, Claude API, FastAPI, WebSockets",
            "bullets": [
                "Engineered ultra-low-latency voice synthesis and voice cloning pipelines combining Cartesia AI and ElevenLabs streaming APIs.",
                "Orchestrated multi-turn reasoning agents using Claude and Gemini models with sub-400ms time-to-first-audio-byte.",
            ],
        },
        {
            "title": "Autonomous Multi-Agent Job & Document Orchestrator",
            "tech_stack": "LangGraph, PostgreSQL Checkpoints, Tectonic LaTeX, Stealth Browser Automation",
            "bullets": [
                "Built stateful Human-in-the-Loop (HITL) agent workflows using LangGraph interrupt checkpoints and Telegram Bot webhooks.",
                "Automated dynamic 1-page LaTeX resume compilation and semantic Q&A retrieval over pgvector embeddings.",
            ],
        },
    ],
}
