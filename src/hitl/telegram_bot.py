from __future__ import annotations

import os
from typing import Any

from src.agent.state import FormFieldSpec
from src.config import settings


def format_hitl_escalation_caption(
    company_name: str,
    role_title: str,
    portal: str,
    low_confidence_fields: list[FormFieldSpec],
    pdf_filename: str,
) -> str:
    """Formats the Telegram HITL notification card matching UX-001 specification."""
    lowest = min((f["confidence"] for f in low_confidence_fields), default=0.70)
    lines = [
        f"🚨 **HITL Verification Needed (Confidence: {lowest:.2f})**",
        "",
        f"🏢 **Company:** {company_name} (`{portal}`)",
        f"💼 **Role:** {role_title}",
        f"📄 **Resume Attached:** `{pdf_filename}`",
        "",
    ]
    for idx, f in enumerate(low_confidence_fields, 1):
        lines.append(f"❓ **Question {idx}:** {f['label_text']}")
        lines.append(f"🤖 **AI Drafted Answer:** `{f.get('proposed_value') or '<Empty>'}`")
        lines.append("")

    lines.append("💡 *Tap Approve to submit immediately, or reply with custom text to overwrite and train qa_cache:*")
    return "\n".join(lines)


def build_hitl_inline_keyboard_payload(thread_id: str, vnc_url: str = "http://100.64.0.1:6080/vnc.html") -> dict[str, Any]:
    """Constructs the Telegram InlineKeyboardMarkup JSON structure for 1-tap resolution."""
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Approve & Submit", "callback_data": f"approve:{thread_id}"},
                {"text": "✏️ Edit via Reply", "callback_data": f"edit:{thread_id}"},
            ],
            [
                {"text": "🖥️ Open Live Browser (VNC)", "url": vnc_url},
                {"text": "⏭️ Skip This Job", "callback_data": f"skip:{thread_id}"},
            ],
        ]
    }


class TelegramHITLNotifier:
    """Sends annotated screenshots and interactive inline buttons to the user's Telegram chat."""

    def __init__(self, bot_token: str | None = None, allowed_chat_id: int | None = None) -> None:
        self.bot_token = bot_token if bot_token is not None else settings.telegram_bot_token
        self.allowed_chat_id = allowed_chat_id if allowed_chat_id is not None else settings.telegram_allowed_chat_id

    async def send_escalation_card(
        self,
        thread_id: str,
        company_name: str,
        role_title: str,
        portal: str,
        low_confidence_fields: list[FormFieldSpec],
        pdf_filename: str,
        screenshot_path: str | None = None,
    ) -> dict[str, Any]:
        caption = format_hitl_escalation_caption(
            company_name=company_name,
            role_title=role_title,
            portal=portal,
            low_confidence_fields=low_confidence_fields,
            pdf_filename=pdf_filename,
        )
        reply_markup = build_hitl_inline_keyboard_payload(thread_id=thread_id)

        if "PYTEST_CURRENT_TEST" in os.environ or not self.bot_token or not self.allowed_chat_id:
            # Return dry-run payload when offline/testing
            return {
                "dispatched": False,
                "mode": "dry_run",
                "thread_id": thread_id,
                "caption": caption,
                "reply_markup": reply_markup,
                "screenshot_path": screenshot_path,
            }

        import httpx
        api_base = f"https://api.telegram.org/bot{self.bot_token}"
        async with httpx.AsyncClient(timeout=15.0) as client:
            if screenshot_path:
                with open(screenshot_path, "rb") as photo_file:
                    resp = await client.post(
                        f"{api_base}/sendPhoto",
                        data={
                            "chat_id": str(self.allowed_chat_id),
                            "caption": caption,
                            "parse_mode": "Markdown",
                            "reply_markup": __import__("json").dumps(reply_markup),
                        },
                        files={"photo": photo_file},
                    )
            else:
                resp = await client.post(
                    f"{api_base}/sendMessage",
                    json={
                        "chat_id": self.allowed_chat_id,
                        "text": caption,
                        "parse_mode": "Markdown",
                        "reply_markup": reply_markup,
                    },
                )
            return {"dispatched": True, "status_code": resp.status_code, "response": resp.json()}
