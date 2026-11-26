from __future__ import annotations

import hashlib
import math
import os
import re

from src.config import settings

EMBEDDING_DIM = 768
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def is_configured_secret(value: str | None) -> bool:
    """Returns True if an environment credential is set and not a sample placeholder."""
    if not value:
        return False
    cleaned = value.strip().strip('"').lower()
    if not cleaned or cleaned == "0":
        return False
    placeholders = (
        "your-",
        "your-project.supabase.co",
        "1234567890",
        "postgres:password@",
    )
    return not any(p in cleaned for p in placeholders)


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm <= 1e-12:
        fallback = [0.0] * len(vec)
        if fallback:
            fallback[0] = 1.0
        return fallback
    return [round(v / norm, 6) for v in vec]


def deterministic_local_embedding(text: str, dim: int = EMBEDDING_DIM) -> list[float]:
    """
    Computes a deterministic, L2-normalized 768-dim lexical hash embedding.
    Produces non-zero unit vectors so pgvector cosine similarity never yields NaN offline.
    """
    cleaned = " ".join(_TOKEN_RE.findall(text.lower()))
    if not cleaned:
        cleaned = "empty_query"

    tokens = cleaned.split()
    features: list[str] = list(tokens)
    for i in range(len(tokens) - 1):
        features.append(f"{tokens[i]}_{tokens[i + 1]}")
    compact = f" {cleaned} "
    for i in range(max(0, len(compact) - 2)):
        features.append(compact[i : i + 3])

    vec = [0.0] * dim
    for feat in features:
        digest = hashlib.sha256(feat.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "big") % dim
        sign = 1.0 if (digest[4] & 1) == 0 else -1.0
        weight = 1.5 if "_" not in feat and len(feat) > 3 else 1.0
        vec[idx] += sign * weight

    return _l2_normalize(vec)


def cosine_similarity(vec_a: list[float], vec_b: list[float]) -> float:
    """Computes safe cosine similarity between two equal-length vectors."""
    if not vec_a or not vec_b or len(vec_a) != len(vec_b):
        return 0.0
    dot = sum(a * b for a, b in zip(vec_a, vec_b))
    norm_a = math.sqrt(sum(a * a for a in vec_a))
    norm_b = math.sqrt(sum(b * b for b in vec_b))
    if norm_a <= 1e-12 or norm_b <= 1e-12:
        return 0.0
    return float(dot / (norm_a * norm_b))


def embed_text(text: str) -> list[float]:
    """
    Generates a 768-dim L2-normalized embedding via Gemini `gemini-embedding-001` when configured
    outside pytest, with deterministic local embedding fallback for offline/test determinism.
    """
    if (
        "PYTEST_CURRENT_TEST" not in os.environ
        and is_configured_secret(settings.gemini_api_key)
        and text.strip()
    ):
        try:
            from google import genai

            client = genai.Client(api_key=settings.gemini_api_key)
            resp = client.models.embed_content(
                model="gemini-embedding-001",
                contents=text,
                config={"output_dimensionality": EMBEDDING_DIM},
            )
            embeddings = getattr(resp, "embeddings", None)
            if embeddings and getattr(embeddings[0], "values", None):
                values = [float(x) for x in embeddings[0].values]
                if len(values) == EMBEDDING_DIM:
                    return _l2_normalize(values)
        except Exception:
            pass

    return deterministic_local_embedding(text, dim=EMBEDDING_DIM)
