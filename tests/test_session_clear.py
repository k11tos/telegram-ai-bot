import asyncio

import bot


def test_session_clear_existing_non_active_session(make_update_context):
    user_id = 301
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code", "AI: done"]
    bot.user_active_sessions[user_id] = bot.DEFAULT_SESSION_NAME
    bot.user_reset_tokens[user_id] = {bot.DEFAULT_SESSION_NAME: 2, "coding": 9}

    update, context = make_update_context(
        user_id=user_id,
        text="/session_clear coding",
        client=None,
        args=["coding"],
    )

    asyncio.run(bot.session_clear_command(update, context))

    assert bot.ensure_user_sessions(user_id)["coding"] == []
    assert bot.user_reset_tokens[user_id]["coding"] == 9
    assert update.message.replies[-1] == "세션 기록을 비웠습니다: coding"


def test_session_clear_active_session_increments_reset_token(make_update_context):
    user_id = 302
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    bot.user_active_sessions[user_id] = bot.DEFAULT_SESSION_NAME
    bot.user_reset_tokens[user_id] = {bot.DEFAULT_SESSION_NAME: 4}

    update, context = make_update_context(
        user_id=user_id,
        text="/session_clear default",
        client=None,
        args=[bot.DEFAULT_SESSION_NAME],
    )

    asyncio.run(bot.session_clear_command(update, context))

    assert bot.get_session_history(user_id, bot.DEFAULT_SESSION_NAME) == []
    assert bot.user_reset_tokens[user_id][bot.DEFAULT_SESSION_NAME] == 5
    assert update.message.replies[-1] == "세션 기록을 비웠습니다: default"


def test_session_clear_missing_session(make_update_context):
    user_id = 303
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    bot.user_active_sessions[user_id] = bot.DEFAULT_SESSION_NAME

    update, context = make_update_context(
        user_id=user_id,
        text="/session_clear coding",
        client=None,
        args=["coding"],
    )

    asyncio.run(bot.session_clear_command(update, context))

    assert update.message.replies[-1] == "세션을 찾을 수 없어요: coding"
