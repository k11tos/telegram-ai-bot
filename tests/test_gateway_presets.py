import asyncio
from types import SimpleNamespace

import httpx

import bot


class FakeResponse:
    def __init__(self, payload=None, status_error=None):
        self._payload = payload
        self._status_error = status_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        return self._payload


class FakePresetClient:
    def __init__(self, payload=None, get_error=None, status_error=None):
        self.payload = payload
        self.get_error = get_error
        self.status_error = status_error
        self.calls = []

    async def get(self, path, headers=None):
        self.calls.append({"path": path, "headers": headers})
        if self.get_error is not None:
            raise self.get_error
        return FakeResponse(payload=self.payload, status_error=self.status_error)


class FakeMessage:
    def __init__(self):
        self.replies = []

    async def reply_text(self, text):
        self.replies.append(text)


def test_load_gateway_presets_uses_gateway_data_when_available():
    client = FakePresetClient(
        payload={
            "presets": [
                {
                    "name": " Research ",
                    "description": "Research mode",
                    "prompt_prefix": "  Preset: research.\n",
                }
            ]
        }
    )
    app = SimpleNamespace(bot_data={bot.HTTP_CLIENT_KEY: client})

    result = asyncio.run(bot.load_gateway_presets(app))

    assert result == (True, False)
    assert client.calls[0]["path"] == bot.AI_GATEWAY_PRESETS_PATH
    # prompt_prefix should be preserved exactly as provided by gateway
    assert app.bot_data[bot.PRESETS_KEY] == {
        "research": {
            "description": "Research mode",
            "prompt_prefix": "  Preset: research.\n",
        }
    }


def test_load_gateway_presets_falls_back_to_static_when_gateway_fails():
    request = httpx.Request("GET", "http://test/presets")
    client = FakePresetClient(get_error=httpx.RequestError("down", request=request))
    app = SimpleNamespace(bot_data={bot.HTTP_CLIENT_KEY: client})

    result = asyncio.run(bot.load_gateway_presets(app))

    assert result == (False, True)
    assert app.bot_data[bot.PRESETS_KEY] == bot.get_static_presets()


def test_reload_presets_command_successful_gateway_refreshes_bot_data():
    client = FakePresetClient(
        payload={
            "presets": [
                {"name": "normal", "description": "n", "prompt_prefix": ""},
                {"name": "coder", "description": "c", "prompt_prefix": ""},
                {"name": "english", "description": "e", "prompt_prefix": ""},
                {"name": "quant", "description": "q", "prompt_prefix": ""},
            ]
        }
    )
    message = FakeMessage()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(application=SimpleNamespace(bot_data={bot.HTTP_CLIENT_KEY: client}))

    asyncio.run(bot.reload_presets_command(update, context))

    assert context.application.bot_data[bot.PRESETS_KEY].keys() == {
        "normal",
        "coder",
        "english",
        "quant",
    }
    assert message.replies[-1] == "프리셋을 다시 불러왔습니다: normal, coder, english, quant"


def test_reload_presets_command_failure_falls_back_safely():
    request = httpx.Request("GET", "http://test/presets")
    client = FakePresetClient(get_error=httpx.RequestError("down", request=request))
    message = FakeMessage()
    update = SimpleNamespace(message=message)
    context = SimpleNamespace(
        application=SimpleNamespace(
            bot_data={
                bot.HTTP_CLIENT_KEY: client,
                bot.PRESETS_KEY: {"legacy": {"description": "old", "prompt_prefix": "old"}},
            }
        )
    )

    asyncio.run(bot.reload_presets_command(update, context))

    assert context.application.bot_data[bot.PRESETS_KEY] == bot.get_static_presets()
    assert message.replies[-1] == "게이트웨이 프리셋을 불러오지 못해 기본 프리셋으로 유지합니다."


def test_preset_command_uses_refreshed_values_after_reload(make_update_context):
    client = FakePresetClient(
        payload={
            "presets": [
                {"name": "normal", "description": "n", "prompt_prefix": ""},
                {"name": "writer", "description": "w", "prompt_prefix": "Preset: writer\n"},
            ]
        }
    )
    message = FakeMessage()
    reload_update = SimpleNamespace(message=message)
    app = SimpleNamespace(bot_data={bot.HTTP_CLIENT_KEY: client})
    reload_context = SimpleNamespace(application=app)

    asyncio.run(bot.reload_presets_command(reload_update, reload_context))

    update, context = make_update_context(
        user_id=777,
        text="/preset writer",
        client=client,
        args=["writer"],
    )
    context.application = app

    asyncio.run(bot.preset_command(update, context))

    assert bot.user_selected_presets[777] == "writer"
    assert update.message.replies[-1] == "프리셋이 변경되었습니다: writer"


def test_build_prompt_with_gateway_prefix_preserves_exact_formatting():
    presets = {
        "research": {
            "description": "Research mode",
            "prompt_prefix": "  Prefix with space\n",
        }
    }

    prompt = bot.build_prompt_with_preset(["User: hi"], "research", presets)

    assert prompt == "  Prefix with space\nUser: hi\nAI:"


def test_build_prompt_with_gateway_prefix_does_not_insert_extra_blank_line():
    presets = {
        "research": {
            "description": "Research mode",
            "prompt_prefix": "Preset: research.",
        }
    }

    prompt = bot.build_prompt_with_preset(["User: hi"], "research", presets)

    assert prompt == "Preset: research.User: hi\nAI:"
