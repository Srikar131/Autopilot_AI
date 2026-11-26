# Contributing & Development Workflow

## Documentation-Driven Development (DDD)
1. Never implement a feature or change portal rate limits without checking `docs/07_Security_DevOps/SEC-001_Security_AntiBan_Baseline.md`.
2. Any architectural change requires a new ADR in `docs/03_System_Architecture/`.
3. All milestones must pass `uv run pytest` and `uv run python -m src.stealth_browser.stealth_audit` before touching real portal sessions.
