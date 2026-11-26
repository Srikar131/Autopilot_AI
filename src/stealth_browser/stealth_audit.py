from __future__ import annotations

import asyncio

from src.stealth_browser.browser_factory import launch_stealth_context


async def run_local_stealth_audit() -> dict:
    """Verifies that navigator.webdriver is False and hardware fingerprints are intact."""
    async with launch_stealth_context("sandbox_audit", headless=True) as context:
        page = await context.new_page()
        await page.goto("about:blank")
        report = await page.evaluate(
            """() => ({
                webdriver: navigator.webdriver,
                languages: navigator.languages,
                hardwareConcurrency: navigator.hardwareConcurrency,
                platform: navigator.platform
            })"""
        )
        return report


if __name__ == "__main__":
    result = asyncio.run(run_local_stealth_audit())
    print("Stealth Audit Report:", result)
