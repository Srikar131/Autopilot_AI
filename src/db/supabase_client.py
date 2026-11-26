from __future__ import annotations

import os
import uuid
from datetime import date, datetime, timezone
from typing import Any

from src.brain.embeddings import cosine_similarity, is_configured_secret
from src.config import settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class SupabaseRepository:
    """Unified repository wrapper around Supabase PostgreSQL + pgvector with offline test fallback."""

    def __init__(self, client: Any | None = None) -> None:
        self._client = client
        self._local_jobs: dict[str, dict[str, Any]] = {}
        self._local_qa_cache: list[dict[str, Any]] = []
        self._local_events: list[dict[str, Any]] = []
        self._local_quota_counts: dict[tuple[str, str], int] = {}
        if (
            self._client is None
            and "PYTEST_CURRENT_TEST" not in os.environ
            and is_configured_secret(settings.supabase_url)
            and is_configured_secret(settings.supabase_service_key)
        ):
            try:
                from supabase import create_client

                self._client = create_client(settings.supabase_url, settings.supabase_service_key)
            except Exception:
                self._client = None

    @property
    def is_connected(self) -> bool:
        return self._client is not None

    def check_health(self) -> dict[str, Any]:
        """Verifies Supabase connectivity and whether the 001_init_schema.sql tables exist."""
        if not self._client:
            return {
                "client_configured": False,
                "schema_ready": False,
                "mode": "local_fallback",
                "detail": "Supabase client unavailable or running in offline test mode; using local memory store.",
            }
        try:
            self._client.table("jobs_queue").select("id").limit(1).execute()
            return {
                "client_configured": True,
                "schema_ready": True,
                "mode": "supabase",
                "detail": "Connected to Supabase and verified jobs_queue schema.",
            }
        except Exception as exc:
            return {
                "client_configured": True,
                "schema_ready": False,
                "mode": "local_fallback",
                "detail": f"Supabase reachable but schema check failed: {exc}",
            }

    def upsert_job(self, job_payload: dict[str, Any]) -> dict[str, Any]:
        job_id = str(
            job_payload.get("id")
            or job_payload.get("url_hash")
            or f"local-mock-job-{len(self._local_jobs) + 1}"
        )
        local_record = {
            **job_payload,
            "id": job_id,
            "discovered_at": job_payload.get("discovered_at") or _now_iso(),
        }
        self._local_jobs[job_id] = local_record
        if not self._client:
            return local_record
        try:
            db_payload = dict(job_payload)
            if db_payload.get("id") and len(str(db_payload["id"])) != 36:
                db_payload.pop("id", None)
            try:
                res = self._client.table("jobs_queue").upsert(db_payload, on_conflict="url_hash").execute()
            except Exception:
                if "external_job_id" in db_payload or "discovery_source" in db_payload:
                    db_payload.pop("external_job_id", None)
                    db_payload.pop("discovery_source", None)
                    res = self._client.table("jobs_queue").upsert(db_payload, on_conflict="url_hash").execute()
                else:
                    raise
            if res.data:
                record = res.data[0]
                self._local_jobs[str(record.get("id", job_id))] = record
                return record
            return local_record
        except Exception:
            return local_record

    def update_job_status(self, job_id: str, status: str, extra: dict[str, Any] | None = None) -> None:
        if job_id in self._local_jobs:
            self._local_jobs[job_id] = {**self._local_jobs[job_id], "status": status, **(extra or {})}
        if not self._client:
            return
        try:
            payload = {"status": status, **(extra or {})}
            self._client.table("jobs_queue").update(payload).eq("id", job_id).execute()
        except Exception:
            return

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        if job_id in self._local_jobs:
            return dict(self._local_jobs[job_id])
        if self._client:
            try:
                res = self._client.table("jobs_queue").select("*").eq("id", job_id).limit(1).execute()
                if res.data:
                    record = res.data[0]
                    self._local_jobs[str(record.get("id", job_id))] = record
                    return record
            except Exception:
                pass
        return None

    def list_jobs_by_status(
        self,
        portal: str | None = None,
        status: str = "READY_TO_APPLY",
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if self._client:
            try:
                query = self._client.table("jobs_queue").select("*").eq("status", status)
                if portal:
                    query = query.eq("portal", portal)
                res = query.limit(limit).execute()
                if res.data is not None:
                    return list(res.data)
            except Exception:
                pass
        matches = [
            job
            for job in self._local_jobs.values()
            if job.get("status") == status and (portal is None or job.get("portal") == portal)
        ]
        return matches[:limit]

    def list_all_jobs(
        self,
        portal: str | None = None,
        status: str | None = None,
        search: str | None = None,
        dream_only: bool = False,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Lists jobs across statuses with optional portal, text, and dream-company filters."""
        records: list[dict[str, Any]] | None = None
        if self._client:
            try:
                query = self._client.table("jobs_queue").select("*").order("discovered_at", desc=True)
                if portal:
                    query = query.eq("portal", portal)
                if status:
                    query = query.eq("status", status)
                if dream_only:
                    query = query.eq("is_dream_company", True)
                res = query.limit(limit).execute()
                if res.data is not None:
                    records = list(res.data)
                    for item in records:
                        if item.get("id"):
                            self._local_jobs[str(item["id"])] = item
            except Exception:
                records = None

        if records is None:
            records = list(self._local_jobs.values())
            records.sort(key=lambda r: str(r.get("discovered_at", "")), reverse=True)

        filtered: list[dict[str, Any]] = []
        needle = (search or "").strip().lower()
        for job in records:
            if portal and job.get("portal") != portal:
                continue
            if status and job.get("status") != status:
                continue
            if dream_only and not job.get("is_dream_company"):
                continue
            if needle:
                haystack = " ".join(
                    [
                        str(job.get("company_name", "")),
                        str(job.get("role_title", "")),
                        str(job.get("location", "")),
                        str(job.get("portal", "")),
                        str(job.get("external_job_id", "")),
                    ]
                ).lower()
                if needle not in haystack:
                    continue
            filtered.append(job)
        return filtered[:limit]

    def search_qa_cache(
        self,
        query_embedding: list[float],
        threshold: float = 0.88,
        top_k: int = 3,
        query_text: str = "",
    ) -> list[dict[str, Any]]:
        if self._client:
            try:
                res = self._client.rpc(
                    "match_qa_cache",
                    {"query_embedding": query_embedding, "match_threshold": threshold, "match_count": top_k},
                ).execute()
                if res.data:
                    return res.data
            except Exception:
                pass

        normalized_query = query_text.strip().lower()
        scored: list[dict[str, Any]] = []
        for record in self._local_qa_cache:
            q_norm = str(record.get("question_normalized", "")).strip().lower()
            if normalized_query and q_norm == normalized_query:
                sim = 1.0
            else:
                sim = cosine_similarity(record.get("question_embedding", []), query_embedding)
            if sim >= threshold:
                scored.append({**record, "similarity": round(sim, 4)})
        scored.sort(key=lambda r: float(r.get("similarity", 0.0)), reverse=True)
        return scored[:top_k]

    def insert_qa_cache(
        self,
        question_normalized: str,
        field_type: str,
        canonical_answer: str,
        question_embedding: list[float],
        portal_context: str = "general",
        source: str = "human_verified",
    ) -> dict[str, Any]:
        cleaned_q = question_normalized.strip()
        cleaned_a = canonical_answer.strip()
        now_iso = _now_iso()

        for existing in self._local_qa_cache:
            if (
                str(existing.get("question_normalized", "")).strip().lower() == cleaned_q.lower()
                and existing.get("portal_context", "general") == portal_context
            ):
                existing["canonical_answer"] = cleaned_a
                existing["field_type"] = field_type
                existing["question_embedding"] = question_embedding
                existing["source"] = source
                existing["usage_count"] = int(existing.get("usage_count", 1)) + 1
                existing["updated_at"] = now_iso
                return existing

        record = {
            "id": str(uuid.uuid4()),
            "question_normalized": cleaned_q,
            "field_type": field_type,
            "canonical_answer": cleaned_a,
            "portal_context": portal_context,
            "question_embedding": question_embedding,
            "source": source,
            "usage_count": 1,
            "created_at": now_iso,
            "updated_at": now_iso,
        }
        self._local_qa_cache.append(record)
        if not self._client:
            return record
        try:
            db_record = {
                "question_normalized": cleaned_q,
                "field_type": field_type,
                "canonical_answer": cleaned_a,
                "portal_context": portal_context,
                "question_embedding": question_embedding,
                "source": source,
            }
            res = self._client.table("qa_cache").insert(db_record).execute()
            return res.data[0] if res.data else record
        except Exception:
            return record

    def list_qa_cache(self, limit: int = 100) -> list[dict[str, Any]]:
        if self._client:
            try:
                res = (
                    self._client.table("qa_cache")
                    .select("id, question_normalized, field_type, canonical_answer, portal_context, source, usage_count, created_at, updated_at")
                    .order("updated_at", desc=True)
                    .limit(limit)
                    .execute()
                )
                if res.data is not None:
                    return list(res.data)
            except Exception:
                pass
        items = [
            {k: v for k, v in rec.items() if k != "question_embedding"}
            for rec in self._local_qa_cache
        ]
        items.sort(key=lambda r: str(r.get("updated_at", "")), reverse=True)
        return items[:limit]

    def delete_qa_cache(self, item_id: str) -> bool:
        removed = False
        before = len(self._local_qa_cache)
        self._local_qa_cache = [r for r in self._local_qa_cache if str(r.get("id")) != item_id]
        if len(self._local_qa_cache) < before:
            removed = True
        if self._client:
            try:
                self._client.table("qa_cache").delete().eq("id", item_id).execute()
                removed = True
            except Exception:
                pass
        return removed

    def record_application_event(self, event: dict[str, Any]) -> dict[str, Any]:
        record = {
            "id": str(event.get("id") or uuid.uuid4()),
            "job_id": str(event.get("job_id", "")),
            "langgraph_thread_id": str(event.get("langgraph_thread_id", "")),
            "event_type": str(event.get("event_type", "INFO")),
            "form_payload": event.get("form_payload"),
            "low_confidence_fields": event.get("low_confidence_fields"),
            "screenshot_path": event.get("screenshot_path"),
            "telegram_message_id": event.get("telegram_message_id"),
            "created_at": event.get("created_at") or _now_iso(),
        }
        self._local_events.insert(0, record)
        self._local_events = self._local_events[:200]
        if not self._client:
            return record
        try:
            db_payload = {
                "langgraph_thread_id": record["langgraph_thread_id"],
                "event_type": record["event_type"],
                "form_payload": record["form_payload"],
                "low_confidence_fields": record["low_confidence_fields"],
                "screenshot_path": record["screenshot_path"],
                "telegram_message_id": record["telegram_message_id"],
            }
            if len(record["job_id"]) == 36:
                db_payload["job_id"] = record["job_id"]
            res = self._client.table("application_events").insert(db_payload).execute()
            return res.data[0] if res.data else record
        except Exception:
            return record

    def clear_jobs(self, keep_applied: bool = True) -> int:
        """Clears discovered/pending job listings cleanly while preserving APPLIED history when requested."""
        to_delete = [
            jid
            for jid, rec in self._local_jobs.items()
            if not (keep_applied and rec.get("status") == "APPLIED")
        ]
        for jid in to_delete:
            self._local_jobs.pop(jid, None)
        deleted = len(to_delete)

        if self._client:
            try:
                query = self._client.table("jobs_queue").select("id")
                if keep_applied:
                    query = query.neq("status", "APPLIED")
                res = query.execute()
                rows = list(res.data or [])
                deleted = len(rows)
                if rows:
                    del_q = self._client.table("jobs_queue").delete()
                    if keep_applied:
                        del_q = del_q.neq("status", "APPLIED")
                    else:
                        del_q = del_q.neq("id", "00000000-0000-0000-0000-000000000000")
                    del_q.execute()
            except Exception:
                pass
        return deleted

    def list_application_events(self, job_id: str | None = None, limit: int = 50) -> list[dict[str, Any]]:
        if self._client:
            try:
                query = self._client.table("application_events").select("*").order("created_at", desc=True)
                if job_id:
                    query = query.eq("job_id", job_id)
                res = query.limit(limit).execute()
                if res.data is not None:
                    return list(res.data)
            except Exception:
                pass
        events = self._local_events
        if job_id:
            events = [e for e in events if str(e.get("job_id")) == str(job_id)]
        return events[:limit]

    def get_daily_quotas_summary(self, portal_limits: dict[str, int]) -> list[dict[str, Any]]:
        today_str = date.today().isoformat()
        summary: list[dict[str, Any]] = []
        db_rows: dict[str, dict[str, Any]] = {}
        if self._client:
            try:
                res = (
                    self._client.table("daily_quota_ledger")
                    .select("*")
                    .eq("quota_date", today_str)
                    .execute()
                )
                for row in res.data or []:
                    db_rows[str(row["portal"])] = row
            except Exception:
                pass

        for portal, max_limit in portal_limits.items():
            row = db_rows.get(portal)
            if row:
                applied = int(row.get("applied_count", 0))
                limit_val = int(row.get("max_daily_limit", max_limit))
                tripped = bool(row.get("circuit_breaker_tripped", False))
            else:
                applied = int(self._local_quota_counts.get((today_str, portal), 0))
                limit_val = max_limit
                tripped = False
            summary.append(
                {
                    "portal": portal,
                    "quota_date": today_str,
                    "applied_count": applied,
                    "max_daily_limit": limit_val,
                    "remaining": max(0, limit_val - applied),
                    "circuit_breaker_tripped": tripped,
                }
            )
        return summary

    def check_and_increment_daily_quota(
        self,
        portal: str,
        max_limit: int,
        increment: bool = True,
    ) -> tuple[bool, int]:
        """Returns (allowed, current_count) for the given portal today, incrementing on allow when increment=True."""
        today_str = date.today().isoformat()
        key = (today_str, portal)

        if self._client:
            try:
                res = (
                    self._client.table("daily_quota_ledger")
                    .select("*")
                    .eq("quota_date", today_str)
                    .eq("portal", portal)
                    .execute()
                )
                if not res.data:
                    initial_count = 1 if increment else 0
                    self._client.table("daily_quota_ledger").insert(
                        {
                            "quota_date": today_str,
                            "portal": portal,
                            "applied_count": initial_count,
                            "max_daily_limit": max_limit,
                        }
                    ).execute()
                    self._local_quota_counts[key] = initial_count
                    return True, initial_count
                row = res.data[0]
                current_count = int(row.get("applied_count", 0))
                if row.get("circuit_breaker_tripped") or current_count >= int(row.get("max_daily_limit", max_limit)):
                    return False, current_count
                if not increment:
                    return True, current_count
                new_count = current_count + 1
                (
                    self._client.table("daily_quota_ledger")
                    .update({"applied_count": new_count, "last_applied_at": _now_iso()})
                    .eq("quota_date", today_str)
                    .eq("portal", portal)
                    .execute()
                )
                self._local_quota_counts[key] = new_count
                return True, new_count
            except Exception:
                pass

        current = self._local_quota_counts.get(key, 0)
        if current >= max_limit:
            return False, current
        if not increment:
            return True, current
        new_count = current + 1
        self._local_quota_counts[key] = new_count
        return True, new_count
