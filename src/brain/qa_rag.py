from __future__ import annotations

import json
import os
import re
import time
from typing import Any

from src.agent.state import FormFieldSpec
from src.brain.embeddings import embed_text, is_configured_secret
from src.config import settings
from src.db.seed_profile_vault import DEFAULT_CANDIDATE_PROFILE
from src.db.supabase_client import SupabaseRepository

ESSAY_TRIGGERS = (
    "why do you want to work",
    "why should we hire",
    "describe a challenging",
    "tell us about a time",
    "what excites you about",
    "cover letter",
    "anything else you'd like us to know",
    "why this role",
    "why our company",
)

_FOREIGN_COUNTRY_HINTS = (
    "united states",
    "u.s.",
    "usa",
    "canada",
    "united kingdom",
    "u.k.",
    "australia",
    "germany",
    "europe",
    "singapore",
)


def _extract_country_from_city(city_str: str) -> str:
    low = (city_str or "").lower()
    if "india" in low:
        return "India"
    parts = [p.strip() for p in (city_str or "").split(",") if p.strip()]
    return parts[-1] if parts else "India"


def _extract_discipline_from_degree(degree_str: str) -> str:
    raw = (degree_str or "").strip()
    if not raw:
        return "Computer Science"
    if "(" in raw and ")" in raw:
        inner = raw.split("(", 1)[1].split(")", 1)[0].strip()
        if inner:
            return inner
    if " in " in raw.lower():
        after_in = re.split(r"\bin\b", raw, maxsplit=1, flags=re.IGNORECASE)[-1].strip()
        if after_in:
            return after_in
    return raw


def _build_vault_rules(profile: dict[str, Any]) -> list[tuple[tuple[str, ...], str, str]]:
    """Constructs dynamic Profile Vault factual resolution rules from active candidate data."""
    default_demo = DEFAULT_CANDIDATE_PROFILE["demographics"]
    demo = {**default_demo, **profile.get("demographics", {})}
    skills = dict(profile.get("skills_years") or DEFAULT_CANDIDATE_PROFILE["skills_years"])
    prefs = profile.get("search_preferences") or {}

    full_name = str(demo.get("full_name", "Srikar Vaka")).strip()
    parts = full_name.split(None, 1)
    first_name = parts[0] if parts else full_name
    last_name = parts[1] if len(parts) > 1 else first_name
    country = _extract_country_from_city(str(demo.get("current_city", "India")))
    degree = str(demo.get("degree", "B.Tech in Computer Science (AI & ML)"))
    discipline = _extract_discipline_from_degree(degree)
    primary_role = next(
        (str(r).strip() for r in (prefs.get("target_roles") or []) if str(r).strip()),
        "Software Engineer",
    )

    rules: list[tuple[tuple[str, ...], str, str]] = [
        (
            ("notice period", "how soon can you join", "availability to join"),
            str(demo.get("notice_period_days", "Immediate / 15 Days")),
            "Direct match in profile_vault.demographics.notice_period_days",
        ),
        (
            ("willing to relocate", "comfortable relocating", "open to relocation"),
            str(demo.get("willing_to_relocate", "Yes")),
            "Direct match in profile_vault.demographics.willing_to_relocate",
        ),
        (
            ("legally authorized to work in india", "work authorization"),
            str(demo.get("work_authorization_india", "Yes")),
            "Direct match in profile_vault.demographics.work_authorization_india",
        ),
        (
            ("require sponsorship", "visa sponsorship"),
            str(demo.get("requires_visa_sponsorship_india", "No")),
            "Direct match in profile_vault.demographics.requires_visa_sponsorship_india",
        ),
        (
            ("current location", "current city", "city of residence"),
            str(demo.get("current_city", "Hyderabad, Telangana, India")),
            "Direct match in profile_vault.demographics.current_city",
        ),
        (
            ("first name", "given name", "preferred name"),
            first_name,
            "Parsed first name from profile_vault.demographics.full_name",
        ),
        (
            ("last name", "surname", "family name"),
            last_name,
            "Parsed last name from profile_vault.demographics.full_name",
        ),
        (
            ("full name", "your name", "candidate name", "legal name"),
            full_name,
            "Direct match in profile_vault.demographics.full_name",
        ),
        (
            ("email address", "your email", "e-mail", "email"),
            str(demo.get("email", "srikar.vaka@example.com")),
            "Direct match in profile_vault.demographics.email",
        ),
        (
            ("phone number", "mobile number", "contact number", "phone"),
            str(demo.get("phone", "+91-9876543210")),
            "Direct match in profile_vault.demographics.phone",
        ),
        (
            ("country",),
            country,
            "Derived country from profile_vault.demographics.current_city",
        ),
        (
            ("degree", "highest qualification", "education"),
            degree,
            "Direct match in profile_vault.demographics.degree",
        ),
        (
            ("university", "college name", "institution", "school"),
            str(demo.get("university", "SRM Institute of Technology")),
            "Direct match in profile_vault.demographics.university",
        ),
        (
            ("discipline", "major", "field of study", "specialization", "branch"),
            discipline,
            "Derived academic discipline from profile_vault.demographics.degree",
        ),
        (
            ("graduation year", "year of graduation", "passing year"),
            str(demo.get("graduation_year", "2026")),
            "Direct match in profile_vault.demographics.graduation_year",
        ),
        (
            ("current company", "current employer", "most recent company"),
            str(demo.get("current_company") or "Independent / Recent Graduate"),
            "Derived from profile_vault.demographics.current_company",
        ),
        (
            ("current role", "current title", "current job title"),
            str(demo.get("current_role") or primary_role),
            "Derived from profile_vault.demographics.current_role",
        ),
    ]

    if "Python" in skills:
        rules.append(
            (
                ("years of experience in python", "python experience"),
                str(skills["Python"]),
                "Direct match in profile_vault.skills_years.Python",
            )
        )

    if demo.get("linkedin_url"):
        rules.append(
            (("linkedin", "linkedin profile", "linkedin url"), str(demo["linkedin_url"]), "Direct match in profile_vault.demographics.linkedin_url")
        )
    if demo.get("github_url"):
        rules.append(
            (("github", "github profile", "github url"), str(demo["github_url"]), "Direct match in profile_vault.demographics.github_url")
        )
    portfolio_val = demo.get("portfolio_url") or demo.get("github_url") or demo.get("linkedin_url")
    if portfolio_val:
        rules.append(
            (("portfolio", "personal website", "website url", "work samples"), str(portfolio_val), "Direct match from profile_vault URLs")
        )

    exp_range = str(prefs.get("experience_years_range") or "")
    target_sal = prefs.get("target_salary_lpa")
    rules.append(
        (
            ("current ctc", "current compensation", "current salary"),
            str(demo.get("current_ctc_lpa") or "0 LPA"),
            "Derived from profile_vault.demographics.current_ctc_lpa",
        )
    )
    rules.append(
        (
            ("expected ctc", "expected compensation", "expected salary", "desired salary", "salary expectation"),
            str(demo.get("expected_ctc_lpa") or (f"{target_sal} LPA" if target_sal else "Negotiable")),
            "Direct match from profile_vault compensation settings",
        )
    )

    work_mode = str(prefs.get("work_mode_preference") or "any").lower()
    if work_mode == "remote_wfh":
        rules.append(
            (("comfortable working from office", "work from office", "onsite"), "Prefer Remote / WFH", "Direct match in search_preferences.work_mode_preference")
        )
    else:
        rules.append(
            (("comfortable working from office", "work from office", "onsite", "hybrid work", "work mode"), "Yes", "Direct match in search_preferences.work_mode_preference")
        )

    default_total_exp = exp_range.split("-")[-1].strip() if "-" in exp_range else (next(iter(skills.values()), "1") if skills else "1")
    rules.extend(
        [
            (
                ("total years of experience", "overall experience", "total experience", "years of experience"),
                str(skills.get("Python") or default_total_exp),
                "Derived from profile_vault experience settings",
            ),
            (
                ("how did you hear about us", "where did you hear about", "referral source"),
                "Company Careers Page / Job Board",
                "Standard factual source from profile_vault",
            ),
            (
                ("gender", "pronouns", "diversity", "veteran", "disability", "race", "ethnicity"),
                "Prefer not to say",
                "Standard voluntary EEO response",
            ),
        ]
    )

    for skill_name, yrs in skills.items():
        primary = skill_name.split("/")[0].split("(")[0].strip().lower()
        if primary and primary != "python":
            rules.append(
                (
                    (f"years of experience in {primary}", f"{primary} experience", f"experience with {primary}"),
                    str(yrs),
                    f"Direct match in profile_vault.skills_years.{skill_name}",
                )
            )

    return rules


class QAResolutionEngine:
    """
    Universal 3-tier intelligent form & screening resolver:
    1. Supabase pgvector qa_cache semantic match (confidence 0.96)
    2. Deterministic Profile Vault factual, country-aware & ATS-hygiene rules (confidence 0.92-0.96)
    3. Batched Gemini 2.5 Flash recruiter-grade synthesis grounded in candidate projects, skills & JD
    """

    def __init__(self, repo: SupabaseRepository | None = None, profile: dict[str, Any] | None = None) -> None:
        self.repo = repo or SupabaseRepository()
        self.profile = profile or DEFAULT_CANDIDATE_PROFILE

    def _resolve_from_vault(self, field: FormFieldSpec) -> FormFieldSpec | None:
        raw_label = field["label_text"].strip()
        label_lower = raw_label.lower()
        norm_label = re.sub(r"[\s*:]+$", "", label_lower).strip()
        is_required = bool(field.get("required") or "*" in raw_label)

        demo = {**DEFAULT_CANDIDATE_PROFILE["demographics"], **(self.profile.get("demographics") or {})}
        country = _extract_country_from_city(str(demo.get("current_city", "India")))

        # 1. Explicit honest-work / terms / privacy / accuracy agreements
        if any(
            t in label_lower
            for t in (
                "agree to use only my own words",
                "i certify",
                "privacy policy",
                "terms of service",
                "acknowledge",
                "i consent",
                "information is true",
            )
        ):
            aligned = self._align_with_options("Yes", field.get("options"))
            return {
                **field,
                "proposed_value": aligned,
                "confidence": 0.95,
                "reasoning": "Standard applicant acknowledgment/agreement matched from Profile Vault.",
            }

        # 2. Optional blank fields or missing optional links -> omit cleanly without HITL escalation
        if not is_required and any(
            opt in norm_label
            for opt in ("name pronunciation", "middle name", "cover letter", "additional information")
        ):
            return {
                **field,
                "proposed_value": "",
                "confidence": 0.95,
                "reasoning": "Optional field omitted cleanly without blocking submission.",
            }

        link_checks = [
            (("linkedin",), str(demo.get("linkedin_url") or "")),
            (("github",), str(demo.get("github_url") or "")),
            (("portfolio", "personal website", "website"), str(demo.get("portfolio_url") or demo.get("github_url") or demo.get("linkedin_url") or "")),
        ]
        for hints, link_val in link_checks:
            if any(h in norm_label for h in hints):
                if link_val.strip():
                    return {
                        **field,
                        "proposed_value": link_val.strip(),
                        "confidence": 0.95,
                        "reasoning": "Matched saved URL from Profile Vault.",
                    }
                if not is_required:
                    return {
                        **field,
                        "proposed_value": "",
                        "confidence": 0.95,
                        "reasoning": "Optional profile URL not set; skipped without HITL.",
                    }

        # 3. Country-aware sponsorship / work authorization check
        if "india" in country.lower() and any(fc in label_lower for fc in _FOREIGN_COUNTRY_HINTS):
            if "sponsorship" in label_lower or "visa" in label_lower:
                aligned = self._align_with_options("Yes", field.get("options"))
                return {
                    **field,
                    "proposed_value": aligned,
                    "confidence": 0.92,
                    "reasoning": "India-based candidate requires visa sponsorship for non-India country.",
                }
            if "authorized" in label_lower or "legally" in label_lower or "eligible" in label_lower:
                aligned = self._align_with_options("No", field.get("options"))
                return {
                    **field,
                    "proposed_value": aligned,
                    "confidence": 0.92,
                    "reasoning": "India-based candidate work authorization check for non-India country.",
                }

        # 4. General deterministic Profile Vault rules
        for keywords, canonical_val, reason in _build_vault_rules(self.profile):
            if any(k in norm_label or k in label_lower for k in keywords):
                resolved_val = self._align_with_options(canonical_val, field.get("options"))
                return {
                    **field,
                    "proposed_value": resolved_val,
                    "confidence": 0.95,
                    "reasoning": reason,
                }

        return None

    def _compose_profile_grounded_fallback(
        self,
        field: FormFieldSpec,
        job_context: dict[str, Any] | None = None,
    ) -> str:
        """Builds a coherent, role-specific fallback answer directly from active Profile Vault items."""
        ctx = job_context or {}
        demo = self.profile.get("demographics") or {}
        skills = list((self.profile.get("skills_years") or {}).keys())
        projects = self.profile.get("projects") or []
        prefs = self.profile.get("search_preferences") or {}

        label_low = field["label_text"].lower()
        company = str(ctx.get("company_name") or "your company").strip()
        role_title = str(ctx.get("role_title") or (prefs.get("target_roles") or ["this role"])[0]).strip()
        degree = str(demo.get("degree") or "B.Tech").strip()
        university = str(demo.get("university") or "university").strip()
        grad_year = str(demo.get("graduation_year") or "2026").strip()

        if any(k in label_low for k in ("math", "mathematics", "academic", "gpa", "grade", "cgpa", "score")):
            return (
                f"Completed {degree} at {university} (Class of {grad_year}) with strong quantitative, "
                f"analytical, and problem-solving coursework across {', '.join(skills[:3]) or 'core subjects'}."
            )

        q_words = set(re.findall(r"[a-z0-9]+", label_low))
        best_proj = projects[0] if projects else None
        if projects:
            best_proj = max(
                projects,
                key=lambda p: len(
                    q_words
                    & set(
                        re.findall(
                            r"[a-z0-9]+",
                            f"{p.get('title', '')} {p.get('tech_stack', '')} {' '.join(p.get('bullets', []))}".lower(),
                        )
                    )
                ),
            )

        skills_phrase = ", ".join(skills[:5]) if skills else role_title
        if best_proj:
            bullets = best_proj.get("bullets") or []
            highlight = bullets[0] if bullets else f"using {best_proj.get('tech_stack', skills_phrase)}"
            return (
                f"For {role_title} at {company}, I bring hands-on experience in {skills_phrase}. "
                f"Most recently in {best_proj.get('title', 'my recent project')}, I {highlight.rstrip('.')}."
            )
        return (
            f"I bring hands-on expertise in {skills_phrase} ({degree}, {university}) and am eager to "
            f"contribute measurable execution to {company} as a {role_title}."
        )

    def resolve_field(self, field: FormFieldSpec) -> FormFieldSpec:
        label_clean = field["label_text"].strip()
        label_lower = label_clean.lower()

        # Tier 1: Semantic / exact lookup in qa_cache
        query_embedding = embed_text(label_clean)
        cache_hits = self.repo.search_qa_cache(
            query_embedding=query_embedding,
            threshold=0.92,
            top_k=1,
            query_text=label_clean,
        )
        if cache_hits:
            best = cache_hits[0]
            resolved_val = self._align_with_options(str(best.get("canonical_answer", "")), field.get("options"))
            if resolved_val:
                sim = float(best.get("similarity", 0.96))
                return {
                    **field,
                    "proposed_value": resolved_val,
                    "confidence": 0.96,
                    "reasoning": f"Verified match from qa_cache (similarity={sim:.2f})",
                }

        # Tier 2: Deterministic factual rules from active Profile Vault
        vault_hit = self._resolve_from_vault(field)
        if vault_hit is not None:
            return vault_hit

        # Tier 3a: Subjective essay / open-ended screening questions
        if field["field_type"] == "textarea" or any(t in label_lower for t in ESSAY_TRIGGERS):
            draft = self._synthesize_with_gemini(field, is_essay=True) or self._compose_profile_grounded_fallback(field)
            return {
                **field,
                "proposed_value": draft,
                "confidence": 0.68,  # Single-field offline path escalates essays to HITL
                "reasoning": "Subjective / open-ended screening question drafted from Profile Vault.",
            }

        # Tier 3b: Optional Gemini field synthesis for unseen non-essay fields
        synth = self._synthesize_with_gemini_structured(field)
        if synth is not None:
            proposed_val = self._align_with_options(synth["value"], field.get("options"))
            conf = min(float(synth["confidence"]), 0.92)
            return {
                **field,
                "proposed_value": proposed_val,
                "confidence": conf,
                "reasoning": synth["reasoning"],
            }

        # Conservative fallback (< 0.85 -> triggers HITL when offline)
        return {
            **field,
            "proposed_value": field.get("proposed_value") or "",
            "confidence": 0.72,
            "reasoning": "Unseen screening field not found in qa_cache or deterministic vault; escalating to HITL.",
        }

    def resolve_fields_batch(
        self,
        fields: list[FormFieldSpec],
        job_context: dict[str, Any] | None = None,
    ) -> list[FormFieldSpec]:
        """
        Resolves all form fields using Tier-1 qa_cache and Tier-2 Vault rules first, then solves
        remaining screening/open-ended questions in ONE batched Gemini call grounded in the full profile & JD.
        """
        resolved_list: list[FormFieldSpec | None] = [None] * len(fields)
        unresolved_indices: list[int] = []

        for idx, field in enumerate(fields):
            label_clean = field["label_text"].strip()
            query_embedding = embed_text(label_clean)
            cache_hits = self.repo.search_qa_cache(
                query_embedding=query_embedding,
                threshold=0.92,
                top_k=1,
                query_text=label_clean,
            )
            if cache_hits:
                best = cache_hits[0]
                aligned = self._align_with_options(str(best.get("canonical_answer", "")), field.get("options"))
                if aligned:
                    sim = float(best.get("similarity", 0.96))
                    resolved_list[idx] = {
                        **field,
                        "proposed_value": aligned,
                        "confidence": 0.96,
                        "reasoning": f"Verified match from qa_cache (similarity={sim:.2f})",
                    }
                    continue

            vault_hit = self._resolve_from_vault(field)
            if vault_hit is not None:
                resolved_list[idx] = vault_hit
                continue

            unresolved_indices.append(idx)

        if not unresolved_indices:
            return [r for r in resolved_list if r is not None]

        if "PYTEST_CURRENT_TEST" in os.environ or not is_configured_secret(settings.gemini_api_key):
            for idx in unresolved_indices:
                resolved_list[idx] = self.resolve_field(fields[idx])
            return [r for r in resolved_list if r is not None]

        batch_fields = [
            {
                "field_id": fields[i]["field_id"],
                "label_text": fields[i]["label_text"],
                "field_type": fields[i]["field_type"],
                "options": fields[i].get("options"),
                "required": bool(fields[i].get("required", "*" in fields[i]["label_text"])),
            }
            for i in unresolved_indices
        ]
        ctx = job_context or {}
        answers_by_id: dict[str, dict[str, Any]] = {}

        for attempt in range(2):
            try:
                from google import genai

                client = genai.Client(api_key=settings.gemini_api_key)
                prompt = (
                    "You are an expert Career Application Strategist helping a real candidate complete a job application.\n"
                    "Answer every field honestly, concisely, and persuasively to maximize ATS & recruiter screening success, "
                    "grounded STRICTLY in the Candidate Profile and Job Description.\n"
                    "Return strict JSON: {\"answers\": [{\"field_id\": str, \"value\": str, \"confidence\": float, \"reasoning\": str}]}.\n"
                    "Rules:\n"
                    "- Assign confidence 0.88-0.95 for all screening, experience, project, tech/domain, academic, motivation, "
                    "work-preference, or optional questions answerable from the profile & JD.\n"
                    "- If options list is provided, pick the exact matching option string.\n"
                    "- ONLY assign confidence < 0.85 if a REQUIRED field asks for sensitive government/legal IDs (SSN/PAN/Passport), "
                    "criminal disclosures, or a required external URL missing from the profile.\n\n"
                    f"Target Job: {ctx.get('company_name', 'Company')} — {ctx.get('role_title', 'Role')}\n"
                    f"Job Description: {str(ctx.get('jd_raw_text') or '')[:2500]}\n"
                    f"Candidate Profile: {json.dumps(self.profile)[:4500]}\n"
                    f"Fields To Answer: {json.dumps(batch_fields)}"
                )
                resp = client.models.generate_content(
                    model="gemini-2.5-flash",
                    contents=prompt,
                    config={"response_mime_type": "application/json", "temperature": 0.2},
                )
                parsed = json.loads(resp.text or "{}")
                for item in parsed.get("answers") or []:
                    if isinstance(item, dict) and item.get("field_id"):
                        answers_by_id[str(item["field_id"])] = item
                if answers_by_id:
                    break
            except Exception:
                if attempt == 0:
                    time.sleep(1.0)

        for idx in unresolved_indices:
            f = fields[idx]
            ai_item = answers_by_id.get(f["field_id"])
            if ai_item and str(ai_item.get("value", "")).strip():
                aligned = self._align_with_options(str(ai_item["value"]).strip(), f.get("options"))
                conf = max(0.30, min(0.95, float(ai_item.get("confidence", 0.89))))
                resolved_list[idx] = {
                    **f,
                    "proposed_value": aligned,
                    "confidence": conf,
                    "reasoning": str(ai_item.get("reasoning") or "Synthesized from Candidate Profile & JD."),
                }
            else:
                draft = self._compose_profile_grounded_fallback(f, job_context)
                resolved_list[idx] = {
                    **f,
                    "proposed_value": draft if (f["field_type"] == "textarea" or len(f["label_text"]) > 18) else "",
                    "confidence": 0.72,
                    "reasoning": "Profile-grounded draft prepared for quick confirmation.",
                }

        return [r for r in resolved_list if r is not None]

    def _synthesize_with_gemini(self, field: FormFieldSpec, is_essay: bool = True) -> str | None:
        if "PYTEST_CURRENT_TEST" in os.environ or not is_configured_secret(settings.gemini_api_key):
            return None
        try:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            prompt = (
                "Draft a concise, high-signal 2-3 sentence answer for this job application screening question "
                "grounded strictly in the candidate profile.\n"
                f"Question: {field['label_text']}\n"
                f"Candidate Profile: {json.dumps(self.profile)}"
            )
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"temperature": 0.3},
            )
            text = (resp.text or "").strip()
            return text or None
        except Exception:
            return None

    def _synthesize_with_gemini_structured(self, field: FormFieldSpec) -> dict[str, Any] | None:
        if "PYTEST_CURRENT_TEST" in os.environ or not is_configured_secret(settings.gemini_api_key):
            return None
        try:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            prompt = (
                "You are PROMPT_FIELD_SYNTHESIZER_V1. Propose an answer for this job form field using the candidate profile. "
                "Return JSON with keys: value (string), confidence (float 0.30-0.92), reasoning (string). "
                "Set confidence >= 0.85 when the answer can be answered accurately from the candidate profile; "
                "set confidence < 0.85 ONLY when asking for private legal/gov IDs or missing external credentials.\n"
                f"Field Label: {field['label_text']}\n"
                f"Field Type: {field['field_type']}\n"
                f"Options: {field.get('options')}\n"
                f"Candidate Profile: {json.dumps(self.profile)}"
            )
            resp = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={"response_mime_type": "application/json", "temperature": 0.1},
            )
            data = json.loads(resp.text or "{}")
            val = str(data.get("value", "")).strip()
            if not val:
                return None
            return {
                "value": val,
                "confidence": max(0.3, min(0.92, float(data.get("confidence", 0.74)))),
                "reasoning": str(data.get("reasoning", "Synthesized by Gemini 2.5 Flash.")),
            }
        except Exception:
            return None

    @staticmethod
    def _align_with_options(val: str, options: list[str] | None) -> str:
        if not options:
            return val
        val_lower = val.strip().lower()
        for opt in options:
            if val_lower == opt.strip().lower():
                return opt
        for opt in options:
            if val_lower in opt.lower() or opt.lower() in val_lower:
                return opt
        if val_lower in ("yes", "i agree", "true", "checked"):
            for opt in options:
                olow = opt.lower()
                if any(tok in olow for tok in ("yes", "agree", "acknowledge", "confirm", "accept")):
                    return opt
        if val_lower in ("no", "false"):
            for opt in options:
                if opt.strip().lower().startswith("no"):
                    return opt
        return val
