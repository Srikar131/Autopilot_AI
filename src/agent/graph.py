from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph

from src.agent.state import ApplicationAgentState, FormFieldSpec
from src.brain.embeddings import embed_text
from src.brain.qa_rag import QAResolutionEngine
from src.config import settings
from src.db.supabase_client import SupabaseRepository
from src.hitl.telegram_bot import TelegramHITLNotifier
from src.stealth_browser.circuit_breaker import PortalCircuitBreaker


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class AutoApplyOrchestrator:
    """
    Stateful LangGraph Agent Orchestrator implementing the AGT-001 specification:
    1. Checks Circadian Window & Portal Circuit Breaker
    2. Scores all extracted form fields via QAResolutionEngine (3-tier RAG + Vault + Gemini)
    3. Pauses execution (PAUSED_HITL) and dispatches a Telegram card if any field confidence < 0.85,
       if security_blocker_detected is True, or if is_dream_company is True
    4. Resumes from checkpoint when human approves/edits via Telegram/Dashboard and learns into qa_cache
    """

    def __init__(
        self,
        circuit_breaker: PortalCircuitBreaker | None = None,
        qa_engine: QAResolutionEngine | None = None,
        notifier: TelegramHITLNotifier | None = None,
        repo: SupabaseRepository | None = None,
    ) -> None:
        self.circuit_breaker = circuit_breaker or PortalCircuitBreaker()
        self.repo = repo or SupabaseRepository()
        self._custom_qa_engine = qa_engine is not None
        self.qa_engine = qa_engine or QAResolutionEngine(repo=self.repo)
        self.notifier = notifier or TelegramHITLNotifier()
        self._checkpoints: dict[str, ApplicationAgentState] = {}
        self._graph = self._build_state_graph()

    def _build_state_graph(self):
        builder = StateGraph(ApplicationAgentState)

        async def check_guardrails(state: ApplicationAgentState, config: RunnableConfig) -> dict[str, Any]:
            now_override = config.get("configurable", {}).get("now")
            allowed, reason = self.circuit_breaker.can_execute(state["portal"], now=now_override)
            if not allowed:
                return {
                    "final_status": "ABORTED",
                    "blocker_reason": reason,
                    "updated_at": _now_iso(),
                }

            if self.circuit_breaker.inspect_url_for_blockers(state["portal"], state["apply_url"]):
                return {
                    "security_blocker_detected": True,
                    "blocker_reason": self.circuit_breaker.tripped_portals.get(
                        state["portal"], "Security blocker URL detected"
                    ),
                    "low_confidence_fields": [],
                    "final_status": "PAUSED_HITL",
                    "updated_at": _now_iso(),
                }

            return {"updated_at": _now_iso()}

        def route_after_guardrails(state: ApplicationAgentState) -> str:
            if state.get("final_status") == "ABORTED":
                return "aborted"
            if state.get("security_blocker_detected") and state.get("final_status") == "PAUSED_HITL":
                return "escalate"
            return "resolve"

        async def resolve_fields(state: ApplicationAgentState) -> dict[str, Any]:
            raw_fields = state.get("extracted_fields", [])
            if not self._custom_qa_engine:
                self.qa_engine.repo = self.repo
                if hasattr(self.qa_engine, "resolve_fields_batch") and raw_fields:
                    resolved_fields = await asyncio.to_thread(
                        self.qa_engine.resolve_fields_batch,
                        raw_fields,
                        {
                            "company_name": state.get("company_name", ""),
                            "role_title": state.get("role_title", ""),
                            "jd_raw_text": state.get("jd_raw_text", ""),
                            "portal": state.get("portal", ""),
                        },
                    )
                else:
                    resolved_fields = [self.qa_engine.resolve_field(f) for f in raw_fields]
            else:
                resolved_fields = [self.qa_engine.resolve_field(f) for f in raw_fields]

            low_conf_fields = [
                f for f in resolved_fields if f["confidence"] < settings.confidence_threshold
            ]

            return {
                "extracted_fields": resolved_fields,
                "low_confidence_fields": low_conf_fields,
                "updated_at": _now_iso(),
            }

        def route_after_resolution(state: ApplicationAgentState) -> str:
            needs_human = (
                len(state.get("low_confidence_fields", [])) > 0
                or state.get("security_blocker_detected", False)
                or state.get("is_dream_company", False)
                or state.get("force_supervised_hitl", False)
            )
            return "escalate" if needs_human else "apply"

        async def escalate_hitl(state: ApplicationAgentState, config: RunnableConfig) -> dict[str, Any]:
            thread_id = str(config.get("configurable", {}).get("thread_id", f"thread-{state['job_id']}"))
            low_fields = state.get("low_confidence_fields", [])
            screenshot_path = state.get("screenshot_path")
            pdf_name = state["tailored_pdf_path"].replace("\\", "/").split("/")[-1]
            await self.notifier.send_escalation_card(
                thread_id=thread_id,
                company_name=state["company_name"],
                role_title=state["role_title"],
                portal=state["portal"],
                low_confidence_fields=low_fields,
                pdf_filename=pdf_name,
                screenshot_path=screenshot_path,
            )
            self.repo.update_job_status(state["job_id"], "PAUSED_HITL")
            self.repo.record_application_event(
                {
                    "job_id": state["job_id"],
                    "langgraph_thread_id": thread_id,
                    "event_type": "PAUSED_HITL",
                    "low_confidence_fields": low_fields,
                    "screenshot_path": screenshot_path,
                }
            )
            return {"final_status": "PAUSED_HITL", "updated_at": _now_iso()}

        async def finalize_apply(state: ApplicationAgentState, config: RunnableConfig) -> dict[str, Any]:
            thread_id = str(config.get("configurable", {}).get("thread_id", f"thread-{state['job_id']}"))
            now_iso = _now_iso()
            self.repo.update_job_status(state["job_id"], "APPLIED", {"applied_at": now_iso})
            self.repo.record_application_event(
                {
                    "job_id": state["job_id"],
                    "langgraph_thread_id": thread_id,
                    "event_type": "APPLIED",
                    "form_payload": state.get("extracted_fields", []),
                }
            )
            return {
                "low_confidence_fields": [],
                "final_status": "APPLIED",
                "updated_at": now_iso,
            }

        async def record_aborted(state: ApplicationAgentState, config: RunnableConfig) -> dict[str, Any]:
            thread_id = str(config.get("configurable", {}).get("thread_id", f"thread-{state['job_id']}"))
            self.repo.record_application_event(
                {
                    "job_id": state["job_id"],
                    "langgraph_thread_id": thread_id,
                    "event_type": "ABORTED_CIRCUIT_BREAKER",
                }
            )
            return {"final_status": "ABORTED", "updated_at": _now_iso()}

        builder.add_node("check_guardrails", check_guardrails)
        builder.add_node("resolve_fields", resolve_fields)
        builder.add_node("escalate_hitl", escalate_hitl)
        builder.add_node("finalize_apply", finalize_apply)
        builder.add_node("record_aborted", record_aborted)

        builder.add_edge(START, "check_guardrails")
        builder.add_conditional_edges(
            "check_guardrails",
            route_after_guardrails,
            {
                "aborted": "record_aborted",
                "escalate": "escalate_hitl",
                "resolve": "resolve_fields",
            },
        )
        builder.add_conditional_edges(
            "resolve_fields",
            route_after_resolution,
            {
                "escalate": "escalate_hitl",
                "apply": "finalize_apply",
            },
        )
        builder.add_edge("record_aborted", END)
        builder.add_edge("escalate_hitl", END)
        builder.add_edge("finalize_apply", END)

        return builder.compile()

    async def evaluate_and_step(
        self,
        thread_id: str,
        state: ApplicationAgentState,
        now: Any | None = None,
    ) -> ApplicationAgentState:
        result: ApplicationAgentState = await self._graph.ainvoke(
            state,
            config={"configurable": {"thread_id": thread_id, "now": now}},
        )
        self._checkpoints[thread_id] = result
        return result

    def attach_screenshot(self, thread_id: str, screenshot_path: str) -> None:
        if thread_id in self._checkpoints:
            self._checkpoints[thread_id] = {
                **self._checkpoints[thread_id],
                "screenshot_path": screenshot_path,
                "updated_at": _now_iso(),
            }

    def list_threads(self) -> list[dict[str, Any]]:
        items = [{"thread_id": tid, **state} for tid, state in self._checkpoints.items()]
        items.sort(
            key=lambda item: (
                0 if item.get("final_status") == "PAUSED_HITL" else 1,
                str(item.get("updated_at", "")),
            )
        )
        return items

    async def resume_from_human(
        self,
        thread_id: str,
        action: str,
        field_overrides: dict[str, str] | None = None,
        save_to_qa_cache: bool = True,
    ) -> ApplicationAgentState:
        """Resumes a paused thread after the user taps Approve, Edit, or Skip via Telegram or Web Desk."""
        if thread_id not in self._checkpoints:
            raise KeyError(f"Unknown LangGraph thread_id: {thread_id}")

        state = self._checkpoints[thread_id]
        now_iso = _now_iso()
        if action == "SKIP":
            skipped: ApplicationAgentState = {**state, "final_status": "SKIPPED", "updated_at": now_iso}
            self._checkpoints[thread_id] = skipped
            self.repo.update_job_status(state["job_id"], "SKIPPED_BY_USER")
            self.repo.record_application_event(
                {
                    "job_id": state["job_id"],
                    "langgraph_thread_id": thread_id,
                    "event_type": "SKIPPED_BY_USER",
                }
            )
            return skipped

        overrides = field_overrides or {}
        updated_fields: list[FormFieldSpec] = []
        for f in state["extracted_fields"]:
            fid = f["field_id"]
            if fid in overrides:
                new_val = overrides[fid]
                updated_fields.append(
                    {
                        **f,
                        "proposed_value": new_val,
                        "confidence": 1.0,
                        "reasoning": "Human verified override via HITL",
                    }
                )
                if save_to_qa_cache:
                    self.repo.insert_qa_cache(
                        question_normalized=f["label_text"],
                        field_type=f["field_type"],
                        canonical_answer=new_val,
                        question_embedding=embed_text(f["label_text"]),
                        portal_context=state["portal"],
                        source="human_verified",
                    )
            else:
                updated_fields.append({**f, "confidence": 1.0})

        completed: ApplicationAgentState = {
            **state,
            "extracted_fields": updated_fields,
            "low_confidence_fields": [],
            "human_resolution": overrides,
            "final_status": "APPLIED",
            "updated_at": now_iso,
        }
        self._checkpoints[thread_id] = completed
        self.repo.update_job_status(state["job_id"], "APPLIED", {"applied_at": now_iso})
        self.repo.record_application_event(
            {
                "job_id": state["job_id"],
                "langgraph_thread_id": thread_id,
                "event_type": "APPLIED",
                "form_payload": updated_fields,
            }
        )
        return completed
