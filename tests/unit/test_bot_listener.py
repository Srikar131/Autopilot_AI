from unittest.mock import AsyncMock, MagicMock

import pytest

from src.db.supabase_client import SupabaseRepository
from src.hitl.bot_listener import (
    TelegramHITLBotListener,
    parse_field_override_reply,
    parse_hitl_callback_data,
)


def _make_mock_http_client(final_status: str = "APPLIED"):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = {
        "status": "RESUMED_AND_PROCESSED",
        "job_id": "job-123",
        "final_status": final_status,
        "qa_cache_entries_added": 1,
    }
    client = AsyncMock()
    client.post.return_value = mock_resp
    return client


def test_parse_hitl_callback_and_reply_formats():
    assert parse_hitl_callback_data("approve:thread-42") == ("approve", "thread-42")
    assert parse_hitl_callback_data("skip:thread-99") == ("skip", "thread-99")
    assert parse_hitl_callback_data("edit:thread-77") == ("edit", "thread-77")

    with pytest.raises(ValueError):
        parse_hitl_callback_data("invalid_format")

    assert parse_field_override_reply("why_zepto: I build low-latency Voice AI systems.") == {
        "why_zepto": "I build low-latency Voice AI systems."
    }
    assert parse_field_override_reply("15 Days", default_field_id="notice_period") == {
        "notice_period": "15 Days"
    }


@pytest.mark.asyncio
async def test_bot_listener_approve_skip_and_edit_flow():
    mock_client = _make_mock_http_client()
    repo = SupabaseRepository(client=None)
    listener = TelegramHITLBotListener(
        bot_token="test-token",
        allowed_chat_id=12345,
        repo=repo,
        http_client=mock_client,
    )

    # 1. Approve callback posts to /api/v1/hitl/resume
    res_approve = await listener.handle_callback_data("approve:thread-1", chat_id=12345)
    assert res_approve["status"] == "RESUMED_AND_PROCESSED"
    mock_client.post.assert_called_with(
        "http://127.0.0.1:8420/api/v1/hitl/resume",
        json={
            "langgraph_thread_id": "thread-1",
            "action": "APPROVE_OR_EDIT",
            "field_overrides": {},
            "save_to_qa_cache": True,
        },
    )

    # 2. Skip callback posts SKIP action without caching
    await listener.handle_callback_data("skip:thread-2", chat_id=12345)
    mock_client.post.assert_called_with(
        "http://127.0.0.1:8420/api/v1/hitl/resume",
        json={
            "langgraph_thread_id": "thread-2",
            "action": "SKIP",
            "field_overrides": None,
            "save_to_qa_cache": False,
        },
    )

    # 3. Edit callback followed by text reply posts overrides and stores in qa_cache
    pending = await listener.handle_callback_data(
        "edit:thread-3", chat_id=12345, default_field_id="why_zepto"
    )
    assert pending["status"] == "AWAITING_EDIT_REPLY"

    res_edit = await listener.handle_text_reply(
        chat_id=12345,
        text="why_zepto: I love shipping fast RAG and Voice AI systems.",
    )
    assert res_edit["final_status"] == "APPLIED"
    mock_client.post.assert_called_with(
        "http://127.0.0.1:8420/api/v1/hitl/resume",
        json={
            "langgraph_thread_id": "thread-3",
            "action": "APPROVE_OR_EDIT",
            "field_overrides": {"why_zepto": "I love shipping fast RAG and Voice AI systems."},
            "save_to_qa_cache": True,
        },
    )
    assert len(repo._local_qa_cache) == 1
    assert repo._local_qa_cache[0]["canonical_answer"] == "I love shipping fast RAG and Voice AI systems."


@pytest.mark.asyncio
async def test_bot_listener_rejects_unauthorized_chat_and_builds_router():
    listener = TelegramHITLBotListener(
        bot_token="test-token",
        allowed_chat_id=999,
        http_client=_make_mock_http_client(),
    )
    with pytest.raises(PermissionError):
        await listener.handle_callback_data("approve:thread-1", chat_id=111)

    router = listener.create_aiogram_router()
    assert router.name == "autoapply_hitl_router"
