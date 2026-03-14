import asyncio

import bot


def test_reset_command_clears_conversation_and_replies(make_update_context):
    user_id = 42
    bot.conversations[user_id] = ["User: hi", "AI: hello"]
    bot.user_reset_tokens[user_id] = 7

    update, context = make_update_context(user_id=user_id, text="/reset", client=None)

    asyncio.run(bot.reset(update, context))

    assert bot.conversations[user_id] == []
    assert bot.user_reset_tokens[user_id] == 8
    assert update.message.replies == ["대화 기록을 초기화했습니다."]


def test_help_command_replies_with_supported_commands(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "사용 가능한 명령어" in reply
    assert "/help" in reply
    assert "/reset" in reply
