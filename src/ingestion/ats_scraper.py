from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

import httpx
from bs4 import BeautifulSoup

from src.ingestion.jd_enrichment import parse_and_check_recency
from src.ingestion.jobspy_worker import normalize_scraped_jobs


def _strip_html(raw_html: str) -> str:
    if not raw_html:
        return ""
    soup = BeautifulSoup(raw_html, "html.parser")
    return " ".join(soup.get_text(" ", strip=True).split())


def fetch_greenhouse_board_jobs(
    board_slug: str,
    client: Any | None = None,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """Fetches public unauthenticated job listings from a Greenhouse board API (past max_age_days)."""
    slug = board_slug.strip().lower()
    if not slug:
        return []
    url = f"https://boards-api.greenhouse.io/v1/boards/{slug}/jobs?content=true"
    try:
        resp = client.get(url, timeout=15.0) if client else httpx.get(url, timeout=15.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    raw_rows: list[dict[str, Any]] = []
    for item in payload.get("jobs", []):
        ok, posted_iso = parse_and_check_recency(item.get("updated_at"), max_age_days=max_age_days)
        if not ok:
            continue
        location = (item.get("location") or {}).get("name") or "Remote / India"
        raw_rows.append(
            {
                "job_url": item.get("absolute_url", ""),
                "id": str(item.get("id") or ""),
                "company": slug.replace("-", " ").title(),
                "title": item.get("title", "Software Engineer"),
                "location": location,
                "description": _strip_html(item.get("content", "")),
                "posted_at": posted_iso,
                "site": "greenhouse",
                "discovery_source": "ats_registry:greenhouse",
            }
        )
    return normalize_scraped_jobs(raw_rows, max_age_days=max_age_days)


def fetch_lever_board_jobs(
    board_slug: str,
    client: Any | None = None,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """Fetches public unauthenticated job listings from a Lever postings API (past max_age_days)."""
    slug = board_slug.strip().lower()
    if not slug:
        return []
    url = f"https://api.lever.co/v0/postings/{slug}?mode=json"
    try:
        resp = client.get(url, timeout=15.0) if client else httpx.get(url, timeout=15.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    if not isinstance(payload, list):
        return []

    raw_rows: list[dict[str, Any]] = []
    for item in payload:
        ok, posted_iso = parse_and_check_recency(item.get("createdAt"), max_age_days=max_age_days)
        if not ok:
            continue
        categories = item.get("categories") or {}
        desc_parts = [item.get("descriptionPlain") or _strip_html(item.get("description", ""))]
        for lst in item.get("lists", []):
            if lst.get("text"):
                desc_parts.append(str(lst["text"]))
            if lst.get("content"):
                desc_parts.append(_strip_html(str(lst["content"])))
        raw_rows.append(
            {
                "job_url": item.get("hostedUrl") or item.get("applyUrl") or "",
                "id": str(item.get("id") or ""),
                "company": slug.replace("-", " ").title(),
                "title": item.get("text", "Software Engineer"),
                "location": categories.get("location") or "Remote / India",
                "description": "\n".join(p for p in desc_parts if p),
                "posted_at": posted_iso,
                "site": "lever",
                "discovery_source": "ats_registry:lever",
            }
        )
    return normalize_scraped_jobs(raw_rows, max_age_days=max_age_days)


def fetch_ashby_board_jobs(
    board_slug: str,
    client: Any | None = None,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """Fetches public unauthenticated job listings from an Ashby posting API (past max_age_days)."""
    slug = board_slug.strip().lower()
    if not slug:
        return []
    url = f"https://api.ashbyhq.com/posting-api/job-board/{slug}"
    try:
        resp = client.get(url, timeout=15.0) if client else httpx.get(url, timeout=15.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    raw_rows: list[dict[str, Any]] = []
    for item in payload.get("jobs", []):
        ok, posted_iso = parse_and_check_recency(
            item.get("publishedAt") or item.get("updatedAt"),
            max_age_days=max_age_days,
        )
        if not ok:
            continue
        raw_rows.append(
            {
                "job_url": item.get("jobUrl") or item.get("applyUrl") or "",
                "id": str(item.get("id") or ""),
                "company": slug.replace("-", " ").title(),
                "title": item.get("title", "Software Engineer"),
                "location": item.get("location") or "Remote / India",
                "description": item.get("descriptionPlain") or _strip_html(item.get("descriptionHtml", "")),
                "posted_at": posted_iso,
                "site": "ashby",
                "discovery_source": "ats_registry:ashby",
            }
        )
    return normalize_scraped_jobs(raw_rows, max_age_days=max_age_days)


def fetch_smartrecruiters_board_jobs(
    board_slug: str,
    client: Any | None = None,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """Fetches public unauthenticated job listings from a SmartRecruiters postings API (past max_age_days)."""
    slug = board_slug.strip().lower()
    if not slug:
        return []
    url = f"https://api.smartrecruiters.com/v1/companies/{slug}/postings?limit=50"
    try:
        resp = client.get(url, timeout=15.0) if client else httpx.get(url, timeout=15.0)
        resp.raise_for_status()
        payload = resp.json()
    except Exception:
        return []

    raw_rows: list[dict[str, Any]] = []
    for item in payload.get("content", []):
        ok, posted_iso = parse_and_check_recency(item.get("releasedDate"), max_age_days=max_age_days)
        if not ok:
            continue
        jid = str(item.get("id") or "")
        if not jid:
            continue
        loc_obj = item.get("location") or {}
        loc_str = ", ".join(str(loc_obj.get(k) or "") for k in ("city", "country") if loc_obj.get(k))
        company_name = str(((item.get("company") or {}).get("name")) or slug.title())
        raw_rows.append(
            {
                "job_url": f"https://jobs.smartrecruiters.com/{slug}/{jid}",
                "id": jid,
                "company": company_name,
                "title": item.get("name", "Software Engineer"),
                "location": loc_str or "India / Remote",
                "description": "",
                "posted_at": posted_iso,
                "site": "smartrecruiters",
                "discovery_source": "ats_registry:smartrecruiters",
            }
        )
    return normalize_scraped_jobs(raw_rows, max_age_days=max_age_days)


def harvest_direct_ats_boards(
    greenhouse_slugs: list[str] | None = None,
    lever_slugs: list[str] | None = None,
    ashby_slugs: list[str] | None = None,
    smartrecruiters_slugs: list[str] | None = None,
    client: Any | None = None,
    max_age_days: int = 7,
) -> list[dict[str, Any]]:
    """Aggregates unauthenticated Greenhouse, Lever, Ashby, and SmartRecruiters boards concurrently."""
    tasks: list[tuple[Any, str]] = []
    for slug in greenhouse_slugs or []:
        tasks.append((fetch_greenhouse_board_jobs, slug))
    for slug in lever_slugs or []:
        tasks.append((fetch_lever_board_jobs, slug))
    for slug in ashby_slugs or []:
        tasks.append((fetch_ashby_board_jobs, slug))
    for slug in smartrecruiters_slugs or []:
        tasks.append((fetch_smartrecruiters_board_jobs, slug))

    if not tasks:
        return []

    batches: list[list[dict[str, Any]]] = []
    if client is not None or len(tasks) <= 3:
        for fn, slug in tasks:
            batches.append(fn(slug, client=client, max_age_days=max_age_days))
    else:
        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(fn, slug, None, max_age_days) for fn, slug in tasks]
            for fut in as_completed(futures):
                try:
                    batches.append(fut.result())
                except Exception:
                    pass

    combined: list[dict[str, Any]] = []
    seen_hashes: set[str] = set()
    for jobs in batches:
        for job in jobs:
            h = job.get("url_hash")
            if h and h not in seen_hashes:
                seen_hashes.add(h)
                combined.append(job)
    return combined
