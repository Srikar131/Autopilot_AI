---
document_id: ADR-002
title: Architecture Decision Record — Unified FastAPI & React Command Center + E2E Fallbacks
version: 1.0.0
status: Approved
owner: principal_ai_architect
created_at: 2026-11-24
last_updated: 2026-11-24
related_documents:
  - ADR-001
  - HLD-001
  - API-001
---

# Decision
1. **Unified Control Plane & Web Command Center:** Serve the React + Vite + Tailwind v4 Command Center from `frontend/dist/` directly inside the FastAPI Control Plane (`src/main.py`) alongside REST endpoints for Discovery, Jobs Queue, Tailored Resume Preview, Desktop HITL Escalation Desk, Profile Vault, Q&A Semantic Memory (`qa_cache`), and Anti-Ban Circuit Breakers.
2. **Real 768-Dim Embeddings with Deterministic Offline Fallback:** Implement `src/brain/embeddings.py` targeting Gemini `gemini-embedding-001` (`output_dimensionality=768`) with L2-normalized local lexical hashing when offline or under pytest, guaranteeing non-zero 768-dim vectors in `qa_cache`.
3. **ATS-Readable PDF Compilation Fallback:** Retain Tectonic LaTeX (`\VAR{}` / `\BLOCK{}`) as the primary resume compiler and replace the empty byte-header fallback with ReportLab 1-page A4 resume generation (`render_fallback_pdf`) when the `tectonic` CLI binary is absent.
4. **Zero-Auth Direct ATS Board Ingestion:** Add public JSON board fetchers for Greenhouse, Lever, and Ashby (`src/ingestion/ats_scraper.py`) plus read-only Gmail IMAP polling (`src/ingestion/gmail_ingestor.py`), keeping browser profiles strictly isolated from discovery traffic.
