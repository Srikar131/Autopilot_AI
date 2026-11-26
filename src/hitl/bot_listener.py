from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx
from aiogram import Bot, Dispatcher, F, Router
from aiogram.types import CallbackQuery, Message

from src.brain.embeddings import embed_text
from src.config import settings
from src.db.supabase_client import SupabaseRepository

VALID_HITL_COMMANDS = {"approve", "skip", "edit"}
KEY_VALUE_LINE_RE = re.compile(r"^([a-z][a-z0-9_.-]{1,63})\s*[:=]\s*(.+)$")


def parse_hitl_callback_data(callback_data: str) -> tuple[str, str]:
    """Parses Telegram inline callback data of the form '<approve|skip|edit>:<thread_id>'."""
    raw = callback_data.strip()
    if ":" not in raw:
        raise ValueError(f"Invalid HITL callback_data format: {callback_data!r}")
    command, thread_id = raw.split(":", 1)
    command = command.strip().lower()
    thread_id = thread_id.strip()
    if command not in VALID_HITL_COMMANDS or not thread_id:
        raise ValueError(f"Unsupported HITL callback command or empty thread_id: {callback_data!r}")
    return command, thread_id


def parse_field_override_reply(
    reply_text: str,
    default_field_id: str = "custom_response",
) -> dict[str, str]:
    """
    Parses a Telegram reply into field_overrides.
    Supports either multi-line 'field_id: value' / 'field_id=value' or plain text for default_field_id.
    """
    cleaned = reply_text.strip()
    if not cleaned:
        raise ValueError("Reply text cannot be empty.")

    lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
    parsed_kv: dict[str, str] = {}
    all_kv = True
    for line in lines:
        match = KEY_VALUE_LINE_RE.match(line)
        if not match:
            all_kv = False
            break
        key, value = match.group(1).strip(), match.group(2).strip()
        if not value:
            all_kv = False
            break
        parsed_kv[key] = value

    if all_kv and parsed_kv:
        return parsed_kv
    return {default_field_id: cleaned}


class TelegramHITLBotListener:
    """
    Two-way Telegram HITL handler built on aiogram + httpx.
    Supports /start / /id pairing, inline approve/skip/edit callbacks, and text replies.
    """

    def __init__(
        self,
        bot_token: str | None = None,
        allowed_chat_id: int | None = None,
        resume_endpoint_url: str = "http://127.0.0.1:8420/api/v1/hitl/resume",
        repo: SupabaseRepository | None = None,
        http_client: Any | None = None,
        resume_callback: Callable[[str, str, dict[str, str] | None, bool], Awaitable[dict[str, Any]]]
        | None = None,
    ) -> None:
        self.bot_token = bot_token if bot_token is not None else settings.telegram_bot_token
        self.allowed_chat_id = (
            allowed_chat_id if allowed_chat_id is not None else settings.telegram_allowed_chat_id
        )
        self.resume_endpoint_url = resume_endpoint_url
        self.repo = repo or SupabaseRepository()
        self.http_client = http_client
        self.resume_callback = resume_callback
        self.pending_edits: dict[int, dict[str, str]] = {}

    def _validate_chat(self, chat_id: int) -> None:
        if self.allowed_chat_id and chat_id != self.allowed_chat_id:
            raise PermissionError(f"Unauthorized Telegram chat_id: {chat_id}")

    async def post_hitl_resume(
        self,
        thread_id: str,
        action: str,
        field_overrides: dict[str, str] | None = None,
        save_to_qa_cache: bool = True,
        field_metadata: dict[str, dict[str, str]] | None = None,
    ) -> dict[str, Any]:
        if self.resume_callback is not None:
            return await self.resume_callback(thread_id, action, field_overrides, save_to_qa_cache)

        payload = {
            "langgraph_thread_id": thread_id,
            "action": action,
            "field_overrides": field_overrides,
            "save_to_qa_cache": save_to_qa_cache,
        }

        if self.http_client is not None:
            resp = await self.http_client.post(self.resume_endpoint_url, json=payload)
        else:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.post(self.resume_endpoint_url, json=payload)

        if hasattr(resp, "raise_for_status"):
            resp.raise_for_status()
        result: dict[str, Any] = resp.json() if hasattr(resp, "json") else dict(resp)

        if save_to_qa_cache and field_overrides and self.repo is not None:
            meta = field_metadata or {}
            for field_id, answer in field_overrides.items():
                field_info = meta.get(field_id, {})
                question_text = field_info.get("label_text", field_id)
                self.repo.insert_qa_cache(
                    question_normalized=question_text,
                    field_type=field_info.get("field_type", "text"),
                    canonical_answer=answer,
                    question_embedding=embed_text(question_text),
                    portal_context=field_info.get("portal", "telegram_hitl"),
                    source="human_verified",
                )

        return result

    async def handle_callback_data(
        self,
        callback_data: str,
        chat_id: int,
        default_field_id: str = "custom_response",
    ) -> dict[str, Any]:
        self._validate_chat(chat_id)
        command, thread_id = parse_hitl_callback_data(callback_data)

        if command == "approve":
            return await self.post_hitl_resume(
                thread_id=thread_id,
                action="APPROVE_OR_EDIT",
                field_overrides={},
                save_to_qa_cache=True,
            )

        if command == "skip":
            return await self.post_hitl_resume(
                thread_id=thread_id,
                action="SKIP",
                field_overrides=None,
                save_to_qa_cache=False,
            )

        self.pending_edits[chat_id] = {
            "thread_id": thread_id,
            "field_id": default_field_id,
        }
        return {
            "status": "AWAITING_EDIT_REPLY",
            "thread_id": thread_id,
            "field_id": default_field_id,
        }

    async def handle_text_reply(
        self,
        chat_id: int,
        text: str,
        thread_id: str | None = None,
        default_field_id: str = "custom_response",
    ) -> dict[str, Any]:
        self._validate_chat(chat_id)

        resolved_thread_id = thread_id
        resolved_field_id = default_field_id
        if resolved_thread_id is None:
            pending = self.pending_edits.pop(chat_id, None)
            if not pending:
                raise ValueError("No pending HITL edit thread found for this chat.")
            resolved_thread_id = pending["thread_id"]
            resolved_field_id = pending.get("field_id", default_field_id)

        overrides = parse_field_override_reply(text, default_field_id=resolved_field_id)
        return await self.post_hitl_resume(
            thread_id=resolved_thread_id,
            action="APPROVE_OR_EDIT",
            field_overrides=overrides,
            save_to_qa_cache=True,
        )

    def create_aiogram_router(self) -> Router:
        """Builds an aiogram Router handling /start|/id pairing, inline HITL callbacks, and edit text replies."""
        router = Router(name="autoapply_hitl_router")

        @router.message(F.text.regexp(r"^/(start|id)\b"))
        async def _on_start_or_id(message: Message) -> None:
            await message.reply(
                f"AutoApply-AI HITL Bot Connected.\n"
                f"Your numeric Chat ID: `{message.chat.id}`\n"
                f"Set `TELEGRAM_ALLOWED_CHAT_ID=\"{message.chat.id}\"` in your `.env` to lock escalations to this chat.",
                parse_mode="Markdown",
            )

        @router.callback_query(F.data.regexp(r"^(approve|skip|edit):.+"))
        async def _on_hitl_callback(callback: CallbackQuery) -> None:
            chat_id = callback.message.chat.id if callback.message else callback.from_user.id
            try:
                res = await self.handle_callback_data(callback.data or "", chat_id=chat_id)
            except PermissionError as exc:
                await callback.answer(str(exc), show_alert=True)
                return
            except Exception as exc:
                await callback.answer(f"HITL error: {exc}", show_alert=True)
                return

            if res.get("status") == "AWAITING_EDIT_REPLY":
                await callback.answer("Send your edited answer as a reply.")
                if callback.message:
                    await callback.message.reply(
                        f"✏️ Reply with your answer for thread `{res['thread_id']}` "
                        f"(use `field_id: answer` or plain text)."
                    )
            else:
                await callback.answer(f"Processed: {res.get('final_status', res.get('status', 'OK'))}")
                if callback.message:
                    await callback.message.reply(
                        f"✅ Thread `{callback.data}` -> `{res.get('final_status', res.get('status', 'DONE'))}`"
                    )

        @router.message(F.text)
        async def _on_hitl_text_reply(message: Message) -> None:
            if not message.text:
                return
            if message.chat.id not in self.pending_edits:
                return
            try:
                res = await self.handle_text_reply(chat_id=message.chat.id, text=message.text)
                await message.reply(
                    f"✅ Resumed and saved to qa_cache: `{res.get('final_status', res.get('status', 'APPLIED'))}`"
                )
            except Exception as exc:
                await message.reply(f"⚠️ Failed to resume HITL thread: {exc}")

        return router

    async def start_polling(self) -> None:
        if not self.bot_token:
            raise ValueError("TELEGRAM_BOT_TOKEN is required to start bot polling.")
        bot = Bot(token=self.bot_token)
        dispatcher = Dispatcher()
        dispatcher.include_router(self.create_aiogram_router())
        await dispatcher.start_polling(bot, handle_signals=False)
