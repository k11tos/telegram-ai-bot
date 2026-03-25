from __future__ import annotations

from typing import Mapping

PRESET_DESCRIPTION_FIELD = "description"
PRESET_PROMPT_PREFIX_FIELD = "prompt_prefix"


PresetCatalog = Mapping[str, Mapping[str, str]]


def normalize_preset_name(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    return value.strip().lower()


def get_preset_prompt_prefix(presets: PresetCatalog, preset_name: str) -> str:
    preset_definition = presets.get(preset_name, {})
    prompt_prefix = preset_definition.get(PRESET_PROMPT_PREFIX_FIELD, "")
    return prompt_prefix if isinstance(prompt_prefix, str) else ""


def get_preset_names(presets: PresetCatalog) -> tuple[str, ...]:
    return tuple(presets.keys())


def has_preset(presets: PresetCatalog, preset_name: str) -> bool:
    return preset_name in presets
