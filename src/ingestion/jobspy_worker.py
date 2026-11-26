from __future__ import annotations

import logging
import math
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from src.ingestion.company_registry import CompanyATSRegistry
from src.ingestion.deduplicator import canonicalize_job_url
from src.ingestion.jd_enrichment import extract_external_job_id, parse_and_check_recency

logger = logging.getLogger("autoapply.jobspy")


def _silence_jobspy_loggers() -> None:
    """Suppresses noisy python-jobspy console errors such as 'Google: no job data returned'."""
    logging.getLogger("JobSpy").setLevel(logging.CRITICAL)
    for name in list(logging.root.manager.loggerDict):
        if name.lower().startswith("jobspy"):
            logging.getLogger(name).setLevel(logging.CRITICAL)

_DIRECT_ATS_HINTS = (
    "greenhouse.io",
    "lever.co",
    "ashbyhq.com",
    "myworkdayjobs.com",
    "smartrecruiters.com",
    "instahyre.com",
)


def _clean_str(value: Any, default: str = "") -> str:
    if value is None:
        return default
    if isinstance(value, float) and math.isnan(value):
        return default
    text = str(value).strip()
    if not text or text.lower() in ("nan", "none", "null"):
        return default
    return text


def detect_portal_type(url: str, site_name: str = "") -> str:
    lower_url = url.lower()
    if "greenhouse.io" in lower_url:
        return "greenhouse"
    if "lever.co" in lower_url:
        return "lever"
    if "ashbyhq.com" in lower_url:
        return "ashby"
    if "smartrecruiters.com" in lower_url:
        return "smartrecruiters"
    if "myworkdayjobs.com" in lower_url or "workday" in lower_url:
        return "workday"
    if "naukri.com" in lower_url or site_name == "naukri":
        return "naukri"
    if "linkedin.com" in lower_url or site_name == "linkedin":
        return "linkedin_easy"
    if "instahyre.com" in lower_url:
        return "instahyre"
    return "other_ats"


def normalize_scraped_jobs(raw_rows: list[dict[str, Any]], max_age_days: int = 7) -> list[dict[str, Any]]:
    """Normalizes raw JobSpy / ATS scraper records, enforcing recency and deduplicating by canonical URL hash."""
    seen_hashes: set[str] = set()
    normalized: list[dict[str, Any]] = []
    registry = CompanyATSRegistry() if "PYTEST_CURRENT_TEST" not in os.environ else None

    for row in raw_rows:
        ok_recency, posted_iso = parse_and_check_recency(
            row.get("date_posted") or row.get("posted_at"),
            max_age_days=max_age_days,
        )
        if not ok_recency:
            continue

        direct_url = _clean_str(row.get("job_url_direct"))
        primary_url = _clean_str(row.get("job_url") or row.get("url"))
        raw_url = (
            direct_url
            if direct_url and any(hint in direct_url.lower() for hint in _DIRECT_ATS_HINTS)
            else (primary_url or direct_url)
        )
        if not raw_url:
            continue

        clean_url, url_hash = canonicalize_job_url(raw_url)
        if url_hash in seen_hashes:
            continue
        seen_hashes.add(url_hash)

        if registry is not None:
            try:
                registry.register_company_from_url(clean_url)
            except Exception:
                pass

        site = _clean_str(row.get("site"))
        portal = detect_portal_type(clean_url, site)
        external_job_id = extract_external_job_id(
            clean_url,
            raw_id=_clean_str(row.get("external_job_id") or row.get("id")),
            url_hash=url_hash,
        )
        discovery_source = _clean_str(row.get("discovery_source") or site, "jobspy")

        normalized.append(
            {
                "canonical_url": clean_url,
                "url_hash": url_hash,
                "external_job_id": external_job_id,
                "discovery_source": discovery_source,
                "portal": portal,
                "company_name": _clean_str(row.get("company") or row.get("company_name"), "Unknown Company"),
                "role_title": _clean_str(row.get("title") or row.get("role_title"), "Software / AI Engineer"),
                "location": _clean_str(row.get("location"), "India"),
                "jd_raw_text": _clean_str(row.get("description") or row.get("jd_raw_text"), ""),
                "posted_at": posted_iso,
                "status": "DISCOVERED",
            }
        )
    return normalized


def harvest_jobs_unauthenticated(
    search_terms: list[str] | None = None,
    locations: list[str] | None = None,
    results_wanted: int = 25,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """
    Executes zero-auth job discovery via python-jobspy across LinkedIn & Indeed India
    for the past max_age_days (default 7 days / 168 hours).
    """
    terms = (
        search_terms
        if search_terms is not None
        else ["AI Engineer", "Machine Learning Engineer", "GenAI Developer", "Python Backend Engineer"]
    )
    locs = (
        locations
        if locations is not None
        else ["Hyderabad, Telangana, India", "Bengaluru, Karnataka, India", "Chennai, Tamil Nadu, India"]
    )
    if not terms or not locs:
        return []

    pairs: list[tuple[str, str]] = []
    seen_pairs: set[tuple[str, str]] = set()
    for term in terms[:3]:
        for loc in locs[:2]:
            key = (term.strip().lower(), loc.strip().lower())
            if key not in seen_pairs and term.strip() and loc.strip():
                seen_pairs.add(key)
                pairs.append((term.strip(), loc.strip()))
            if len(pairs) >= 6:
                break
        if len(pairs) >= 6:
            break

    all_raw: list[dict[str, Any]] = []
    _silence_jobspy_loggers()

    try:
        from jobspy import scrape_jobs

        _silence_jobspy_loggers()

        def _query_one(term: str, loc: str) -> list[dict[str, Any]]:
            df = scrape_jobs(
                site_name=["linkedin", "indeed"],
                search_term=term,
                location=loc,
                country_indeed="india",
                results_wanted=results_wanted,
                hours_old=max_age_days * 24,
                linkedin_fetch_description=False,
                fetch_description=True,
                verbose=0,
            )
            records: list[dict[str, Any]] = []
            if df is not None and not df.empty:
                records = df.to_dict(orient="records")
                for rec in records:
                    rec.setdefault("discovery_source", f"jobspy:{rec.get('site', 'multi')}")
            msg = f"[Agent 1 | JobSpy] Querying '{term}' in '{loc}' (past {max_age_days}d) -> {len(records)} listings"
            logger.info(msg)
            if "PYTEST_CURRENT_TEST" not in os.environ:
                print(msg, flush=True)
            return records

        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [pool.submit(_query_one, term, loc) for term, loc in pairs]
            for fut in as_completed(futures):
                try:
                    all_raw.extend(fut.result())
                except Exception:
                    continue
    except Exception:
        # Safe fallback if offline or jobspy blocked
        pass

    return normalize_scraped_jobs(all_raw, max_age_days=max_age_days)
