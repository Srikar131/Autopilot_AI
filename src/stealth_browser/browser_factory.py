from __future__ import annotations

import shutil
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from src.config import settings


def clear_portal_profile(portal: str) -> dict[str, Any]:
    """Safely removes saved cookies and storage for one portal and recreates an empty profile dir."""
    profile_dir = settings.get_profile_dir(portal)
    if profile_dir.exists():
        shutil.rmtree(profile_dir, ignore_errors=True)
    profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    return {"portal": portal, "cleared": True}


def inspect_portal_profiles(portals: list[str] | None = None) -> list[dict[str, Any]]:
    """Inspects local persistent browser profile directories on disk without launching browsers."""
    targets = portals or ["linkedin_easy", "naukri", "greenhouse", "lever", "ashby"]
    base = Path(settings.profiles_base_dir).expanduser().resolve()
    results: list[dict[str, Any]] = []
    for portal in targets:
        pdir = base / portal
        exists = pdir.exists()
        file_count = sum(1 for _ in pdir.iterdir()) if exists else 0
        results.append(
            {
                "portal": portal,
                "path": str(pdir),
                "exists": exists,
                "session_initialized": file_count > 0,
                "entry_count": file_count,
            }
        )
    return results


@asynccontextmanager
async def launch_stealth_context(
    portal: str,
    headless: bool = False,
    engine: str | None = None,
    worker_slot: int | None = None,
):
    """
    Launches a persistent, isolated browser profile for the given portal.
    Supports Camoufox (C++ patched Firefox) and Patchright (stealth-patched Chromium).
    Unauthenticated Direct ATS portals receive isolated worker_slot dirs for parallel apply.
    """
    dir_key = (
        f"{portal}_worker_{worker_slot}"
        if worker_slot is not None and portal not in ("linkedin_easy", "naukri", "instahyre")
        else portal
    )
    profile_dir = settings.get_profile_dir(dir_key)
    chosen = (engine or settings.default_browser_engine or "patchright").strip().lower()

    if chosen == "camoufox":
        try:
            from camoufox.async_api import AsyncCamoufox

            async with AsyncCamoufox(
                persistent_context=True,
                user_data_dir=str(profile_dir),
                headless=headless,
            ) as context:
                yield context
                return
        except Exception:
            pass

    try:
        from patchright.async_api import async_playwright
    except ImportError:
        from playwright.async_api import async_playwright

    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            viewport={"width": 1440, "height": 900},
            locale="en-IN",
            timezone_id="Asia/Kolkata",
            args=[
                "--disable-blink-features=AutomationControlled",
                "--no-default-browser-check",
            ],
        )
        try:
            yield context
        finally:
            await context.close()
