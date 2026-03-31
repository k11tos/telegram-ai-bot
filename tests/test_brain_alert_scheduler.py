import asyncio
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock

import bot
from brain_alert_scheduler import BrainAlertScheduler


class FixedNow:
    def __init__(self, now: datetime):
        self.value = now

    def __call__(self) -> datetime:
        return self.value


def test_brain_alert_scheduler_skips_opted_out_users():
    send_alert = AsyncMock(return_value=True)
    user_modes = {10: "off", 11: "notable", 12: "all"}
    sent_windows = {}
    now = datetime(2026, 3, 31, 9, 5)

    scheduler = BrainAlertScheduler(
        user_brain_alert_modes=user_modes,
        user_brain_alert_times={},
        last_sent_windows=sent_windows,
        send_alert_for_user=send_alert,
        logger=bot.logger,
        default_time_local="09:00",
        now_func=FixedNow(now),
    )

    asyncio.run(scheduler.run_once())

    called_user_ids = [call.args[0] for call in send_alert.await_args_list]
    assert called_user_ids == [11, 12]
    assert 10 not in sent_windows


def test_brain_alert_scheduler_prevents_duplicate_send_within_same_window():
    send_alert = AsyncMock(return_value=True)
    user_modes = {50: "all"}
    sent_windows = {}
    now = datetime(2026, 3, 31, 9, 10)

    scheduler = BrainAlertScheduler(
        user_brain_alert_modes=user_modes,
        user_brain_alert_times={},
        last_sent_windows=sent_windows,
        send_alert_for_user=send_alert,
        logger=bot.logger,
        default_time_local="09:00",
        now_func=FixedNow(now),
    )

    asyncio.run(scheduler.run_once())
    asyncio.run(scheduler.run_once())

    send_alert.assert_awaited_once_with(50, "all")
    assert sent_windows[50] == "2026-03-31"


def test_brain_alert_scheduler_respects_user_configured_time():
    send_alert = AsyncMock(return_value=True)
    user_modes = {70: "all"}
    user_times = {70: "07:30"}
    sent_windows = {}

    scheduler_before = BrainAlertScheduler(
        user_brain_alert_modes=user_modes,
        user_brain_alert_times=user_times,
        last_sent_windows=sent_windows,
        send_alert_for_user=send_alert,
        logger=bot.logger,
        default_time_local="09:00",
        now_func=FixedNow(datetime(2026, 3, 31, 7, 29)),
    )
    asyncio.run(scheduler_before.run_once())
    send_alert.assert_not_awaited()

    scheduler_after = BrainAlertScheduler(
        user_brain_alert_modes=user_modes,
        user_brain_alert_times=user_times,
        last_sent_windows=sent_windows,
        send_alert_for_user=send_alert,
        logger=bot.logger,
        default_time_local="09:00",
        now_func=FixedNow(datetime(2026, 3, 31, 7, 30)),
    )
    asyncio.run(scheduler_after.run_once())
    send_alert.assert_awaited_once_with(70, "all")
    assert sent_windows[70] == "2026-03-31"


def test_should_send_brain_alert_respects_notable_and_all_modes():
    notable_payload = {"has_notable_changes": True}
    non_notable_payload = {"has_notable_changes": False}

    assert bot.should_send_brain_alert("off", notable_payload) is False
    assert bot.should_send_brain_alert("notable", notable_payload) is True
    assert bot.should_send_brain_alert("notable", non_notable_payload) is False
    assert bot.should_send_brain_alert("all", non_notable_payload) is True


def test_send_scheduled_brain_alert_notable_mode_skips_without_changes(monkeypatch):
    app = SimpleNamespace(
        bot_data={bot.HTTP_CLIENT_KEY: object()},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )
    mocked_post_agent_brain = AsyncMock(
        return_value={"overall_status": "ok", "message_lines": ["정상"], "has_notable_changes": False}
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)

    sent = asyncio.run(bot.send_scheduled_brain_alert(app, user_id=101, mode="notable"))

    assert sent is False
    app.bot.send_message.assert_not_awaited()


def test_send_scheduled_brain_alert_all_mode_sends_without_changes(monkeypatch):
    app = SimpleNamespace(
        bot_data={bot.HTTP_CLIENT_KEY: object()},
        bot=SimpleNamespace(send_message=AsyncMock()),
    )
    mocked_post_agent_brain = AsyncMock(
        return_value={"overall_status": "ok", "message_lines": ["정상"], "has_notable_changes": False}
    )
    monkeypatch.setattr(bot, "post_agent_brain", mocked_post_agent_brain)

    sent = asyncio.run(bot.send_scheduled_brain_alert(app, user_id=102, mode="all"))

    assert sent is True
    app.bot.send_message.assert_awaited()
    first_text = app.bot.send_message.await_args_list[0].kwargs["text"]
    assert "⏰ 자동 브레인 브리핑" in first_text


def test_brain_alert_scheduler_malformed_payload_does_not_crash_loop(monkeypatch):
    send_mock = AsyncMock()
    app = SimpleNamespace(
        bot_data={bot.HTTP_CLIENT_KEY: object()},
        bot=SimpleNamespace(send_message=send_mock),
    )
    monkeypatch.setattr(bot, "post_agent_brain", AsyncMock(return_value="malformed-payload"))
    user_modes = {201: "notable", 202: "all"}
    sent_windows = {}
    now = datetime(2026, 3, 31, 9, 30)

    scheduler = BrainAlertScheduler(
        user_brain_alert_modes=user_modes,
        user_brain_alert_times={},
        last_sent_windows=sent_windows,
        send_alert_for_user=lambda user_id, mode: bot.send_scheduled_brain_alert(
            app, user_id, mode
        ),
        logger=bot.logger,
        default_time_local="09:00",
        now_func=FixedNow(now),
    )

    asyncio.run(scheduler.run_once())

    assert sent_windows[202] == "2026-03-31"
    assert 201 not in sent_windows
    send_mock.assert_awaited()


def test_init_and_shutdown_manage_brain_alert_scheduler(monkeypatch):
    class FakeBot:
        def __init__(self):
            self.send_message = AsyncMock()

    app = SimpleNamespace(bot_data={}, bot=FakeBot())

    async def fake_load_gateway_presets(_app):
        return {"loaded_from_gateway": False, "used_fallback": True}

    async def fake_post_agent_brain(client, payload, request_id=None):
        return {"overall_status": "ok", "message_lines": ["ok"], "has_notable_changes": True}

    monkeypatch.setattr(bot, "load_gateway_presets", fake_load_gateway_presets)
    monkeypatch.setattr(bot, "post_agent_brain", fake_post_agent_brain)
    monkeypatch.setattr(bot, "BRAIN_ALERT_POLL_INTERVAL_SECONDS", 1.0)
    monkeypatch.setattr(bot, "BRAIN_ALERT_SCHEDULE_HOUR_LOCAL", 23)
    monkeypatch.setattr(bot, "AI_GATEWAY_BASE_URL", "http://gateway.local")

    async def _run():
        await bot.init_http_client(app)

        scheduler = app.bot_data[bot.BRAIN_ALERT_SCHEDULER_KEY]
        assert scheduler.task is not None
        assert scheduler.task.done() is False

        await bot.close_http_client(app)

        assert bot.BRAIN_ALERT_SCHEDULER_KEY not in app.bot_data
        assert bot.HTTP_CLIENT_KEY not in app.bot_data
        assert scheduler.task.done() is True

    asyncio.run(_run())
