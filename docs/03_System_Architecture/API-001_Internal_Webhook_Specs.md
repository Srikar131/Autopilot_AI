---
document_id: API-001
title: Internal FastAPI Control & Telegram HITL Callback Contracts
version: 1.0.0
status: Approved
owner: backend_engineering
created_at: 2026-11-02
last_updated: 2026-11-02
related_documents:
  - HLD-001
---
# FastAPI Endpoints (`src/main.py`)
- `GET /health`
- `POST /api/v1/orchestrator/trigger-batch`
- `POST /api/v1/hitl/resume`
- `POST /api/v1/circuit-breaker/trip`
