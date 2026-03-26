import json
import logging
import os
from typing import Callable


def _normalize_int_key_mapping(source: dict) -> dict[int, object]:
    normalized: dict[int, object] = {}
    for key, value in source.items():
        try:
            normalized[int(key)] = value
        except (TypeError, ValueError):
            continue
    return normalized


def build_state_payload(
    conversations: dict,
    user_active_sessions: dict,
    user_selected_models: dict,
    user_selected_presets: dict,
    user_document_summary_modes: dict,
) -> dict[str, object]:
    serialized_conversations: dict[str, dict[str, list[str]]] = {}
    for user_id, per_session in conversations.items():
        if not isinstance(per_session, dict):
            continue
        session_payload = {
            session_name: history
            for session_name, history in per_session.items()
            if isinstance(session_name, str) and isinstance(history, list)
        }
        if session_payload:
            serialized_conversations[str(user_id)] = session_payload

    return {
        "version": 1,
        "conversations": serialized_conversations,
        "active_sessions": {
            str(user_id): session_name for user_id, session_name in user_active_sessions.items()
        },
        "selected_models": {
            str(user_id): model for user_id, model in user_selected_models.items()
        },
        "selected_presets": {
            str(user_id): preset for user_id, preset in user_selected_presets.items()
        },
        "selected_document_modes": {
            str(user_id): mode for user_id, mode in user_document_summary_modes.items()
        },
    }


def save_bot_state(
    state_file_path: str,
    local_data_dir: str,
    conversations: dict,
    user_active_sessions: dict,
    user_selected_models: dict,
    user_selected_presets: dict,
    user_document_summary_modes: dict,
    logger: logging.Logger,
) -> None:
    try:
        os.makedirs(local_data_dir, exist_ok=True)
        payload = build_state_payload(
            conversations,
            user_active_sessions,
            user_selected_models,
            user_selected_presets,
            user_document_summary_modes,
        )
        temp_path = f"{state_file_path}.tmp"
        with open(temp_path, "w", encoding="utf-8") as state_file:
            json.dump(payload, state_file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_path, state_file_path)
    except OSError as error:
        logger.warning("state_save_failed path=%s error=%s", state_file_path, error)


def load_bot_state(
    state_file_path: str,
    normalize_session_name: Callable[[str], str],
    default_session_name: str,
    max_history: int,
    logger: logging.Logger,
) -> dict[str, dict[int, object]]:
    loaded_conversations: dict[int, dict[str, list[str]]] = {}
    loaded_active_sessions: dict[int, str] = {}
    loaded_models: dict[int, str] = {}
    loaded_presets: dict[int, str] = {}
    loaded_document_modes: dict[int, str] = {}

    if not os.path.exists(state_file_path):
        logger.info("state_file_missing path=%s", state_file_path)
    else:
        try:
            with open(state_file_path, "r", encoding="utf-8") as state_file:
                payload = json.load(state_file)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("state_load_failed path=%s error=%s", state_file_path, error)
        else:
            if not isinstance(payload, dict):
                logger.warning("state_load_invalid_root path=%s", state_file_path)
            else:
                raw_conversations = payload.get("conversations", {})
                if isinstance(raw_conversations, dict):
                    normalized_conversations = _normalize_int_key_mapping(raw_conversations)
                    for user_id, raw_history in normalized_conversations.items():
                        per_session_histories: dict[str, list[str]] = {}
                        if isinstance(raw_history, list):
                            cleaned_history = [line for line in raw_history if isinstance(line, str)]
                            per_session_histories[default_session_name] = cleaned_history[-max_history:]
                        elif isinstance(raw_history, dict):
                            for raw_session_name, session_history in raw_history.items():
                                if not isinstance(raw_session_name, str) or not isinstance(
                                    session_history, list
                                ):
                                    continue
                                normalized_session_name = normalize_session_name(raw_session_name)
                                cleaned_history = [
                                    line for line in session_history if isinstance(line, str)
                                ]
                                per_session_histories[normalized_session_name] = cleaned_history[
                                    -max_history:
                                ]

                        if per_session_histories:
                            loaded_conversations[user_id] = per_session_histories

                raw_active_sessions = payload.get("active_sessions", {})
                if isinstance(raw_active_sessions, dict):
                    normalized_active_sessions = _normalize_int_key_mapping(raw_active_sessions)
                    for user_id, raw_session_name in normalized_active_sessions.items():
                        if not isinstance(raw_session_name, str):
                            continue
                        loaded_active_sessions[user_id] = normalize_session_name(raw_session_name)

                raw_models = payload.get("selected_models", {})
                if isinstance(raw_models, dict):
                    normalized_models = _normalize_int_key_mapping(raw_models)
                    for user_id, model in normalized_models.items():
                        if isinstance(model, str) and model.strip():
                            loaded_models[user_id] = model.strip()

                raw_presets = payload.get("selected_presets", {})
                if isinstance(raw_presets, dict):
                    normalized_presets = _normalize_int_key_mapping(raw_presets)
                    for user_id, preset in normalized_presets.items():
                        if not isinstance(preset, str):
                            continue
                        normalized_preset = preset.strip().lower()
                        if normalized_preset:
                            loaded_presets[user_id] = normalized_preset

                raw_document_modes = payload.get("selected_document_modes", {})
                if isinstance(raw_document_modes, dict):
                    normalized_document_modes = _normalize_int_key_mapping(raw_document_modes)
                    for user_id, mode in normalized_document_modes.items():
                        if isinstance(mode, str):
                            normalized_mode = mode.strip().lower()
                            if normalized_mode:
                                loaded_document_modes[user_id] = normalized_mode

    return {
        "conversations": loaded_conversations,
        "active_sessions": loaded_active_sessions,
        "selected_models": loaded_models,
        "selected_presets": loaded_presets,
        "selected_document_modes": loaded_document_modes,
    }
