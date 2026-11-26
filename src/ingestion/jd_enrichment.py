from __future__ import annotations

import json
import math
import os
import re
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import urlparse

import httpx
from bs4 import BeautifulSoup

_JOB_ID_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"linkedin\.com/jobs/view/(?:[^/?]+-)?(\d+)", re.IGNORECASE),
    re.compile(r"naukri\.com/job-listings-.*?-(\d{6,})(?:$|[/?])", re.IGNORECASE),
    re.compile(r"(?:boards|job-boards)\.greenhouse\.io/[^/]+/jobs/(\d+)", re.IGNORECASE),
    re.compile(r"[?&]gh_jid=(\d+)", re.IGNORECASE),
    re.compile(r"jobs\.lever\.co/[^/]+/([0-9a-fA-F-]{8,})", re.IGNORECASE),
    re.compile(r"jobs\.ashbyhq\.com/[^/]+/([0-9a-fA-F-]{8,})", re.IGNORECASE),
    re.compile(r"myworkdayjobs\.com/.*?_([A-Za-z]*R-?\d+|\d{5,})(?:$|[/?])", re.IGNORECASE),
    re.compile(r"instahyre\.com/job-(\d+)-", re.IGNORECASE),
    re.compile(r"jobs\.smartrecruiters\.com/[^/]+/(\d+)", re.IGNORECASE),
]

_NON_TECH_EXCLUSIONS = (
    "account executive",
    "sales manager",
    "business development",
    "recruiter",
    "talent acquisition",
    "legal counsel",
    "accountant",
    "payroll",
    "customer support",
    "office manager",
    "copywriter",
    "nurse",
    "driver",
    "hr manager",
    "people partner",
)

_DEFAULT_ROLE_TOKENS = {
    "ai",
    "ml",
    "machine",
    "learning",
    "genai",
    "llm",
    "rag",
    "python",
    "software",
    "backend",
    "fullstack",
    "full-stack",
    "full stack",
    "data",
    "agent",
    "voice",
    "nlp",
    "deep",
    "pytorch",
    "fastapi",
}

_TECH_DOMAIN_HINTS = {
    "ai",
    "ml",
    "genai",
    "llm",
    "rag",
    "python",
    "software",
    "backend",
    "fullstack",
    "full-stack",
    "frontend",
    "data",
    "devops",
    "cloud",
    "pytorch",
    "fastapi",
}

_ROLE_STOPWORDS = {
    "engineer",
    "developer",
    "specialist",
    "associate",
    "junior",
    "senior",
    "lead",
    "staff",
    "principal",
    "intern",
    "role",
    "and",
    "the",
    "for",
    "with",
}

_CITY_ALIASES = {
    "bangalore": "bengaluru",
    "gurgaon": "gurugram",
    "bombay": "mumbai",
}

_FOREIGN_ONSITE_HINTS = (
    ", ca",
    ", ny",
    ", wa",
    ", tx",
    ", ma",
    "san francisco",
    "new york",
    "seattle",
    "austin",
    "boston",
    "london",
    "berlin",
    "paris",
    "amsterdam",
    "dublin",
    "toronto",
    "united states",
    "usa",
    "us only",
    "uk only",
    "eu only",
)

_REMOTE_OR_INDIA_HINTS = (
    "india",
    "bengaluru",
    "bangalore",
    "hyderabad",
    "chennai",
    "pune",
    "mumbai",
    "delhi",
    "ncr",
    "gurgaon",
    "gurugram",
    "noida",
    "remote",
    "anywhere",
    "global",
    "apac",
)


def extract_external_job_id(url: str, raw_id: str = "", url_hash: str = "") -> str:
    """Extracts the portal-native job ID from a canonical or raw job URL."""
    for pattern in _JOB_ID_PATTERNS:
        match = pattern.search(url or "")
        if match:
            return match.group(1)

    cleaned_raw = str(raw_id or "").strip()
    if cleaned_raw and cleaned_raw.lower() not in ("nan", "none", "null"):
        return cleaned_raw
    return (url_hash or "")[:12]


def parse_and_check_recency(
    raw_date: Any,
    max_age_days: int = 7,
    now: datetime | None = None,
) -> tuple[bool, str | None]:
    """
    Validates whether raw_date falls within max_age_days (default 7 days).
    Missing/unparseable dates return (True, None) so date-less sources still surface.
    """
    if raw_date is None:
        return True, None
    if isinstance(raw_date, float) and math.isnan(raw_date):
        return True, None

    ref_now = now or datetime.now(timezone.utc)
    if ref_now.tzinfo is None:
        ref_now = ref_now.replace(tzinfo=timezone.utc)

    parsed_dt: datetime | None = None

    if isinstance(raw_date, datetime):
        parsed_dt = raw_date if raw_date.tzinfo else raw_date.replace(tzinfo=timezone.utc)
    elif isinstance(raw_date, date):
        parsed_dt = datetime(raw_date.year, raw_date.month, raw_date.day, tzinfo=timezone.utc)
    elif isinstance(raw_date, (int, float)):
        ts = float(raw_date)
        if ts > 1e11:
            ts /= 1000.0
        parsed_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
    else:
        text = str(raw_date).strip()
        low = text.lower()
        if not text or low in ("nan", "none", "null"):
            return True, None
        if text.isdigit():
            ts = float(text)
            if ts > 1e11:
                ts /= 1000.0
            parsed_dt = datetime.fromtimestamp(ts, tz=timezone.utc)
        elif any(tok in low for tok in ("just now", "today", "few hours", "recently")):
            parsed_dt = ref_now
        elif "yesterday" in low:
            parsed_dt = ref_now - timedelta(days=1)
        else:
            rel = re.search(r"(\d+)\+?\s*(hour|hr|day|week|month|year)s?\s*(?:ago)?", low)
            if rel and "t" not in low and "-" not in low[:10]:
                qty = int(rel.group(1))
                unit = rel.group(2)
                if unit in ("hour", "hr"):
                    parsed_dt = ref_now - timedelta(hours=qty)
                elif unit == "day":
                    parsed_dt = ref_now - timedelta(days=qty)
                elif unit == "week":
                    parsed_dt = ref_now - timedelta(days=qty * 7)
                elif unit == "month":
                    parsed_dt = ref_now - timedelta(days=qty * 30)
                else:
                    parsed_dt = ref_now - timedelta(days=qty * 365)
            else:
                try:
                    dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
                    parsed_dt = dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
                except ValueError:
                    try:
                        d = date.fromisoformat(text[:10])
                        parsed_dt = datetime(d.year, d.month, d.day, tzinfo=timezone.utc)
                    except ValueError:
                        return True, None

    age_days = (ref_now - parsed_dt).total_seconds() / 86400.0
    return (age_days <= max_age_days + 0.25), parsed_dt.isoformat()


def parse_experience_bounds(exp_range: str | None) -> tuple[float, float] | None:
    """Parses strings like '0-2', '1-3', '2', '3+' into (min_years, max_years)."""
    text = str(exp_range or "").strip().lower()
    if not text:
        return None
    nums = [float(x) for x in re.findall(r"\d+(?:\.\d+)?", text)]
    if not nums:
        return None
    if len(nums) >= 2:
        return (min(nums[0], nums[1]), max(nums[0], nums[1]))
    if "+" in text:
        return (nums[0], 99.0)
    return (0.0, nums[0])


def _normalize_city_key(loc: str) -> str:
    city = str(loc or "").split(",")[0].strip().lower()
    return _CITY_ALIASES.get(city, city)


def build_profile_search_queries(profile: dict[str, Any] | None = None) -> tuple[list[str], list[str]]:
    """Derives unauthenticated search queries and city-deduplicated locations from the candidate profile."""
    prof = profile or {}
    prefs = prof.get("search_preferences") or {}
    demo = prof.get("demographics") or {}

    raw_terms: list[str] = []
    for key in ("target_roles", "search_terms"):
        vals = prefs.get(key)
        if isinstance(vals, list):
            raw_terms.extend(str(v).strip() for v in vals if str(v).strip())

    if not raw_terms:
        raw_terms = [
            "AI Engineer",
            "Machine Learning Engineer",
            "GenAI Developer",
            "Python Backend Engineer",
        ]

    # Deduplicate preserving order and cap to avoid excessive search fan-out
    seen_terms: set[str] = set()
    terms: list[str] = []
    for t in raw_terms:
        low = t.lower()
        if low not in seen_terms:
            seen_terms.add(low)
            terms.append(t)
        if len(terms) >= 5:
            break

    raw_locs: list[str] = []
    for candidate_list in (demo.get("preferred_locations"), prefs.get("locations")):
        if isinstance(candidate_list, list):
            raw_locs.extend(str(v).strip() for v in candidate_list if str(v).strip())

    if not raw_locs:
        raw_locs = ["Hyderabad, Telangana, India", "Bengaluru, Karnataka, India", "Remote"]

    seen_locs: set[str] = set()
    locations: list[str] = []
    for loc in raw_locs:
        city_key = _normalize_city_key(loc)
        if city_key and city_key not in seen_locs:
            seen_locs.add(city_key)
            locations.append(loc)
        if len(locations) >= 4:
            break

    return terms, locations


def is_role_relevant_to_profile(
    role_title: str,
    location: str,
    profile: dict[str, Any] | None = None,
) -> bool:
    """
    Universal pre-filter supporting both tech and non-tech candidate profiles.
    Skips unrelated domains and incompatible onsite geographies before JD hydration/scoring.
    """
    title_low = (role_title or "").strip().lower()
    if not title_low or title_low == "pending jd fetch":
        return True

    prof = profile or {}
    prefs = prof.get("search_preferences") or {}
    demo = prof.get("demographics") or {}
    skills = prof.get("skills_years") or {}

    target_phrases = [
        str(r).strip()
        for r in [*(prefs.get("target_roles") or []), *(prefs.get("search_terms") or [])]
        if str(r).strip()
    ]
    user_words: set[str] = set()
    for phrase in [*target_phrases, *(str(s) for s in skills.keys()), *(str(k) for k in (prefs.get("must_have_keywords") or []))]:
        for word in re.findall(r"[a-z0-9]+", phrase.lower()):
            if len(word) >= 2:
                user_words.add(word)

    is_tech_profile = (not target_phrases and not user_words) or bool(user_words & _TECH_DOMAIN_HINTS)

    for ex in _NON_TECH_EXCLUSIONS:
        ex_words = set(re.findall(r"[a-z0-9]+", ex))
        user_wants_ex = any(ex in tp.lower() or tp.lower() in ex for tp in target_phrases) or bool(
            (ex_words - _ROLE_STOPWORDS) & (user_words - _ROLE_STOPWORDS)
        )
        if not user_wants_ex and ex in title_low:
            return False

    active_tokens: set[str] = set(_DEFAULT_ROLE_TOKENS) if is_tech_profile else set()
    for word in user_words:
        if word not in _ROLE_STOPWORDS:
            active_tokens.add(word)

    title_words = set(re.findall(r"[a-z0-9]+", title_low))
    phrase_hit = any(tp.lower() in title_low for tp in target_phrases if len(tp.strip()) >= 3)
    tech_phrase_hit = is_tech_profile and any(
        tok in title_low
        for tok in ("ai ", " ai", "ml ", " ml", "genai", "llm", "python", "software", "backend", "full-stack", "fullstack")
    )
    role_match = bool(title_words & active_tokens) or phrase_hit or tech_phrase_hit
    if not role_match:
        return False

    loc_low = (location or "").strip().lower()
    if not loc_low or loc_low in ("unknown", "flexible", "hybrid"):
        return True

    preferred_cities = {
        str(c).strip().lower()
        for c in [*(demo.get("preferred_locations") or []), *(prefs.get("locations") or [])]
        if str(c).strip()
    }
    if any(city in loc_low for city in preferred_cities):
        return True

    if any(hint in loc_low for hint in _REMOTE_OR_INDIA_HINTS):
        if any(lock in loc_low for lock in ("us only", "united states only", "uk only", "eu only")):
            return False
        return True

    if any(foreign in loc_low for foreign in _FOREIGN_ONSITE_HINTS):
        return False

    return True


def needs_jd_hydration(job: dict[str, Any]) -> bool:
    """Returns True when a discovered job only has placeholder metadata or a truncated JD snippet."""
    jd_text = str(job.get("jd_raw_text") or "").strip()
    role = str(job.get("role_title") or "").strip()
    company = str(job.get("company_name") or "").strip()
    if role == "Pending JD Fetch":
        return True
    if company in ("", "Unknown Company", "Email Alert Discovery"):
        return True
    if "Extracted via Gmail Job Alert Ingestor" in jd_text:
        return True
    return len(jd_text) < 120


def _strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def _http_get_sync(url: str, client: Any | None = None) -> tuple[int, str]:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        ),
        "Accept": "text/html,application/json;q=0.9,*/*;q=0.8",
    }
    if client is not None:
        resp = client.get(url, timeout=15.0)
        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
        return getattr(resp, "status_code", 200), getattr(resp, "text", "") or json.dumps(resp.json())

    resp = httpx.get(url, headers=headers, follow_redirects=True, timeout=15.0)
    if resp.status_code in (403, 429):
        try:
            from curl_cffi import requests as cffi_requests

            cffi_resp = cffi_requests.get(url, impersonate="chrome", timeout=15.0)
            if cffi_resp.status_code < 400:
                return cffi_resp.status_code, cffi_resp.text
        except Exception:
            pass
    resp.raise_for_status()
    return resp.status_code, resp.text


def _parse_jsonld_job_posting(html_text: str) -> dict[str, str]:
    soup = BeautifulSoup(html_text, "html.parser")
    for script in soup.find_all("script", attrs={"type": "application/ld+json"}):
        raw = script.string or script.get_text()
        if not raw:
            continue
        try:
            parsed = json.loads(raw)
        except Exception:
            continue
        candidates = parsed if isinstance(parsed, list) else [parsed]
        for node in candidates:
            if isinstance(node, dict) and node.get("@type") == "JobPosting":
                org = node.get("hiringOrganization") or {}
                company = org.get("name", "") if isinstance(org, dict) else ""
                loc_obj = node.get("jobLocation") or {}
                if isinstance(loc_obj, list) and loc_obj:
                    loc_obj = loc_obj[0]
                addr = loc_obj.get("address", {}) if isinstance(loc_obj, dict) else {}
                loc_parts = [
                    addr.get("addressLocality", ""),
                    addr.get("addressRegion", ""),
                    addr.get("addressCountry", ""),
                ]
                loc_str = ", ".join(str(p).strip() for p in loc_parts if str(p).strip())
                return {
                    "role_title": str(node.get("title") or "").strip(),
                    "company_name": str(company or "").strip(),
                    "location": loc_str,
                    "jd_raw_text": _strip_html(str(node.get("description") or "")),
                }
    return {}


def hydrate_job_details(
    job: dict[str, Any],
    client: Any | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """
    Zero-auth deep hydrator: resolves full JD, role title, company, location, and external_job_id
    from ATS JSON endpoints, LinkedIn Guest HTML, or schema.org JobPosting JSON-LD.
    """
    url = str(job.get("canonical_url") or "")
    ext_id = str(job.get("external_job_id") or "") or extract_external_job_id(
        url,
        raw_id=str(job.get("id") or ""),
        url_hash=str(job.get("url_hash") or ""),
    )
    enriched = {**job, "external_job_id": ext_id}

    if not force and not needs_jd_hydration(enriched):
        return enriched
    if client is None and "PYTEST_CURRENT_TEST" in os.environ:
        return enriched

    portal = str(enriched.get("portal") or "")
    parsed = urlparse(url)
    path_parts = [p for p in parsed.path.split("/") if p]

    try:
        # 1. Greenhouse single-job API
        if portal == "greenhouse" and len(path_parts) >= 3 and "jobs" in path_parts:
            slug = path_parts[0]
            jid = path_parts[-1]
            api_url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs/{jid}"
            _, body = _http_get_sync(api_url, client=client)
            data = json.loads(body)
            desc = _strip_html(str(data.get("content") or ""))
            loc = ((data.get("location") or {}).get("name")) or enriched.get("location", "India")
            return {
                **enriched,
                "external_job_id": str(data.get("id") or ext_id),
                "company_name": slug.replace("-", " ").title()
                if enriched.get("company_name") in ("Email Alert Discovery", "Unknown Company", "")
                else enriched["company_name"],
                "role_title": str(data.get("title") or enriched.get("role_title") or "Software Engineer"),
                "location": str(loc),
                "jd_raw_text": desc or str(enriched.get("jd_raw_text") or ""),
            }

        # 2. Lever single-job API
        if portal == "lever" and len(path_parts) >= 2:
            slug, jid = path_parts[0], path_parts[1]
            api_url = f"https://api.lever.co/v0/postings/{slug}/{jid}"
            _, body = _http_get_sync(api_url, client=client)
            data = json.loads(body)
            cats = data.get("categories") or {}
            parts = [str(data.get("descriptionPlain") or _strip_html(str(data.get("description") or "")))]
            for lst in data.get("lists") or []:
                if lst.get("text"):
                    parts.append(str(lst["text"]))
                if lst.get("content"):
                    parts.append(_strip_html(str(lst["content"])))
            return {
                **enriched,
                "external_job_id": str(data.get("id") or ext_id),
                "company_name": slug.replace("-", " ").title()
                if enriched.get("company_name") in ("Email Alert Discovery", "Unknown Company", "")
                else enriched["company_name"],
                "role_title": str(data.get("text") or enriched.get("role_title") or "Software Engineer"),
                "location": str(cats.get("location") or enriched.get("location") or "India"),
                "jd_raw_text": "\n".join(p for p in parts if p) or str(enriched.get("jd_raw_text") or ""),
            }

        # 3. Ashby board lookup
        if portal == "ashby" and len(path_parts) >= 2:
            slug, jid = path_parts[0], path_parts[1]
            api_url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
            _, body = _http_get_sync(api_url, client=client)
            data = json.loads(body)
            for item in data.get("jobs") or []:
                item_url = str(item.get("jobUrl") or item.get("applyUrl") or "")
                if jid in item_url or item_url.rstrip("/") == url.rstrip("/"):
                    return {
                        **enriched,
                        "external_job_id": str(item.get("id") or ext_id),
                        "company_name": slug.replace("-", " ").title()
                        if enriched.get("company_name") in ("Email Alert Discovery", "Unknown Company", "")
                        else enriched["company_name"],
                        "role_title": str(item.get("title") or enriched.get("role_title") or "Software Engineer"),
                        "location": str(item.get("location") or enriched.get("location") or "Remote"),
                        "jd_raw_text": str(
                            item.get("descriptionPlain")
                            or _strip_html(str(item.get("descriptionHtml") or ""))
                            or enriched.get("jd_raw_text")
                            or ""
                        ),
                    }

        # 4. SmartRecruiters single-job API
        if portal == "smartrecruiters" and len(path_parts) >= 2:
            slug = path_parts[0]
            jid = re.split(r"[-/]", path_parts[1])[0]
            api_url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings/{jid}"
            _, body = _http_get_sync(api_url, client=client)
            data = json.loads(body)
            sections = (data.get("jobAd") or {}).get("sections") or {}
            desc = "\n".join(
                _strip_html(str((sec or {}).get("text") or ""))
                for sec in sections.values()
                if isinstance(sec, dict)
            )
            loc = ((data.get("location") or {}).get("city")) or enriched.get("location", "India")
            return {
                **enriched,
                "external_job_id": str(data.get("id") or ext_id),
                "company_name": str(((data.get("company") or {}).get("name")) or slug.title()),
                "role_title": str(data.get("name") or enriched.get("role_title") or "Software Engineer"),
                "location": str(loc),
                "jd_raw_text": desc or str(enriched.get("jd_raw_text") or ""),
            }

        # 5. LinkedIn unauthenticated guest job posting endpoint
        if portal == "linkedin_easy" and ext_id.isdigit():
            guest_url = f"https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{ext_id}"
            _, html_text = _http_get_sync(guest_url, client=client)
            soup = BeautifulSoup(html_text, "html.parser")
            title_el = soup.select_one(".top-card-layout__title, h2.top-card-layout__title")
            comp_el = soup.select_one(".topcard__org-name-link, a.topcard__org-name-link")
            loc_el = soup.select_one(".topcard__flavor--bullet")
            desc_el = soup.select_one(".show-more-less-html__markup, .description__text")
            desc_text = desc_el.get_text(" ", strip=True) if desc_el else ""
            if desc_text:
                return {
                    **enriched,
                    "company_name": comp_el.get_text(strip=True)
                    if comp_el
                    else enriched.get("company_name", "Unknown Company"),
                    "role_title": title_el.get_text(strip=True)
                    if title_el
                    else enriched.get("role_title", "Software Engineer"),
                    "location": loc_el.get_text(strip=True)
                    if loc_el
                    else enriched.get("location", "India"),
                    "jd_raw_text": desc_text,
                }

        # 6. Generic HTML + JSON-LD JobPosting fallback (Workday, Naukri, Instahyre, company career sites)
        if url.startswith(("http://", "https://")):
            _, html_text = _http_get_sync(url, client=client)
            jsonld = _parse_jsonld_job_posting(html_text)
            if jsonld.get("jd_raw_text"):
                return {
                    **enriched,
                    "company_name": jsonld.get("company_name") or enriched.get("company_name", "Unknown Company"),
                    "role_title": jsonld.get("role_title") or enriched.get("role_title", "Software Engineer"),
                    "location": jsonld.get("location") or enriched.get("location", "India"),
                    "jd_raw_text": jsonld["jd_raw_text"],
                }
            soup = BeautifulSoup(html_text, "html.parser")
            main_node = soup.select_one("main, article, .job-description, #job-description, body")
            if main_node:
                extracted_text = " ".join(main_node.get_text(" ", strip=True).split())
                if len(extracted_text) >= 120:
                    og_title = soup.find("meta", attrs={"property": "og:title"})
                    title_str = (
                        str(og_title["content"]).strip()
                        if og_title and og_title.get("content")
                        else (soup.title.get_text(strip=True) if soup.title else "")
                    )
                    return {
                        **enriched,
                        "role_title": title_str
                        if enriched.get("role_title") == "Pending JD Fetch" and title_str
                        else enriched.get("role_title", "Software Engineer"),
                        "jd_raw_text": extracted_text[:8000],
                    }
    except Exception:
        return enriched

    return enriched
