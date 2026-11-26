# AGENTS.md — AutoApply-AI Principal Engineering Instructions

## Project Overview
You are working inside **AutoApply-AI**, an autonomous, stealth, Human-in-the-Loop (HITL) job application engine.
All 10 documentation folders in `docs/` are populated and approved, and Milestones 1 through 5 are scaffolded and unit-tested in `src/` and `tests/`.

## Core Non-Negotiable Architecture Rules
1. **Zero-Auth Discovery Separation:** Never use authenticated LinkedIn/Naukri browser profiles to search or scrape job listings. Discovery (`src/ingestion/`) uses only Gmail alerts, JobSpy, and unauthenticated ATS scraping.
2. **Stealth Browser Engine:** Use `camoufox` and `patchright` with persistent local `user_data_dir` profiles (`~/.autoapply/profiles/{portal}`).
3. **Human Physics:** All clicks and typing in `src/stealth_browser/` must route through cubic Bezier mouse trajectories and Gaussian keystroke delays.
4. **LaTeX Jinja2 Delimiters:** Use `\VAR{}` and `\BLOCK{}` in `src/brain/latex_compiler.py` so Jinja2 never collides with native LaTeX braces.
5. **HITL Interrupts:** Pause execution and dispatch a Telegram card whenever any form field has `confidence < 0.85`, when `is_dream_company == True`, or when an OTP/CAPTCHA is detected.
