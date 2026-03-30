import asyncio
from unittest.mock import AsyncMock

import bot


def test_brain_command_sends_formatted_briefing_on_success(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상", "디스크 사용률 71.2%"],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    client = object()
    update, context = make_update_context(text="/brain", client=client)

    asyncio.run(bot.brain_command(update, context))

    mocked_post_agent_brain.assert_awaited_once()
    assert mocked_post_agent_brain.await_args.args[0] is client
    assert mocked_post_agent_brain.await_args.kwargs["payload"] == {}
    assert mocked_post_agent_brain.await_args.kwargs["request_id"]

    reply = update.message.replies[-1]
    assert "📊 오늘 브리핑" in reply
    assert "[서버]" in reply
    assert "- ai-gateway 정상" in reply
    assert "- 디스크 사용률 71.2%" in reply
    assert "[상태]" in reply
    assert "✅ 안정" in reply


def test_brain_command_reports_timeout(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(side_effect=bot.GatewayClientError("agent_brain_timeout"))
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    mocked_post_agent_brain.assert_awaited_once()
    assert update.message.replies[-1] == "brain 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요."


def test_brain_command_reports_connection_failure(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(side_effect=bot.GatewayClientError("agent_brain_connect_error"))
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    mocked_post_agent_brain.assert_awaited_once()
    assert update.message.replies[-1] == "gateway에 연결하지 못했습니다."


def test_brain_command_reports_malformed_response_fallback(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        side_effect=bot.GatewayClientError("agent_brain_malformed_response")
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    mocked_post_agent_brain.assert_awaited_once()
    assert update.message.replies[-1] == "brain 응답 형식을 처리하지 못했습니다."


def test_brain_command_keeps_existing_format_with_legacy_fields_only(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상", "디스크 사용률 71.2%"],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    assert update.message.replies[-1] == (
        "📊 오늘 브리핑\n"
        "\n"
        "[서버]\n"
        "- ai-gateway 정상\n"
        "- 디스크 사용률 71.2%\n"
        "\n"
        "[상태]\n"
        "✅ 안정"
    )


def test_brain_command_accepts_empty_additive_change_fields(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상", "디스크 사용률 71.2%"],
            "has_notable_changes": False,
            "changes": [],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    assert update.message.replies[-1] == (
        "📊 오늘 브리핑\n"
        "\n"
        "[서버]\n"
        "- ai-gateway 정상\n"
        "- 디스크 사용률 71.2%\n"
        "\n"
        "[상태]\n"
        "✅ 안정"
    )


def test_brain_command_accepts_structured_and_unknown_change_items_without_format_breakage(
    make_update_context, monkeypatch
):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "warning",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {"type": "restart_detected", "service": "ai-gateway", "count": 1},
                {
                    "type": "service_state_change",
                    "service": "worker",
                    "from_state": "degraded",
                    "to_state": "healthy",
                },
                {
                    "type": "docker_summary_change",
                    "running": 5,
                    "restarting": 1,
                },
                {"type": "new_gateway_change_type", "details": {"foo": "bar"}},
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    assert update.message.replies[-1] == (
        "📊 오늘 브리핑\n"
        "\n"
        "[서버]\n"
        "- 일부 점검 필요\n"
        "\n"
        "[상태]\n"
        "🚨 점검 필요"
    )
