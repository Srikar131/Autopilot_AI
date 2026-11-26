---
document_id: BRD-001
title: Business Requirements Document (BRD) & 90-Day Product Roadmap
version: 1.0.0
status: Approved
owner: product_engineering
created_at: 2026-11-02
last_updated: 2026-11-02
related_documents:
  - PRD-001
---

# 1. Executive Summary & ROI Analysis
Manual job hunting requires 3–4 hours daily for 10–15 applications (~18 minutes per tailored application). AutoApply-AI reduces human time investment to <=15 minutes/day via mobile Telegram approvals while scaling output to 150–220 tailored applications/day across isolated platform buckets.

# 2. 90-Day Rollout Roadmap
- **Week 1 (Foundation & Stealth Audit):** Local workspace setup, Supabase `pgvector` initialization, Camoufox/Patchright fingerprint validation on CreepJS/PixelScan.
- **Week 2 (Burner Account Soak Test):** 100% Supervised HITL mode on a burner Naukri account + unauthenticated Direct ATS boards (Greenhouse/Lever/Ashby).
- **Weeks 3–4 (Primary Profile Co-Pilot & RAG Warmup):** Attach primary profiles locally over home ISP; build `qa_cache` to 200+ verified recruiter Q&A pairs.
- **Month 2 (Sniper Mode Autopilot):** Auto-submit when field confidence >= 0.85; sub-20-minute latency from job alert email to submitted application.
- **Month 3 (Recruiter Warm Outreach Loop):** Automated post-application referral/recruiter note drafting.
