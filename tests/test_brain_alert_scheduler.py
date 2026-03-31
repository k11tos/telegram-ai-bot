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
        last_sent_windows=sent_windows,
        send_alert_for_user=send_alert,
        logger=bot.logger,
        schedule_hour_local=9,
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
        last_sent_windows=sent_windows,
        send_alert_for_user=send_alert,
        logger=bot.logger,
        schedule_hour_local=9,
        now_func=FixedNow(now),
    )

    asyncio.run(scheduler.run_once())
    asyncio.run(scheduler.run_once())

    send_alert.assert_awaited_once_with(50, "all")
    assert sent_windows[50] == "2026-03-31"


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
