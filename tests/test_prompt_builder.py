import prompt_builder


def test_build_prompt_with_preset_includes_history_and_ai_marker():
    presets = {"normal": {"prompt_prefix": ""}}

    prompt = prompt_builder.build_prompt_with_preset(
        ["User: hi", "AI: hello", "User: next"],
        "normal",
        presets,
    )

    assert prompt == "User: hi\nAI: hello\nUser: next\nAI:"


def test_build_prompt_with_preset_adds_prefix_when_present():
    presets = {"coder": {"prompt_prefix": "You are coding helper.\n\n"}}

    prompt = prompt_builder.build_prompt_with_preset(["User: hi"], "coder", presets)

    assert prompt == "You are coding helper.\n\nUser: hi\nAI:"


def test_build_prompt_with_preset_falls_back_when_preset_missing():
    prompt = prompt_builder.build_prompt_with_preset(["User: hi"], "unknown", {})

    assert prompt == "User: hi\nAI:"
