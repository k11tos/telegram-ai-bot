from __future__ import annotations

from typing import Mapping


def build_prompt_with_preset(
    history_lines: list[str],
    active_preset: str,
    presets: Mapping[str, Mapping[str, str]],
) -> str:
    prompt = "\n".join(history_lines) + "\nAI:"
    preset_definition = presets.get(active_preset, {})
    preset_prefix = preset_definition.get("prompt_prefix", "")
    if not preset_prefix:
        return prompt

    return f"{preset_prefix}{prompt}"
