from __future__ import annotations

from typing import Mapping

from preset_catalog import get_preset_prompt_prefix


def build_prompt_with_preset(
    history_lines: list[str],
    active_preset: str,
    presets: Mapping[str, Mapping[str, str]],
) -> str:
    prompt = "\n".join(history_lines) + "\nAI:"
    preset_prefix = get_preset_prompt_prefix(presets, active_preset)
    if not preset_prefix:
        return prompt

    return f"{preset_prefix}{prompt}"
