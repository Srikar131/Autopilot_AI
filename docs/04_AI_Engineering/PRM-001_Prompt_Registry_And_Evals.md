---
document_id: PRM-001
title: System Prompt Registry, Structured Schemas & AI Evaluation Rubric
version: 1.0.0
status: Approved
owner: ai_engineering
created_at: 2026-11-02
last_updated: 2026-11-02
related_documents:
  - AGT-001
---

# 1. Prompt Registry
## `PROMPT_JD_SCORER_V1`
Evaluates a raw Job Description against the candidate's `profile_vault` JSON.
- Outputs strict JSON adhering to `JDScorerResult`:
  - `match_score` (0.0 to 100.0)
  - `matched_skills` (list[str])
  - `missing_critical_skills` (list[str])
  - `selected_project_titles` (list[str] — top 3 projects to include in the tailored LaTeX PDF)
  - `tailored_summary` (str — 2-sentence ATS-optimized summary)
  - `hard_blocker_detected` (bool — e.g., requires 10+ YoE or US Security Clearance)

## `PROMPT_FIELD_SYNTHESIZER_V1`
Synthesizes answers for application form fields when `qa_cache` cosine similarity is `< 0.92`.
- Enforces strict confidence calibration:
  - `>= 0.85`: Explicit factual match found in `profile_vault` (Notice Period, Current/Expected CTC, Years of Experience in Python/AI, Degree, Location).
  - `< 0.85`: Subjective open-ended essay, ambiguous legal/bond terms, or unfamiliar technology. Triggers LangGraph `interrupt()`.
