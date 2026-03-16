import asyncio

import bot


def test_session_delete_existing(make_update_context):
    user_id = 201
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code"]
    bot.user_active_sessions[user_id] = bot.DEFAULT_SESSION_NAME

    update, context = make_update_context(
        user_id=user_id,
        text="/session_delete coding",
        client=None,
        args=["coding"],
    )

    asyncio.run(bot.session_delete_command(update, context))

    assert "coding" not in bot.ensure_user_sessions(user_id)
    assert update.message.replies[-1] == "세션이 삭제되었습니다: coding"


def test_session_delete_active_error(make_update_context):
    user_id = 202
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code"]
    bot.user_active_sessions[user_id] = "coding"

    update, context = make_update_context(
        user_id=user_id,
        text="/session_delete coding",
        client=None,
        args=["coding"],
    )

    asyncio.run(bot.session_delete_command(update, context))

    assert "coding" in bot.ensure_user_sessions(user_id)
    assert update.message.replies[-1] == "현재 사용 중인 세션은 삭제할 수 없어요."


def test_session_delete_default_error(make_update_context):
    user_id = 203
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code"]
    bot.user_active_sessions[user_id] = "coding"

    update, context = make_update_context(
        user_id=user_id,
        text="/session_delete default",
        client=None,
        args=[bot.DEFAULT_SESSION_NAME],
    )

    asyncio.run(bot.session_delete_command(update, context))

    assert bot.DEFAULT_SESSION_NAME in bot.ensure_user_sessions(user_id)
    assert update.message.replies[-1] == "기본 세션은 삭제할 수 없어요."


def test_session_delete_missing_error(make_update_context):
    user_id = 204
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    bot.user_active_sessions[user_id] = bot.DEFAULT_SESSION_NAME

    update, context = make_update_context(
        user_id=user_id,
        text="/session_delete coding",
        client=None,
        args=["coding"],
    )

    asyncio.run(bot.session_delete_command(update, context))

    assert update.message.replies[-1] == "세션을 찾을 수 없어요: coding"
