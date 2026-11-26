from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

from src.config import settings
from src.db.seed_profile_vault import DEFAULT_CANDIDATE_PROFILE

DEFAULT_DREAM_COMPANIES: list[str] = [
    "google",
    "deepmind",
    "openai",
    "anthropic",
    "stripe",
    "razorpay",
    "zepto",
    "cred",
    "uber",
    "microsoft",
]

DEFAULT_SEARCH_PREFERENCES: dict[str, Any] = {
    "target_roles": [
        "AI Engineer",
        "Machine Learning Engineer",
        "GenAI Engineer",
        "LLM Engineer",
        "Python Backend Engineer",
        "Software Engineer - AI",
    ],
    "search_terms": [
        "AI Engineer",
        "Machine Learning Engineer",
        "GenAI Developer",
        "Python Backend Engineer",
    ],
    "locations": [
        "Hyderabad, Telangana, India",
        "Bengaluru, Karnataka, India",
        "Chennai, Tamil Nadu, India",
    ],
    "work_mode_preference": "any",
    "min_salary_lpa": 0,
    "target_salary_lpa": 18,
    "experience_years_range": "0-3",
    "must_have_keywords": [],
    "excluded_keywords": [],
    "latest_blueprint": None,
    "ats_boards": {
        "greenhouse": [],
        "lever": [],
        "ashby": [],
    },
}


class ProfileVaultStore:
    """File-backed JSON persistence for the candidate Profile Vault, Dream Companies, and Search Config."""

    def __init__(self, vault_path: str | Path | None = None) -> None:
        self.path = Path(vault_path or settings.profile_vault_path)

    def _default_payload(self) -> dict[str, Any]:
        base = copy.deepcopy(DEFAULT_CANDIDATE_PROFILE)
        base.setdefault("demographics", {})
        base["demographics"].setdefault("linkedin_url", "")
        base["demographics"].setdefault("github_url", "")
        base["demographics"].setdefault("portfolio_url", "")
        base["demographics"].setdefault("current_company", "")
        base["demographics"].setdefault("current_role", "")
        base["demographics"].setdefault("current_ctc_lpa", "")
        base["demographics"].setdefault("expected_ctc_lpa", "")
        base["dream_companies"] = list(DEFAULT_DREAM_COMPANIES)
        base["search_preferences"] = copy.deepcopy(DEFAULT_SEARCH_PREFERENCES)
        return base

    def load(self) -> dict[str, Any]:
        """Loads profile vault from disk, initializing defaults on first access."""
        defaults = self._default_payload()
        if not self.path.exists():
            return self.save(defaults)
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return defaults
            merged = {**defaults, **raw}
            merged["demographics"] = {**defaults["demographics"], **raw.get("demographics", {})}
            merged["skills_years"] = raw.get("skills_years", defaults["skills_years"])
            merged["projects"] = raw.get("projects", defaults["projects"])
            merged["dream_companies"] = raw.get("dream_companies", defaults["dream_companies"])
            merged["search_preferences"] = {
                **defaults["search_preferences"],
                **raw.get("search_preferences", {}),
            }
            return merged
        except Exception:
            return defaults

    def save(self, payload: dict[str, Any]) -> dict[str, Any]:
        """Persists updated profile vault cleanly to disk and returns normalized payload."""
        defaults = self._default_payload()
        merged = {**defaults, **payload}
        merged["demographics"] = {**defaults["demographics"], **payload.get("demographics", {})}
        merged["search_preferences"] = {
            **defaults["search_preferences"],
            **payload.get("search_preferences", {}),
        }
        merged["dream_companies"] = [
            str(c).strip().lower() for c in merged.get("dream_companies", []) if str(c).strip()
        ]
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(merged, indent=2), encoding="utf-8")
        return merged

    def is_placeholder_contact(self, profile: dict[str, Any] | None = None) -> bool:
        """Detects whether the active Profile Vault still uses sample placeholder contact info."""
        prof = profile or self.load()
        demo = prof.get("demographics", {})
        email = str(demo.get("email", "")).strip().lower()
        phone = str(demo.get("phone", "")).strip()
        return email.endswith("@example.com") or phone == "+91-9876543210" or not email
