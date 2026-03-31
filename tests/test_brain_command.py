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


def test_brain_command_shows_restart_detected_change_line(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "restart_detected",
                    "field": "service_states.ai-gateway",
                    "previous": "healthy",
                    "current": "healthy",
                    "notable": True,
                },
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 재시작 감지: ai-gateway" in reply


def test_brain_command_shows_service_state_change_line(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "partial",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker",
                    "previous": "degraded",
                    "current": "healthy",
                    "notable": True,
                }
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 상태 변경: worker degraded→healthy" in reply


def test_brain_command_shows_service_state_change_fallback_line_when_incomplete(
    make_update_context, monkeypatch
):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "partial",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {
                    "type": "service_state_change",
                    "service": "worker",
                    "from_state": "degraded",
                    "notable": True,
                }
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 상태 변경 감지" in reply


def test_brain_command_shows_docker_summary_change_line(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "docker_summary_change",
                    "field": "docker_summary",
                    "previous": {"running": 4, "stopped": 0},
                    "current": {"running": 5, "stopped": 1},
                    "notable": True,
                }
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 도커 요약 변화: 실행 4→5, 중지 0→1" in reply


def test_brain_command_keeps_docker_summary_legacy_restarting_fallback(
    make_update_context, monkeypatch
):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "docker_summary_change",
                    "field": "docker_summary",
                    "previous": {"running": 4, "restarting": 0},
                    "current": {"running": 5, "restarting": 1},
                    "notable": True,
                }
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 도커 요약 변화: 실행 4→5, 재시작 0→1" in reply


def test_brain_command_keeps_legacy_non_transition_docker_restarting_label(
    make_update_context, monkeypatch
):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "ok",
            "message_lines": ["ai-gateway 정상"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "docker_summary_change",
                    "running": 5,
                    "restarting": 1,
                    "notable": True,
                }
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 도커 요약 변화: 실행 5, 재시작 1" in reply


def test_brain_command_shows_metric_delta_change_line(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "warning",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "metric_delta",
                    "field": "memory_percent",
                    "previous": 66.2,
                    "current": 74.8,
                    "notable": True,
                }
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 지표 변화: 메모리 사용률 66.2%→74.8%" in reply


def test_brain_command_ignores_unknown_change_kinds_without_format_breakage(
    make_update_context, monkeypatch
):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "warning",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
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


def test_brain_command_compacts_multiple_service_state_changes(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "partial",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker-a",
                    "previous": "degraded",
                    "current": "healthy",
                    "notable": True,
                },
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker-b",
                    "previous": "healthy",
                    "current": "degraded",
                    "notable": True,
                },
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker-c",
                    "previous": "starting",
                    "current": "healthy",
                    "notable": True,
                },
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker-d",
                    "previous": "healthy",
                    "current": "degraded",
                    "notable": True,
                },
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert reply.count("- 상태 변경:") == 1
    assert "worker-a degraded→healthy" in reply
    assert "worker-b healthy→degraded" in reply
    assert "외 1건" in reply


def test_brain_command_deduplicates_metric_changes_for_compact_output(
    make_update_context, monkeypatch
):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "warning",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {"kind": "metric_delta", "field": "load_average", "previous": 0.7, "current": 1.5, "notable": True},
                {"kind": "metric_delta", "field": "load_average", "previous": 0.7, "current": 1.5, "notable": True},
                {"kind": "metric_delta", "field": "memory_percent", "previous": 61.0, "current": 72.0, "notable": True},
                {"kind": "metric_delta", "field": "memory_percent", "previous": 61.0, "current": 72.0, "notable": True},
                {"kind": "metric_delta", "field": "disk_percent", "previous": 68.4, "current": 79.6, "notable": True},
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert reply.count("- 지표 변화:") == 1
    assert "로드 평균 0.7→1.5, 메모리 사용률 61.0%→72.0% 외 1건" in reply


def test_brain_command_keeps_change_summary_short_for_normal_telegram_usage(
    make_update_context, monkeypatch
):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "warning",
            "message_lines": ["ai-gateway 정상", "worker 점검 필요", "디스크 사용률 74.3%"],
            "has_notable_changes": True,
            "changes": [
                {"kind": "restart_detected", "field": "service_states.ai-gateway", "notable": True},
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker-a",
                    "previous": "healthy",
                    "current": "degraded",
                    "notable": True,
                },
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker-b",
                    "previous": "degraded",
                    "current": "healthy",
                    "notable": True,
                },
                {
                    "kind": "docker_summary_change",
                    "field": "docker_summary",
                    "previous": {"running": 7, "stopped": 0},
                    "current": {"running": 6, "stopped": 1},
                    "notable": True,
                },
                {"kind": "metric_delta", "field": "load_average", "previous": 0.8, "current": 1.2, "notable": True},
                {"kind": "metric_delta", "field": "memory_percent", "previous": 63.0, "current": 70.4, "notable": True},
                {"kind": "metric_delta", "field": "load_average", "previous": 0.8, "current": 1.2, "notable": True},
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert len(reply) < 600
    assert reply.count("\n- 재시작 감지:") == 1
    assert reply.count("\n- 상태 변경:") == 1
    assert reply.count("\n- 도커 요약 변화:") == 1
    assert reply.count("\n- 지표 변화:") == 1


def test_brain_command_ignores_non_notable_service_state_change(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "partial",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker",
                    "previous": "degraded",
                    "current": "healthy",
                    "notable": False,
                }
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
        "⚠️ 일부 정보 누락"
    )


def test_brain_command_renders_only_notable_changes_when_mixed(make_update_context, monkeypatch):
    mocked_post_agent_brain = AsyncMock(
        return_value={
            "overall_status": "warning",
            "message_lines": ["일부 점검 필요"],
            "has_notable_changes": True,
            "changes": [
                {"kind": "restart_detected", "field": "service_states.ai-gateway", "notable": False},
                {
                    "kind": "service_state_change",
                    "field": "service_states.worker-a",
                    "previous": "healthy",
                    "current": "degraded",
                    "notable": True,
                },
                {
                    "kind": "docker_summary_change",
                    "field": "docker_summary",
                    "previous": {"running": 7, "stopped": 0},
                    "current": {"running": 6, "stopped": 1},
                    "notable": False,
                },
                {"kind": "metric_delta", "field": "memory_percent", "previous": 63.0, "current": 70.4, "notable": True},
            ],
        }
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)
    update, context = make_update_context(text="/brain", client=object())

    asyncio.run(bot.brain_command(update, context))

    reply = update.message.replies[-1]
    assert "[변화 감지]" in reply
    assert "- 상태 변경: worker-a healthy→degraded" in reply
    assert "- 지표 변화: 메모리 사용률 63.0%→70.4%" in reply
    assert "재시작 감지" not in reply
    assert "도커 요약 변화" not in reply
