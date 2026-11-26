# AutoApply-AI: Stealth HITL Job Application Autopilot

An enterprise-grade, zero-ban-risk AI Job Application Autopilot built with a decoupled zero-auth discovery plane, persistent anti-detect local browsers (`camoufox` / `patchright`), dynamic Tectonic LaTeX / ReportLab resume tailoring, 3-tier `pgvector` Q&A memory, LangGraph + Telegram Human-in-the-Loop (HITL) escalation, and a full React Web Command Center.

## Quick Start (Backend + Web Command Center)
1. Copy `.env.example` to `.env` and fill in your API keys (`GEMINI_API_KEY`, `SUPABASE_URL`, `SUPABASE_SERVICE_KEY`, `TELEGRAM_BOT_TOKEN`, `TELEGRAM_ALLOWED_CHAT_ID`).
2. Install Python dependencies and run the full test suite + lint checks:
   ```bash
   uv sync
   uv run ruff check src tests
   uv run pytest -v
   ```
3. Build the Web Command Center frontend (`frontend/`):
   ```bash
   cd frontend
   npm install
   npm run build
   ```
4. Launch the FastAPI Control Plane + bundled Web Command Center on `http://127.0.0.1:8420`:
   ```bash
   uv run uvicorn src.main:app --host 127.0.0.1 --port 8420
   ```
   For frontend hot-reload during development, run `npm run dev` inside `frontend/` (`http://127.0.0.1:5173`, proxied to `:8420`).

## Architecture Highlights
- **Zero-Auth Discovery (`src/ingestion/`):** Combines `python-jobspy`, public Greenhouse/Lever/Ashby board APIs, and read-only Gmail alert parsing without touching personal authenticated sessions.
- **Stealth Execution (`src/stealth_browser/` & `src/agent/`):** Uses `patchright` / `camoufox` persistent profiles (`~/.autoapply/profiles/{portal}`), cubic Bezier mouse curves, Gaussian typing delays, circadian IST windows, and portal circuit breakers.
- **HITL Escalation (`src/hitl/` & Web HITL Desk):** Pauses in `PAUSED_HITL` whenever any field has confidence `< 0.85`, `is_dream_company == True`, or a security/OTP challenge is detected, and learns verified human overrides back into `qa_cache`.
