from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

# Zero-auth public JSON boards monitored continuously without manual company entry
BUILTIN_ATS_COMPANIES: dict[str, list[str]] = {
    "greenhouse": [
        "razorpay",
        "stripe",
        "databricks",
        "cloudflare",
        "postman",
        "mongodb",
        "rubrik",
        "twilio",
        "gitlab",
        "grafana",
        "elastic",
        "pinterest",
        "reddit",
        "coinbase",
        "robinhood",
        "figma",
        "samsara",
        "vercel",
        "togetherai",
        "fireworksai",
        "scaleai",
        "arizeai",
        "weightsandbiases",
        "brex",
        "gusto",
        "affirm",
        "instacart",
        "dropbox",
        "asana",
        "duolingo",
        "coursera",
        "airbnb",
        "doordash",
        "okta",
        "zscaler",
        "crowdstrike",
        "xai",
        "canonical",
        "cockroachlabs",
        "temporaltechnologies",
        "algolia",
        "contentful",
        "intercom",
        "zendesk",
        "hubspot",
    ],
    "lever": [
        "zepto",
        "cred",
        "meesho",
        "groww",
        "plaid",
        "palantir",
        "mistral",
        "zoox",
        "shieldai",
        "attentive",
        "outreach",
        "highspot",
        "veeva",
        "benchling",
        "lattice",
        "ro",
        "color",
        "voleon",
    ],
    "ashby": [
        "openai",
        "anthropic",
        "perplexity",
        "linear",
        "notion",
        "langchain",
        "elevenlabs",
        "cartesia",
        "groq",
        "cohere",
        "supabase",
        "ramp",
        "deel",
        "vanta",
        "replit",
        "cursor",
        "modal",
        "baseten",
        "replicate",
        "pinecone",
        "weaviate",
        "qdrant",
        "posthog",
        "cal",
        "resend",
        "render",
        "railway",
        "clerk",
        "neon",
    ],
    "smartrecruiters": [
        "visa",
        "bosch",
        "servicenow",
        "paloaltonetworks",
        "nagarro",
        "publicissapient",
        "nielseniq",
    ],
}

_ATS_SLUG_PATTERNS: list[tuple[str, re.Pattern[str]]] = [
    ("greenhouse", re.compile(r"https?://(?:boards|job-boards)\.greenhouse\.io/([a-zA-Z0-9_-]+)", re.IGNORECASE)),
    ("lever", re.compile(r"https?://jobs\.lever\.co/([a-zA-Z0-9_-]+)", re.IGNORECASE)),
    ("ashby", re.compile(r"https?://jobs\.ashbyhq\.com/([a-zA-Z0-9_.-]+)", re.IGNORECASE)),
    ("smartrecruiters", re.compile(r"https?://jobs\.smartrecruiters\.com/([a-zA-Z0-9_-]+)", re.IGNORECASE)),
]
_IGNORED_SLUGS = {"embed", "v1", "v0", "posting-api", "jobs", "postings"}


class CompanyATSRegistry:
    """
    Self-expanding master registry of company ATS boards across Greenhouse, Lever, Ashby,
    and SmartRecruiters. Pre-loaded with 95+ companies and auto-learns new company slugs
    discovered via JobSpy, Gmail alerts, or open-web ATS dork searches.
    """

    def __init__(self, registry_path: str | Path = "data/company_ats_registry.json") -> None:
        self.path = Path(registry_path)

    def _default_state(self) -> dict[str, Any]:
        return {
            "custom_added": {ats: [] for ats in BUILTIN_ATS_COMPANIES},
            "auto_discovered": {ats: [] for ats in BUILTIN_ATS_COMPANIES},
        }

    def load(self) -> dict[str, Any]:
        state = self._default_state()
        if not self.path.exists():
            return state
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            if not isinstance(raw, dict):
                return state
            for bucket in ("custom_added", "auto_discovered"):
                src_bucket = raw.get(bucket, {})
                if isinstance(src_bucket, dict):
                    for ats in BUILTIN_ATS_COMPANIES:
                        vals = src_bucket.get(ats, [])
                        if isinstance(vals, list):
                            state[bucket][ats] = sorted({str(s).strip().lower() for s in vals if str(s).strip()})
            return state
        except Exception:
            return state

    def _save(self, state: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(state, indent=2), encoding="utf-8")

    def get_all_slugs(self) -> dict[str, list[str]]:
        state = self.load()
        merged: dict[str, list[str]] = {}
        for ats, defaults in BUILTIN_ATS_COMPANIES.items():
            combined = set(defaults)
            combined.update(state["custom_added"].get(ats, []))
            combined.update(state["auto_discovered"].get(ats, []))
            merged[ats] = sorted(combined)
        return merged

    def add_slug(self, ats: str, slug: str, source: str = "manual") -> dict[str, Any]:
        norm_ats = ats.strip().lower()
        norm_slug = slug.strip().lower()
        if norm_ats not in BUILTIN_ATS_COMPANIES:
            raise ValueError(f"Unsupported ATS board type: {ats}")
        if not norm_slug or norm_slug in _IGNORED_SLUGS:
            raise ValueError(f"Invalid company slug: {slug}")

        state = self.load()
        bucket = "auto_discovered" if source == "auto_discovered" else "custom_added"
        all_existing = set(BUILTIN_ATS_COMPANIES[norm_ats])
        all_existing.update(state["custom_added"].get(norm_ats, []))
        all_existing.update(state["auto_discovered"].get(norm_ats, []))

        added = False
        if norm_slug not in all_existing:
            existing = set(state[bucket].get(norm_ats, []))
            existing.add(norm_slug)
            state[bucket][norm_ats] = sorted(existing)
            self._save(state)
            added = True

        return {"ats": norm_ats, "slug": norm_slug, "added": added, "source": source}

    def register_company_from_url(self, url: str) -> tuple[str, str] | None:
        """Extracts ATS provider & company slug from any job URL and persists newly seen companies."""
        cleaned = url.strip()
        for ats, pattern in _ATS_SLUG_PATTERNS:
            match = pattern.search(cleaned)
            if not match:
                continue
            slug = match.group(1).strip().lower()
            if not slug or slug in _IGNORED_SLUGS:
                return None
            self.add_slug(ats, slug, source="auto_discovered")
            return ats, slug
        return None

    def summary(self) -> dict[str, Any]:
        state = self.load()
        all_slugs = self.get_all_slugs()
        counts = {ats: len(slugs) for ats, slugs in all_slugs.items()}
        return {
            "total_companies": sum(counts.values()),
            "counts_by_ats": counts,
            "slugs_by_ats": all_slugs,
            "auto_discovered": state["auto_discovered"],
            "custom_added": state["custom_added"],
        }
