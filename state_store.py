import json
import logging
import os
from typing import Callable

from document_summary import normalize_document_summary_mode


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
    user_brain_alert_modes: dict,
    user_brain_alert_times: dict,
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
        "document_summary_modes": {
            str(user_id): mode for user_id, mode in user_document_summary_modes.items()
        },
        "brain_alert_modes": {
            str(user_id): mode for user_id, mode in user_brain_alert_modes.items()
        },
        "brain_alert_times": {
            str(user_id): time_text for user_id, time_text in user_brain_alert_times.items()
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
    user_brain_alert_modes: dict,
    user_brain_alert_times: dict,
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
            user_brain_alert_modes,
            user_brain_alert_times,
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
    normalize_brain_alert_mode: Callable[[str | None], str],
    logger: logging.Logger,
) -> dict[str, dict[int, object]]:
    loaded_conversations: dict[int, dict[str, list[str]]] = {}
    loaded_active_sessions: dict[int, str] = {}
    loaded_models: dict[int, str] = {}
    loaded_presets: dict[int, str] = {}
    loaded_document_summary_modes: dict[int, str] = {}
    loaded_brain_alert_modes: dict[int, str] = {}
    loaded_brain_alert_times: dict[int, str] = {}

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

                raw_document_summary_modes = payload.get("document_summary_modes", {})
                if isinstance(raw_document_summary_modes, dict):
                    normalized_document_summary_modes = _normalize_int_key_mapping(
                        raw_document_summary_modes
                    )
                    for user_id, raw_mode in normalized_document_summary_modes.items():
                        if not isinstance(raw_mode, str):
                            continue
                        stripped_mode = raw_mode.strip()
                        if not stripped_mode:
                            continue
                        normalized_mode = normalize_document_summary_mode(stripped_mode)
                        if normalized_mode:
                            loaded_document_summary_modes[user_id] = normalized_mode

                raw_brain_alert_modes = payload.get("brain_alert_modes", {})
                if isinstance(raw_brain_alert_modes, dict):
                    normalized_brain_alert_modes = _normalize_int_key_mapping(raw_brain_alert_modes)
                    for user_id, raw_mode in normalized_brain_alert_modes.items():
                        if not isinstance(raw_mode, str):
                            continue
                        stripped_mode = raw_mode.strip()
                        if stripped_mode.lower() not in {"off", "notable", "all", "on"}:
                            continue
                        normalized_mode = normalize_brain_alert_mode(stripped_mode)
                        if normalized_mode in {"off", "notable", "all"}:
                            loaded_brain_alert_modes[user_id] = normalized_mode

                raw_brain_alert_times = payload.get("brain_alert_times", {})
                if isinstance(raw_brain_alert_times, dict):
                    normalized_brain_alert_times = _normalize_int_key_mapping(raw_brain_alert_times)
                    for user_id, raw_time in normalized_brain_alert_times.items():
                        if not isinstance(raw_time, str):
                            continue
                        if len(raw_time) != 5:
                            continue
                        hour_part, sep, minute_part = raw_time.partition(":")
                        if sep != ":":
                            continue
                        if len(hour_part) != 2 or len(minute_part) != 2:
                            continue
                        if not (hour_part.isdigit() and minute_part.isdigit()):
                            continue
                        hour = int(hour_part)
                        minute = int(minute_part)
                        if 0 <= hour <= 23 and 0 <= minute <= 59:
                            loaded_brain_alert_times[user_id] = f"{hour:02d}:{minute:02d}"

    return {
        "conversations": loaded_conversations,
        "active_sessions": loaded_active_sessions,
        "selected_models": loaded_models,
        "selected_presets": loaded_presets,
        "document_summary_modes": loaded_document_summary_modes,
        "brain_alert_modes": loaded_brain_alert_modes,
        "brain_alert_times": loaded_brain_alert_times,
    }
