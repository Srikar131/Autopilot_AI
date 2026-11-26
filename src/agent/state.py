from __future__ import annotations

from typing import Dict, List, Literal, NotRequired, Optional, TypedDict


class FormFieldSpec(TypedDict):
    field_id: str
    label_text: str
    field_type: Literal["text", "textarea", "number", "select", "radio", "checkbox", "file"]
    options: Optional[List[str]]
    proposed_value: Optional[str]
    confidence: float
    reasoning: str
    bounding_box: Optional[Dict[str, int]]
    required: NotRequired[bool]
    is_combobox: NotRequired[bool]


class ApplicationAgentState(TypedDict):
    job_id: str
    portal: str
    apply_url: str
    company_name: str
    role_title: str
    tailored_pdf_path: str
    is_dream_company: bool
    current_step_index: int
    extracted_fields: List[FormFieldSpec]
    low_confidence_fields: List[FormFieldSpec]
    security_blocker_detected: bool
    human_resolution: Optional[Dict[str, str]]
    final_status: Literal["IN_PROGRESS", "PAUSED_HITL", "APPLIED", "SKIPPED", "ABORTED"]
    jd_raw_text: NotRequired[str]
    screenshot_path: NotRequired[Optional[str]]
    force_supervised_hitl: NotRequired[bool]
    blocker_reason: NotRequired[Optional[str]]
    updated_at: NotRequired[str]
