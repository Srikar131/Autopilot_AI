from __future__ import annotations

import os
import random
from typing import Any

from src.ingestion.jobspy_worker import normalize_scraped_jobs

_CHALLENGE_HINTS = (
    "/checkpoint/",
    "/challenge/",
    "/authwall",
    "verify you are human",
    "captcha",
    "access denied",
    "security-verification",
)

_VIEWPORT_POOL = [
    {"width": 1366, "height": 768},
    {"width": 1440, "height": 900},
    {"width": 1536, "height": 864},
]


def _is_challenge_page(url: str, title: str) -> bool:
    combined = f"{url} {title}".lower()
    return any(hint in combined for hint in _CHALLENGE_HINTS)


async def _scrape_guest_page(page: Any, source_kind: str) -> list[dict[str, Any]]:
    if source_kind == "naukri":
        return await page.evaluate(
            """() => {
                const out = [];
                const cards = document.querySelectorAll('.srp-jobtuple-wrapper, article.jobTuple, .cust-job-tuple');
                cards.forEach((card) => {
                    const titleEl = card.querySelector('a.title, a[href*="job-listings-"]');
                    if (!titleEl || !titleEl.href) return;
                    const compEl = card.querySelector('a.comp-name, .comp-name');
                    const locEl = card.querySelector('.locWdth, .loc-wrap, .location');
                    const salEl = card.querySelector('.sal-wrap, .salary');
                    const descEl = card.querySelector('.job-desc, .row4');
                    const ageEl = card.querySelector('.job-post-day, .ni-job-tuple-icon-srp-clock');
                    out.push({
                        job_url: titleEl.href,
                        title: titleEl.innerText.trim(),
                        company: compEl ? compEl.innerText.trim() : 'Naukri Listing',
                        location: locEl ? locEl.innerText.trim() : 'India',
                        description: [salEl ? 'Salary: ' + salEl.innerText.trim() : '', descEl ? descEl.innerText.trim() : ''].filter(Boolean).join(' | '),
                        posted_at: ageEl ? ageEl.innerText.trim() : '',
                        site: 'naukri',
                        discovery_source: 'ephemeral_guest:naukri'
                    });
                });
                if (out.length === 0) {
                    document.querySelectorAll('a[href*="job-listings-"]').forEach((a) => {
                        if (a.href && a.innerText.trim()) {
                            out.push({
                                job_url: a.href,
                                title: a.innerText.trim(),
                                company: 'Naukri Listing',
                                location: 'India',
                                description: '',
                                site: 'naukri',
                                discovery_source: 'ephemeral_guest:naukri'
                            });
                        }
                    });
                }
                return out.slice(0, 25);
            }"""
        )

    return await page.evaluate(
        """() => {
            const out = [];
            const cards = document.querySelectorAll('.base-search-card, li .base-card');
            cards.forEach((card) => {
                const linkEl = card.querySelector('a.base-card__full-link, a[href*="/jobs/view/"]');
                if (!linkEl || !linkEl.href) return;
                const titleEl = card.querySelector('.base-search-card__title');
                const compEl = card.querySelector('.base-search-card__subtitle');
                const locEl = card.querySelector('.job-search-card__location');
                const timeEl = card.querySelector('time');
                out.push({
                    job_url: linkEl.href,
                    title: titleEl ? titleEl.innerText.trim() : linkEl.innerText.trim(),
                    company: compEl ? compEl.innerText.trim() : 'LinkedIn Public Listing',
                    location: locEl ? locEl.innerText.trim() : 'India',
                    description: '',
                    posted_at: timeEl ? (timeEl.getAttribute('datetime') || timeEl.innerText.trim()) : '',
                    site: 'linkedin',
                    discovery_source: 'ephemeral_guest:linkedin'
                });
            });
            return out.slice(0, 25);
        }"""
    )


async def harvest_ephemeral_guest_jobs(
    blueprint: dict[str, Any] | None = None,
    playwright_factory: Any | None = None,
    max_naukri_urls: int = 3,
    max_linkedin_urls: int = 2,
) -> list[dict[str, Any]]:
    """
    Agent 1 Ephemeral Chromium Scout (Strict Zero-Auth Isolation):
    - Launches headless Chromium with NO user_data_dir and NO personal cookies.
    - Creates a fresh disposable browser context per search URL, clears cookies before & after,
      and aborts immediately without bypassing if any CAPTCHA/authwall appears.
    """
    if playwright_factory is None and "PYTEST_CURRENT_TEST" in os.environ:
        return []

    bp = blueprint or {}
    naukri_urls = list(bp.get("naukri_urls") or [])[:max_naukri_urls]
    linkedin_urls = list(bp.get("linkedin_guest_urls") or [])[:max_linkedin_urls]
    targets: list[tuple[str, str]] = [
        *[("naukri", u) for u in naukri_urls],
        *[("linkedin", u) for u in linkedin_urls],
    ]
    if not targets:
        return []

    if playwright_factory is None:
        try:
            from patchright.async_api import async_playwright as pw_factory
        except ImportError:
            from playwright.async_api import async_playwright as pw_factory
    else:
        pw_factory = playwright_factory

    raw_rows: list[dict[str, Any]] = []
    try:
        async with pw_factory() as p:
            browser = await p.chromium.launch(
                headless=True,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-default-browser-check",
                ],
            )
            try:
                for source_kind, target_url in targets:
                    viewport = random.choice(_VIEWPORT_POOL)
                    context = await browser.new_context(
                        viewport=viewport,
                        locale="en-IN",
                        timezone_id="Asia/Kolkata",
                    )
                    try:
                        if hasattr(context, "clear_cookies"):
                            await context.clear_cookies()
                        page = await context.new_page()
                        await page.goto(target_url, wait_until="domcontentloaded", timeout=15000)
                        current_url = str(getattr(page, "url", target_url) or target_url)
                        title = str(await page.title()) if hasattr(page, "title") else ""
                        if _is_challenge_page(current_url, title):
                            continue
                        if hasattr(page, "wait_for_selector"):
                            try:
                                await page.wait_for_selector(
                                    '.srp-jobtuple-wrapper, article.jobTuple, .cust-job-tuple, .base-search-card, a[href*="job-listings-"]',
                                    timeout=4500,
                                )
                            except Exception:
                                pass
                        extracted = await _scrape_guest_page(page, source_kind)
                        if isinstance(extracted, list):
                            if "PYTEST_CURRENT_TEST" not in os.environ:
                                print(
                                    f"[Agent 1 | Ephemeral Chromium] {target_url[:70]} -> {len(extracted)} public listings",
                                    flush=True,
                                )
                            raw_rows.extend(extracted)
                    except Exception:
                        continue
                    finally:
                        try:
                            if hasattr(context, "clear_cookies"):
                                await context.clear_cookies()
                        except Exception:
                            pass
                        await context.close()
            finally:
                await browser.close()
    except Exception:
        return []

    return normalize_scraped_jobs(raw_rows)
