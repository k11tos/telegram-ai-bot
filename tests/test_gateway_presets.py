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

    loaded_from_gateway, used_fallback = asyncio.run(bot.load_gateway_presets(app))

    assert client.calls[0]["path"] == bot.AI_GATEWAY_PRESETS_PATH
    assert loaded_from_gateway is True
    assert used_fallback is False
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

    loaded_from_gateway, used_fallback = asyncio.run(bot.load_gateway_presets(app))

    assert loaded_from_gateway is False
    assert used_fallback is True
    assert app.bot_data[bot.PRESETS_KEY] == bot.get_static_presets()


def test_load_gateway_presets_falls_back_to_static_when_gateway_returns_empty_payload():
    client = FakePresetClient(payload={"presets": []})
    app = SimpleNamespace(bot_data={bot.HTTP_CLIENT_KEY: client})

    loaded_from_gateway, used_fallback = asyncio.run(bot.load_gateway_presets(app))

    assert loaded_from_gateway is False
    assert used_fallback is True
    assert app.bot_data[bot.PRESETS_KEY] == bot.get_static_presets()


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
