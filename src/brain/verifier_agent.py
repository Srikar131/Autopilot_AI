from __future__ import annotations

import asyncio
import json
import os
import re
from typing import Any

from src.brain.embeddings import is_configured_secret
from src.config import settings
from src.ingestion.jd_enrichment import is_role_relevant_to_profile, parse_experience_bounds

_SENIOR_TITLE_RE = re.compile(
    r"\b(sr\.?|senior|lead|principal|staff|director|vp|head\s+of|architect|manager)(?=\s|$|[,/-])",
    re.IGNORECASE,
)
_HIGH_EXP_JD_RE = re.compile(
    r"\b([5-9]|\d{2,})\+?\s*(?:years|yrs)\s+(?:of\s+)?experience\b",
    re.IGNORECASE,
)


def detect_work_mode(role_title: str, location: str, jd_text: str) -> str:
    """Infers work mode ('remote_wfh', 'hybrid', 'onsite', or 'unspecified') from listing metadata & JD."""
    combined = f"{role_title} {location} {jd_text}".lower()
    if any(tok in combined for tok in ("work from home", "wfh", "100% remote", "fully remote", "remote")):
        if "hybrid" in combined and "fully remote" not in combined:
            return "hybrid"
        return "remote_wfh"
    if "hybrid" in combined:
        return "hybrid"
    if any(tok in combined for tok in ("on-site", "onsite", "work from office", "in-office")):
        return "onsite"
    return "unspecified"


def _extract_lpa_mentions(text: str) -> list[float]:
    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(?:-\s*(\d+(?:\.\d+)?))?\s*lpa", text.lower())
    vals: list[float] = []
    for lo, hi in matches:
        try:
            vals.append(float(hi or lo))
        except ValueError:
            continue
    return vals


def _deterministic_round2_verify(
    user_message: str,
    jobs: list[dict[str, Any]],
    profile: dict[str, Any],
) -> dict[str, Any]:
    msg_low = (user_message or "").lower()
    prefs = profile.get("search_preferences") or {}
    demo = profile.get("demographics") or {}
    exp_range = str(prefs.get("experience_years_range") or "")
    exp_bounds = parse_experience_bounds(exp_range)

    want_remote = any(k in msg_low for k in ("wfh", "remote", "work from home")) or (
        prefs.get("work_mode_preference") == "remote_wfh" and "onsite" not in msg_low
    )
    want_hybrid = "hybrid" in msg_low or prefs.get("work_mode_preference") == "hybrid"

    min_sal = float(prefs.get("min_salary_lpa") or 0)
    sal_match = re.search(r"(\d+(?:\.\d+)?)\s*lpa", msg_low)
    if sal_match:
        min_sal = max(min_sal, float(sal_match.group(1)))

    excluded = [str(k).strip().lower() for k in (prefs.get("excluded_keywords") or []) if str(k).strip()]
    must_have = [str(k).strip().lower() for k in (prefs.get("must_have_keywords") or []) if str(k).strip()]
    pref_cities = [str(c).strip().lower() for c in (demo.get("preferred_locations") or []) if str(c).strip()]

    # Extract ad-hoc tech keywords mentioned in user chat prompt
    chat_tech_hints = [
        kw
        for kw in ("genai", "llm", "rag", "python", "fastapi", "voice", "agent", "langgraph", "pytorch", "ml")
        if kw in msg_low
    ]

    verified_ids: list[str] = []
    rejected_ids: list[str] = []
    verdicts: dict[str, dict[str, Any]] = {}

    for job in jobs:
        jid = str(job["id"])
        title = str(job.get("role_title") or "")
        loc = str(job.get("location") or "")
        jd = str(job.get("jd_raw_text") or "")
        combined = f"{title} {loc} {jd}".lower()
        score = float(job.get("match_score") or 0.0)
        reasoning = job.get("match_reasoning") or {}
        hard_blocked = bool(isinstance(reasoning, dict) and reasoning.get("hard_blocker_detected"))
        work_mode = detect_work_mode(title, loc, jd)

        reasons: list[str] = []
        approved = True

        if hard_blocked:
            approved = False
            reasons.append("Hard blocker detected in JD")
        if not is_role_relevant_to_profile(title, loc, profile):
            approved = False
            reasons.append("Role/location outside target profile domain")
        if exp_bounds is not None and exp_bounds[1] <= 2.5:
            if _SENIOR_TITLE_RE.search(title):
                approved = False
                reasons.append(f"Seniority mismatch for {exp_range} yrs experience")
            elif _HIGH_EXP_JD_RE.search(jd):
                approved = False
                reasons.append(f"Requires 5+ years experience (profile: {exp_range} yrs)")
        if score < 70.0:
            approved = False
            reasons.append(f"Round-1 match score too low ({score:.0f}%)")
        if any(ex in combined for ex in excluded):
            approved = False
            reasons.append("Contains excluded keyword")
        if must_have and not all(mh in combined for mh in must_have):
            approved = False
            reasons.append("Missing required profile keyword")
        if chat_tech_hints and not any(hint in combined for hint in chat_tech_hints):
            approved = False
            reasons.append(f"Does not mention requested stack ({', '.join(chat_tech_hints)})")

        if want_remote and work_mode not in ("remote_wfh",):
            if work_mode == "onsite" or "wfh" in msg_low or "remote" in msg_low:
                approved = False
                reasons.append(f"Work mode is {work_mode}, expected WFH/Remote")
        elif want_hybrid and work_mode == "onsite":
            approved = False
            reasons.append("Onsite-only role while Hybrid preferred")

        if min_sal > 0:
            lpa_mentions = _extract_lpa_mentions(jd)
            if lpa_mentions and max(lpa_mentions) < min_sal:
                approved = False
                reasons.append(f"Listed CTC ({max(lpa_mentions):.1f} LPA) below {min_sal:.0f} LPA minimum")

        if approved and pref_cities and "remote" not in loc.lower() and "india" not in loc.lower():
            if any(bad in loc.lower() for bad in ("san francisco", "new york", "london", "us only")):
                approved = False
                reasons.append("Outside preferred location / remote bounds")

        if approved:
            verified_ids.append(jid)
            verdict_msg = f"Round-2 Verified ({score:.0f}% match, work mode: {work_mode})"
        else:
            rejected_ids.append(jid)
            verdict_msg = "; ".join(reasons) or "Filtered out during Round-2 verification"

        verdicts[jid] = {
            "approved": approved,
            "fit_score": score,
            "reason": verdict_msg,
            "work_mode": work_mode,
        }

    reply = (
        f"Round-2 Verification complete across {len(jobs)} discovered roles: "
        f"{len(verified_ids)} verified as strong matches ready to apply, "
        f"{len(rejected_ids)} filtered out based on your profile & prompt criteria."
    )
    return {
        "assistant_reply": reply,
        "verified_job_ids": verified_ids,
        "rejected_job_ids": rejected_ids,
        "verdicts": verdicts,
    }


async def verify_and_filter_jobs_conversational(
    user_message: str,
    jobs: list[dict[str, Any]],
    profile: dict[str, Any],
    blueprint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """
    Agent 2 — Conversational AI Filter & Second-Round Verifier.
    Evaluates discovered roles against the saved Profile Vault + Blueprint + user's chat prompt.
    """
    if not jobs:
        return {
            "assistant_reply": "No discovered jobs found in the queue yet. Click 'Start Autonomous Search' in Profile & Start Search first.",
            "verified_job_ids": [],
            "rejected_job_ids": [],
            "verdicts": {},
        }

    deterministic = _deterministic_round2_verify(user_message, jobs, profile)

    if "PYTEST_CURRENT_TEST" not in os.environ and is_configured_secret(settings.gemini_api_key):
        try:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            compact_jobs = [
                {
                    "id": str(j["id"]),
                    "company": j.get("company_name"),
                    "role": j.get("role_title"),
                    "location": j.get("location"),
                    "portal": j.get("portal"),
                    "round1_score": j.get("match_score"),
                    "jd_snippet": str(j.get("jd_raw_text") or "")[:500],
                }
                for j in jobs[:40]
            ]
            prompt = (
                "You are Agent 2 (Round-2 Job Verification Agent). Evaluate each job strictly against the "
                "candidate's profile, requirements (WFH/salary/location), and the user's filter instruction.\n"
                "Return strict JSON with keys:\n"
                "- assistant_reply (concise helpful summary string)\n"
                "- decisions: array of {id, approved (bool), fit_score (0-100), reason (short string), work_mode (remote_wfh|hybrid|onsite|unspecified)}\n\n"
                f"User Instruction: {user_message}\n"
                f"Profile Blueprint: {json.dumps(blueprint or {})[:1500]}\n"
                f"Profile Preferences: {json.dumps(profile.get('search_preferences') or {})[:1000]}\n"
                f"Candidate Skills: {json.dumps(profile.get('skills_years') or {})[:800]}\n"
                f"Jobs To Verify: {json.dumps(compact_jobs)}"
            )
            resp = await asyncio.to_thread(
                client.models.generate_content,
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.1},
            )
            parsed = json.loads(resp.text or "{}")
            decisions = parsed.get("decisions")
            if isinstance(decisions, list) and decisions:
                verified_ids: list[str] = []
                rejected_ids: list[str] = []
                verdicts: dict[str, dict[str, Any]] = {}
                by_id = {str(d.get("id")): d for d in decisions if d.get("id")}
                for job in jobs:
                    jid = str(job["id"])
                    if jid in by_id:
                        dec = by_id[jid]
                        ok = bool(dec.get("approved"))
                        verdicts[jid] = {
                            "approved": ok,
                            "fit_score": float(dec.get("fit_score") or job.get("match_score") or 0.0),
                            "reason": str(dec.get("reason") or "Verified by Agent 2"),
                            "work_mode": str(dec.get("work_mode") or "unspecified"),
                        }
                        (verified_ids if ok else rejected_ids).append(jid)
                    else:
                        fallback_v = deterministic["verdicts"][jid]
                        verdicts[jid] = fallback_v
                        (verified_ids if fallback_v["approved"] else rejected_ids).append(jid)

                return {
                    "assistant_reply": str(parsed.get("assistant_reply") or deterministic["assistant_reply"]),
                    "verified_job_ids": verified_ids,
                    "rejected_job_ids": rejected_ids,
                    "verdicts": verdicts,
                }
        except Exception:
            pass

    return deterministic
