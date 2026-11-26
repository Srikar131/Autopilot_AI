from __future__ import annotations

import hashlib
from urllib.parse import urlparse, urlunparse


def canonicalize_job_url(raw_url: str) -> tuple[str, str]:
    """Strips tracking query params and returns (canonical_url, sha256_hash)."""
    parsed = urlparse(raw_url.strip())
    clean = urlunparse((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/"), "", "", ""))
    url_hash = hashlib.sha256(clean.encode("utf-8")).hexdigest()
    return clean, url_hash
