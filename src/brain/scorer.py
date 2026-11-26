from __future__ import annotations

import asyncio
import json
import os
import re
from dataclasses import asdict, dataclass
from typing import Any, List

from src.brain.embeddings import is_configured_secret
from src.config import settings
from src.db.seed_profile_vault import DEFAULT_CANDIDATE_PROFILE
from src.ingestion.jd_enrichment import parse_experience_bounds


@dataclass
class JDScorerResult:
    match_score: float
    matched_skills: List[str]
    missing_critical_skills: List[str]
    selected_project_titles: List[str]
    tailored_summary: str
    hard_blocker_detected: bool = False
    blocker_reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


_SENIOR_TITLE_RE = re.compile(
    r"\b(sr\.?|senior|lead|principal|staff|director|vp|head\s+of|architect|manager)(?=\s|$|[,/-])",
    re.IGNORECASE,
)
_HIGH_EXP_JD_RE = re.compile(
    r"\b([5-9]|\d{2,})\+?\s*(?:years|yrs)\s+(?:of\s+)?experience\b",
    re.IGNORECASE,
)


def deterministic_keyword_score(
    jd_text: str,
    profile: dict[str, Any] | None = None,
    role_title: str = "",
) -> JDScorerResult:
    """Word-boundary heuristic scorer with seniority & experience-range guard."""
    prof = profile or DEFAULT_CANDIDATE_PROFILE
    search_text = f"{role_title}\n{jd_text}".lower()

    base_keywords = [
        "python",
        "ai",
        "machine learning",
        "llm",
        "genai",
        "rag",
        "fastapi",
        "postgres",
        "postgresql",
        "supabase",
        "langgraph",
        "langchain",
        "pytorch",
        "deep learning",
        "voice",
        "agent",
        "docker",
        "gcp",
    ]
    prefs = prof.get("search_preferences") or {}
    demo = prof.get("demographics") or {}
    projects_list = prof.get("projects") or []

    profile_skills = [
        str(k).strip().lower()
        for k in (prof.get("skills_years") or {}).keys()
        if len(str(k).strip()) >= 2
    ]
    role_tokens: list[str] = []
    for raw_item in [
        *(prefs.get("target_roles") or []),
        *(prefs.get("search_terms") or []),
        *(prefs.get("must_have_keywords") or []),
    ]:
        item_low = str(raw_item).strip().lower()
        if len(item_low) >= 2:
            role_tokens.append(item_low)
        for word in re.findall(r"[a-z0-9]+", item_low):
            if len(word) >= 3 and word not in {"manager", "specialist", "executive", "coordinator", "lead", "engineer", "developer", "associate", "senior", "junior", "and", "the", "for", "with"}:
                role_tokens.append(word)

    project_tokens: list[str] = []
    for proj in projects_list:
        stack_str = str(proj.get("tech_stack") or "")
        for part in re.split(r"[,/|;]+", stack_str):
            cleaned = part.strip().lower()
            if len(cleaned) >= 2:
                project_tokens.append(cleaned)

    tech_signal_words = {
        w
        for src_kw in [*profile_skills, *role_tokens]
        for w in re.findall(r"[a-z0-9]+", src_kw)
    }
    is_tech_or_default = (prof is DEFAULT_CANDIDATE_PROFILE) or bool(
        tech_signal_words
        & {
            "ai",
            "ml",
            "genai",
            "llm",
            "rag",
            "python",
            "software",
            "backend",
            "fullstack",
            "frontend",
            "data",
            "cloud",
            "devops",
            "pytorch",
            "fastapi",
        }
    )

    if is_tech_or_default:
        keyword_pool = list(dict.fromkeys([*base_keywords, *profile_skills, *project_tokens]))
    else:
        keyword_pool = list(dict.fromkeys([*profile_skills, *role_tokens, *project_tokens]))

    matched = [
        kw
        for kw in keyword_pool
        if re.search(rf"(?<![a-z0-9]){re.escape(kw)}(?![a-z0-9])", search_text)
    ]

    exp_range = str(prefs.get("experience_years_range") or "")
    exp_bounds = parse_experience_bounds(exp_range)

    hard_blocker = any(
        phrase in search_text
        for phrase in [
            "us citizens only",
            "active top secret clearance",
            "10+ years of experience",
            "12+ years",
        ]
    )
    blocker_reason = "Hard experience or citizenship constraint detected" if hard_blocker else ""

    raw_score = min(98.0, 55.0 + len(matched) * 6.5)
    if hard_blocker:
        raw_score = 25.0
    elif exp_bounds is not None and exp_bounds[1] <= 2.5:
        if role_title and _SENIOR_TITLE_RE.search(role_title):
            hard_blocker = True
            raw_score = 38.0
            blocker_reason = f"Seniority mismatch for {exp_range} yrs experience"
        elif _HIGH_EXP_JD_RE.search(search_text):
            hard_blocker = True
            raw_score = 35.0
            blocker_reason = f"Requires 5+ years experience (profile: {exp_range} yrs)"

    projects = [p["title"] for p in projects_list][:3]
    primary_role = next(
        (str(r).strip() for r in (prefs.get("target_roles") or []) if str(r).strip()),
        "Professional",
    )
    degree = str(demo.get("degree") or "").strip()
    top_skills = list((prof.get("skills_years") or {}).keys())[:5]
    skills_phrase = ", ".join(top_skills) if top_skills else ", ".join(keyword_pool[:4]) or primary_role
    projects_phrase = ", ".join(projects[:2]) if projects else primary_role
    degree_part = f" ({degree})" if degree else ""
    summary = (
        f"{primary_role}{degree_part} experienced in {skills_phrase}, "
        f"delivering measurable outcomes across {projects_phrase}."
    )
    return JDScorerResult(
        match_score=round(raw_score, 2),
        matched_skills=matched,
        missing_critical_skills=[],
        selected_project_titles=projects,
        tailored_summary=summary,
        hard_blocker_detected=hard_blocker,
        blocker_reason=blocker_reason,
    )


async def score_job_description(
    jd_text: str,
    profile: dict[str, Any] | None = None,
    role_title: str = "",
) -> JDScorerResult:
    """Scores a JD using Gemini 2.5 Flash Structured Outputs off the event loop with safe deterministic fallback."""
    prof = profile or DEFAULT_CANDIDATE_PROFILE
    det = deterministic_keyword_score(jd_text, prof, role_title=role_title)
    if det.hard_blocker_detected or "PYTEST_CURRENT_TEST" in os.environ or not is_configured_secret(settings.gemini_api_key):
        return det

    try:
        from google import genai

        client = genai.Client(api_key=settings.gemini_api_key)
        prefs = prof.get("search_preferences") or {}
        prompt = (
            "Score this Job Description against the candidate profile and return strict JSON matching JDScorerResult keys "
            "(match_score, matched_skills, missing_critical_skills, selected_project_titles, tailored_summary, "
            "hard_blocker_detected, blocker_reason). Pay strict attention to experience_years_range "
            f"({prefs.get('experience_years_range', '0-3')} yrs).\n"
            f"Role Title: {role_title}\n"
            f"Candidate Profile: {json.dumps(prof)[:4500]}\n\n"
            f"Job Description:\n{jd_text[:5500]}"
        )
        response = await asyncio.to_thread(
            client.models.generate_content,
            model="gemini-2.5-flash",
            contents=prompt,
            config={"response_mime_type": "application/json", "temperature": 0.1},
        )
        data = json.loads(response.text)
        return JDScorerResult(**data)
    except Exception:
        return det
