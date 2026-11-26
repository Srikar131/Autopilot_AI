from __future__ import annotations

import os
import re
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

import httpx
from bs4 import BeautifulSoup

from src.ingestion.jobspy_worker import normalize_scraped_jobs

_DORK_SITE_CLAUSE = (
    "site:boards.greenhouse.io OR site:jobs.lever.co OR site:jobs.ashbyhq.com "
    "OR site:naukri.com/job-listings OR site:instahyre.com OR site:jobs.smartrecruiters.com"
)

_TARGET_URL_RE = re.compile(
    r"https?://(?:www\.)?(?:(?:boards|job-boards)\.greenhouse\.io/[^\s\"'<>]+|jobs\.lever\.co/[^\s\"'<>]+|jobs\.ashbyhq\.com/[^\s\"'<>]+|naukri\.com/job-listings-[^\s\"'<>]+|instahyre\.com/job-[^\s\"'<>]+|jobs\.smartrecruiters\.com/[^\s\"'<>]+)",
    re.IGNORECASE,
)


def _extract_ddg_target_url(href: str) -> str | None:
    if not href:
        return None
    candidate = href
    if "uddg=" in href:
        parsed = urlparse(href)
        uddg_vals = parse_qs(parsed.query).get("uddg")
        if uddg_vals:
            candidate = unquote(uddg_vals[0])
    match = _TARGET_URL_RE.search(candidate)
    return match.group(0) if match else None


def harvest_ats_search_dorks(
    search_terms: list[str] | None = None,
    locations: list[str] | None = None,
    max_queries: int = 4,
    client: Any | None = None,
) -> list[dict[str, Any]]:
    """
    Runs zero-auth open-web ATS dork searches (DuckDuckGo HTML) to uncover companies
    outside the built-in registry and feed newly discovered job URLs + company slugs.
    """
    if client is None and "PYTEST_CURRENT_TEST" in os.environ:
        return []

    terms = search_terms or ["AI Engineer", "GenAI Engineer", "Machine Learning Engineer"]
    locs = locations or ["India", "Remote"]
    raw_rows: list[dict[str, Any]] = []

    queries: list[str] = []
    for term in terms[:3]:
        loc = locs[0] if locs else "India"
        city = loc.split(",")[0].strip()
        queries.append(f'({_DORK_SITE_CLAUSE}) "{term}" "{city}"')
    queries = queries[:max_queries]

    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
        )
    }

    for q in queries:
        try:
            if client is not None:
                resp = client.get("https://html.duckduckgo.com/html/", params={"q": q}, timeout=12.0)
            else:
                resp = httpx.get(
                    "https://html.duckduckgo.com/html/",
                    params={"q": q},
                    headers=headers,
                    follow_redirects=True,
                    timeout=12.0,
                )
            resp.raise_for_status()
            soup = BeautifulSoup(resp.text, "html.parser")
            for link in soup.select("a.result__a, a[href]"):
                target_url = _extract_ddg_target_url(str(link.get("href") or ""))
                if not target_url:
                    continue
                title_text = link.get_text(" ", strip=True) or "Pending JD Fetch"
                raw_rows.append(
                    {
                        "job_url": target_url,
                        "company": "Unknown Company",
                        "title": title_text,
                        "location": locs[0] if locs else "India",
                        "description": "",
                        "site": "ats_dork",
                        "discovery_source": "ats_dork",
                    }
                )
        except Exception:
            continue

    return normalize_scraped_jobs(raw_rows)
