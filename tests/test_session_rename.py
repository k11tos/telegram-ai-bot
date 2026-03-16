import asyncio

import bot


def test_session_rename_existing_session(make_update_context):
    user_id = 301
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code", "AI: ok"]
    bot.user_active_sessions[user_id] = bot.DEFAULT_SESSION_NAME

    update, context = make_update_context(
        user_id=user_id,
        text="/session_rename coding python",
        client=None,
        args=["coding", "python"],
    )

    asyncio.run(bot.session_rename_command(update, context))

    renamed_sessions = bot.ensure_user_sessions(user_id)
    assert "coding" not in renamed_sessions
    assert renamed_sessions["python"] == ["User: code", "AI: ok"]
    assert update.message.replies[-1] == "세션 이름이 변경되었습니다: coding → python"


def test_session_rename_active_session_updates_active_pointer(make_update_context):
    user_id = 302
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code"]
    bot.user_active_sessions[user_id] = "coding"

    update, context = make_update_context(
        user_id=user_id,
        text="/session_rename coding python",
        client=None,
        args=["coding", "python"],
    )

    asyncio.run(bot.session_rename_command(update, context))

    assert bot.get_active_session_name(user_id) == "python"
    assert update.message.replies[-1] == "세션 이름이 변경되었습니다: coding → python"


def test_session_rename_missing_session(make_update_context):
    user_id = 303
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]

    update, context = make_update_context(
        user_id=user_id,
        text="/session_rename coding python",
        client=None,
        args=["coding", "python"],
    )

    asyncio.run(bot.session_rename_command(update, context))

    assert update.message.replies[-1] == "세션을 찾을 수 없어요: coding"


def test_session_rename_duplicate_target(make_update_context):
    user_id = 304
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code"]
    sessions["python"] = ["User: py"]

    update, context = make_update_context(
        user_id=user_id,
        text="/session_rename coding python",
        client=None,
        args=["coding", "python"],
    )

    asyncio.run(bot.session_rename_command(update, context))

    assert "coding" in bot.ensure_user_sessions(user_id)
    assert update.message.replies[-1] == "이미 존재하는 세션 이름이에요: python"


def test_session_rename_normalization_same_name_edge_case(make_update_context):
    user_id = 305
    sessions = bot.ensure_user_sessions(user_id)
    sessions[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    sessions["coding"] = ["User: code"]

    update, context = make_update_context(
        user_id=user_id,
        text="/session_rename coding ' coding '",
        client=None,
        args=["coding", " coding "],
    )

    asyncio.run(bot.session_rename_command(update, context))

    assert "coding" in bot.ensure_user_sessions(user_id)
    assert update.message.replies[-1] == "변경 전/후 세션 이름이 같아요. 다른 이름을 입력해주세요."
