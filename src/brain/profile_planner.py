from __future__ import annotations

import asyncio
import json
import os
from typing import Any
from urllib.parse import quote_plus

from src.brain.embeddings import is_configured_secret
from src.config import settings
from src.ingestion.jd_enrichment import build_profile_search_queries


def _naukri_ctc_filter(min_salary_lpa: float) -> str:
    """Maps minimum desired CTC in LPA to Naukri's public ctcFilter URL bucket."""
    if min_salary_lpa <= 0:
        return ""
    if min_salary_lpa < 3:
        return "0to3"
    if min_salary_lpa < 6:
        return "3to6"
    if min_salary_lpa < 10:
        return "6to10"
    if min_salary_lpa < 15:
        return "10to15"
    if min_salary_lpa < 25:
        return "15to25"
    if min_salary_lpa < 50:
        return "25to50"
    return "50to75"


def _work_mode_flags(work_mode: str) -> tuple[str, str]:
    """Returns (naukri_wfhType, linkedin_f_WT) public query parameters."""
    mode = (work_mode or "any").strip().lower()
    if mode == "remote_wfh":
        return "2", "2"
    if mode == "hybrid":
        return "3", "3"
    if mode == "onsite":
        return "", "1"
    return "", ""


def _build_public_search_urls(
    search_terms: list[str],
    locations: list[str],
    work_mode: str,
    min_salary_lpa: float,
    experience_range: str,
) -> tuple[list[str], list[str]]:
    naukri_wfh, li_wt = _work_mode_flags(work_mode)
    ctc_bucket = _naukri_ctc_filter(min_salary_lpa)
    exp_str = (experience_range or "0-3").strip()

    naukri_urls: list[str] = []
    linkedin_urls: list[str] = []

    top_terms = search_terms[:2] or ["AI Engineer"]
    top_cities = [loc.split(",")[0].strip() for loc in locations[:2] if loc.strip()] or ["Bengaluru"]

    for term in top_terms:
        slug_term = "-".join(term.lower().split())
        for city in top_cities:
            slug_city = "-".join(city.lower().split())
            n_params = [f"k={quote_plus(term)}", f"l={quote_plus(city)}", "jobAge=7"]
            if exp_str:
                n_params.append(f"experience={quote_plus(exp_str)}")
            if naukri_wfh:
                n_params.append(f"wfhType={naukri_wfh}")
            if ctc_bucket:
                n_params.append(f"ctcFilter={ctc_bucket}")
            naukri_urls.append(
                f"https://www.naukri.com/{slug_term}-jobs-in-{slug_city}?{'&'.join(n_params)}"
            )

            li_params = [
                f"keywords={quote_plus(term)}",
                f"location={quote_plus(city if 'india' in city.lower() or city.lower() == 'remote' else f'{city}, India')}",
                "f_TPR=r604800",
            ]
            if li_wt:
                li_params.append(f"f_WT={li_wt}")
            linkedin_urls.append(f"https://www.linkedin.com/jobs/search?{'&'.join(li_params)}")

    return naukri_urls[:3], linkedin_urls[:2]


async def generate_profile_search_blueprint(
    profile: dict[str, Any],
    allow_llm: bool = True,
) -> dict[str, Any]:
    """
    Agent 0 — Analyzes the saved Candidate Profile Vault and requirements to synthesize an
    executive summary, targeted role/location search terms, and zero-auth Naukri/LinkedIn URLs.
    """
    demo = profile.get("demographics", {})
    prefs = profile.get("search_preferences", {})
    skills = profile.get("skills_years", {})

    work_mode = str(prefs.get("work_mode_preference") or "any")
    min_sal = float(prefs.get("min_salary_lpa") or 0)
    target_sal = float(prefs.get("target_salary_lpa") or 0)
    exp_range = str(prefs.get("experience_years_range") or "0-3")
    excluded = [str(k).strip() for k in (prefs.get("excluded_keywords") or []) if str(k).strip()]
    must_have = [str(k).strip() for k in (prefs.get("must_have_keywords") or []) if str(k).strip()]

    search_terms, locations = build_profile_search_queries(profile)
    top_skills = list(skills.keys())[:5]
    name = str(demo.get("full_name") or "Candidate")
    city = str(demo.get("current_city") or "India")
    degree = str(demo.get("degree") or "Engineering")

    mode_label = {
        "remote_wfh": "Remote / WFH preferred",
        "hybrid": "Hybrid preferred",
        "onsite": "Onsite",
    }.get(work_mode, "Flexible (Remote / Hybrid / Onsite)")

    salary_note = (
        f"Minimum {min_sal:.0f} LPA (target {target_sal:.0f} LPA)."
        if min_sal > 0 or target_sal > 0
        else "Open / competitive compensation."
    )

    persona_headline = f"{', '.join(search_terms[:2])} | {', '.join(top_skills[:3])}"
    executive_summary = (
        f"{name} ({degree}, based in {city}) targets {', '.join(search_terms[:3])} roles "
        f"with core expertise in {', '.join(top_skills[:4])}. "
        f"Preferences: {mode_label} across {', '.join(locations[:3])}; {salary_note}"
    )

    if allow_llm and "PYTEST_CURRENT_TEST" not in os.environ and is_configured_secret(settings.gemini_api_key):
        try:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            prompt = (
                "You are Agent 0 (Profile Intelligence & Search Strategist). "
                "Analyze this candidate profile and requirements and return strict JSON with keys: "
                "persona_headline (str), executive_summary (3 concise sentences), "
                "search_terms (list of 3-5 job titles), dealbreakers (list of strings).\n"
                f"Profile: {json.dumps(profile)[:5500]}"
            )
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.2},
            )
            parsed = json.loads(resp.text or "{}")
            if parsed.get("executive_summary"):
                executive_summary = str(parsed["executive_summary"]).strip()
            if parsed.get("persona_headline"):
                persona_headline = str(parsed["persona_headline"]).strip()
            if isinstance(parsed.get("search_terms"), list) and parsed["search_terms"]:
                search_terms = [str(s).strip() for s in parsed["search_terms"] if str(s).strip()][:5]
            if isinstance(parsed.get("dealbreakers"), list):
                excluded = sorted({*excluded, *(str(d).strip() for d in parsed["dealbreakers"] if str(d).strip())})
        except Exception:
            pass

    naukri_urls, linkedin_urls = _build_public_search_urls(
        search_terms=search_terms,
        locations=locations,
        work_mode=work_mode,
        min_salary_lpa=min_sal,
        experience_range=exp_range,
    )

    return {
        "persona_headline": persona_headline,
        "executive_summary": executive_summary,
        "search_terms": search_terms,
        "locations": locations,
        "work_mode_preference": work_mode,
        "min_salary_lpa": min_sal,
        "target_salary_lpa": target_sal,
        "experience_years_range": exp_range,
        "must_have_keywords": must_have,
        "dealbreakers": excluded,
        "naukri_urls": naukri_urls,
        "linkedin_guest_urls": linkedin_urls,
    }
