import asyncio
from types import SimpleNamespace

import httpx
import pytest

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


EXPECTED_FALLBACK_PRESET_NAMES = {"normal", "coder", "english", "quant"}
GATEWAY_PRESET_FIELDS = {"description", "prompt_prefix"}


@pytest.mark.parametrize(
    "preset_name",
    sorted(EXPECTED_FALLBACK_PRESET_NAMES),
)
def test_static_fallback_presets_include_expected_gateway_names(preset_name):
    fallback_presets = bot.get_static_presets()

    assert preset_name in fallback_presets


@pytest.mark.parametrize(
    ("preset_name", "preset_definition"),
    sorted(bot.get_static_presets().items()),
)
def test_static_fallback_presets_match_gateway_field_shape(
    preset_name, preset_definition
):
    assert set(preset_definition) == GATEWAY_PRESET_FIELDS
    assert preset_definition["description"] == bot.STATIC_PRESET_DEFINITIONS[preset_name][
        "description"
    ]
    assert preset_definition["prompt_prefix"] == bot.STATIC_PRESET_DEFINITIONS[preset_name][
        "prompt_prefix"
    ]


def test_default_preset_is_present_in_static_fallback_presets():
    fallback_presets = bot.get_static_presets()

    assert bot.DEFAULT_PRESET in fallback_presets
    assert fallback_presets[bot.DEFAULT_PRESET] == {
        "description": bot.STATIC_PRESET_DEFINITIONS[bot.DEFAULT_PRESET]["description"],
        "prompt_prefix": bot.STATIC_PRESET_DEFINITIONS[bot.DEFAULT_PRESET]["prompt_prefix"],
    }


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

    assert result == {"loaded_from_gateway": True, "used_fallback": False}
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

    assert result == {"loaded_from_gateway": False, "used_fallback": True}
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
