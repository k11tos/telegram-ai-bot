import asyncio

import bot


def test_sessions_command_single_session(make_update_context):
    user_id = 101
    bot.ensure_user_sessions(user_id)[bot.DEFAULT_SESSION_NAME] = ["User: hi"]
    update, context = make_update_context(user_id=user_id, text="/sessions", client=None)

    asyncio.run(bot.sessions_command(update, context))

    assert update.message.replies[-1] == (
        "Active session: default\n\n"
        "Available sessions:\n"
        "- default"
    )


def test_sessions_command_multiple_sessions_sorted(make_update_context):
    user_id = 102
    bot.ensure_user_sessions(user_id)["trading"] = ["User: market"]
    bot.ensure_user_sessions(user_id)["coding"] = ["User: python"]
    bot.user_active_sessions[user_id] = "trading"
    update, context = make_update_context(user_id=user_id, text="/sessions", client=None)

    asyncio.run(bot.sessions_command(update, context))

    assert update.message.replies[-1] == (
        "Active session: trading\n\n"
        "Available sessions:\n"
        "- coding\n"
        "- trading"
    )


def test_sessions_command_empty_case(make_update_context):
    user_id = 103
    update, context = make_update_context(user_id=user_id, text="/sessions", client=None)

    asyncio.run(bot.sessions_command(update, context))

    assert update.message.replies[-1] == (
        "Active session: default\n\n"
        "Available sessions:\n"
        "- (none)"
    )
