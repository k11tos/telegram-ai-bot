from types import SimpleNamespace

import pytest

import bot


class FakeWaitingMessage:
    def __init__(self):
        self.edits = []
        self.fail_on_edit = False

    async def edit_text(self, text):
        if self.fail_on_edit:
            raise RuntimeError("edit failed")
        self.edits.append(text)


class FakeMessage:
    def __init__(self, text):
        self.text = text
        self.replies = []
        self.waiting_message = FakeWaitingMessage()

    async def reply_text(self, text):
        self.replies.append(text)
        if text in ("생각 중…", "파일을 읽고 요약 중…"):
            return self.waiting_message
        return SimpleNamespace(text=text)


@pytest.fixture(autouse=True)
def clear_bot_state():
    bot.conversations.clear()
    bot.user_active_sessions.clear()
    bot.user_locks.clear()
    bot.user_reset_tokens.clear()
    bot.user_turn_counters.clear()
    bot.user_next_turn_to_finalize.clear()
    bot.user_finalize_conditions.clear()
    bot.user_in_flight_requests.clear()
    bot.user_selected_models.clear()
    bot.user_selected_presets.clear()
    bot.user_document_summary_modes.clear()
    bot.user_brain_alert_modes.clear()
    bot.user_brain_alert_sent_windows.clear()
    yield
    bot.conversations.clear()
    bot.user_active_sessions.clear()
    bot.user_locks.clear()
    bot.user_reset_tokens.clear()
    bot.user_turn_counters.clear()
    bot.user_next_turn_to_finalize.clear()
    bot.user_finalize_conditions.clear()
    bot.user_in_flight_requests.clear()
    bot.user_selected_models.clear()
    bot.user_selected_presets.clear()
    bot.user_document_summary_modes.clear()
    bot.user_brain_alert_modes.clear()
    bot.user_brain_alert_sent_windows.clear()


@pytest.fixture
def make_update_context():
    def _build(user_id=123, chat_id=456, text="hello", client=None, args=None):
        message = FakeMessage(text=text)
        update = SimpleNamespace(
            effective_user=SimpleNamespace(id=user_id),
            effective_chat=SimpleNamespace(id=chat_id),
            message=message,
        )
        context = SimpleNamespace(
            application=SimpleNamespace(bot_data={bot.HTTP_CLIENT_KEY: client}),
            bot=SimpleNamespace(),
            args=args if args is not None else [],
        )
        return update, context

    return _build
