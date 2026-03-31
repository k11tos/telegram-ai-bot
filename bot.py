import ast
import asyncio
import json
from dataclasses import dataclass, field
import logging
import os
import re
import time
import uuid

import httpx
from brain_alert_scheduler import (
    BrainAlertScheduler,
    DEFAULT_BRAIN_ALERT_POLL_INTERVAL_SECONDS,
    DEFAULT_BRAIN_ALERT_SCHEDULE_HOUR_LOCAL,
    is_valid_brain_alert_time_text,
)
from brain_formatter import render_brain_payload
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

from commands.ops import (
    OperationalCommandDependencies,
    brain_command,
    configure_operational_dependencies,
    health_command,
    help_command,
    models_command,
    register_operational_handlers,
    reload_presets_command,
    status_command,
    version_command,
)
from commands.sessions import SessionCommandDependencies, build_session_handlers
from document_summary import (
    DEFAULT_DOCUMENT_SUMMARY_MODE,
    DocumentValidationError,
    SUPPORTED_DOCUMENT_SUMMARY_MODES,
    build_document_summary_prompt as build_document_summary_prompt_helper,
    is_supported_document as is_supported_document_helper,
    normalize_document_summary_mode as normalize_document_summary_mode_helper,
    summarize_document_text,
)
from gateway_client import (
    AI_GATEWAY_AGENT_BRAIN_PATH,
    GatewayClientError,
    extract_model_names,
    post_agent_brain,
)
from preset_catalog import (
    PRESET_DESCRIPTION_FIELD,
    PRESET_PROMPT_PREFIX_FIELD,
    get_preset_names,
    has_preset,
    normalize_preset_name,
)
from prompt_builder import build_prompt_with_preset as build_prompt_with_preset_helper
from session_state import ensure_user_sessions as ensure_user_sessions_with_state
from session_state import get_active_session_name as get_active_session_name_with_state
from session_state import get_session_history as get_session_history_with_state
from session_state import normalize_session_name as normalize_session_name_with_state
from state_store import build_state_payload as build_persisted_state_payload
from state_store import load_bot_state as load_bot_state_from_file
from state_store import save_bot_state as persist_bot_state

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_GATEWAY_BASE_URL = os.getenv("AI_GATEWAY_BASE_URL")
AI_GATEWAY_CHAT_PATH = "/chat"
AI_GATEWAY_STREAM_PATH = "/generate_stream"
AI_GATEWAY_MODELS_PATH = "/models"
AI_GATEWAY_PRESETS_PATH = "/presets"
AI_GATEWAY_READY_PATH = "/health/ready"
MAX_KEEPALIVE_CONNECTIONS = int(os.getenv("MAX_KEEPALIVE_CONNECTIONS", "20"))
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "100"))

DEFAULT_CONNECT_TIMEOUT = 5.0
DEFAULT_READ_TIMEOUT = 300.0
DEFAULT_WRITE_TIMEOUT = 30.0
DEFAULT_POOL_TIMEOUT = 5.0


def resolve_http_timeout_config() -> dict[str, float]:
    legacy_timeout_value = os.getenv("TIMEOUT")
    legacy_timeout = float(legacy_timeout_value) if legacy_timeout_value else None

    return {
        "connect": float(
            os.getenv(
                "HTTP_CONNECT_TIMEOUT",
                (
                    legacy_timeout
                    if legacy_timeout is not None
                    else DEFAULT_CONNECT_TIMEOUT
                ),
            )
        ),
        "read": float(
            os.getenv(
                "HTTP_READ_TIMEOUT",
                legacy_timeout if legacy_timeout is not None else DEFAULT_READ_TIMEOUT,
            )
        ),
        "write": float(
            os.getenv(
                "HTTP_WRITE_TIMEOUT",
                legacy_timeout if legacy_timeout is not None else DEFAULT_WRITE_TIMEOUT,
            )
        ),
        "pool": float(
            os.getenv(
                "HTTP_POOL_TIMEOUT",
                legacy_timeout if legacy_timeout is not None else DEFAULT_POOL_TIMEOUT,
            )
        ),
    }


HTTP_TIMEOUT_CONFIG = resolve_http_timeout_config()

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

@dataclass
class RuntimeState:
    conversations: dict[int, dict[str, list[str]]] = field(default_factory=dict)
    user_active_sessions: dict[int, str] = field(default_factory=dict)
    user_locks: dict[int, asyncio.Lock] = field(default_factory=dict)
    user_reset_tokens: dict[int, dict[str, int]] = field(default_factory=dict)
    user_turn_counters: dict[int, int] = field(default_factory=dict)
    user_next_turn_to_finalize: dict[int, int] = field(default_factory=dict)
    user_finalize_conditions: dict[int, asyncio.Condition] = field(default_factory=dict)
    user_in_flight_requests: dict[int, bool] = field(default_factory=dict)
    user_selected_models: dict[int, str] = field(default_factory=dict)
    user_selected_presets: dict[int, str] = field(default_factory=dict)
    user_document_summary_modes: dict[int, str] = field(default_factory=dict)
    user_brain_alert_modes: dict[int, str] = field(default_factory=dict)
    user_brain_alert_times: dict[int, str] = field(default_factory=dict)
    user_brain_alert_sent_windows: dict[int, str] = field(default_factory=dict)


runtime_state = RuntimeState()

# Backward-compatible aliases for existing tests/importers.
conversations = runtime_state.conversations
user_active_sessions = runtime_state.user_active_sessions
user_locks = runtime_state.user_locks
user_reset_tokens = runtime_state.user_reset_tokens
user_turn_counters = runtime_state.user_turn_counters
user_next_turn_to_finalize = runtime_state.user_next_turn_to_finalize
user_finalize_conditions = runtime_state.user_finalize_conditions
user_in_flight_requests = runtime_state.user_in_flight_requests
user_selected_models = runtime_state.user_selected_models
user_selected_presets = runtime_state.user_selected_presets
user_document_summary_modes = runtime_state.user_document_summary_modes
user_brain_alert_modes = runtime_state.user_brain_alert_modes
user_brain_alert_times = runtime_state.user_brain_alert_times
user_brain_alert_sent_windows = runtime_state.user_brain_alert_sent_windows
MODEL_RESET_ALIASES = {"default", "reset"}

LOCAL_DATA_DIR = os.getenv("LOCAL_DATA_DIR", "data")
STATE_FILE_NAME = "bot_state.json"
STATE_FILE_PATH = os.path.join(LOCAL_DATA_DIR, STATE_FILE_NAME)

DEFAULT_PRESET = "normal"
STATIC_PRESET_DEFINITIONS = {
    "normal": {
        "description": "Balanced assistant for general use.",
        "prompt_prefix": "",
    },
    "coder": {
        "description": "Focused on programming and debugging tasks.",
        "prompt_prefix": "You are a practical coding assistant. Be precise and production-minded.\n\n",
    },
    "english": {
        "description": "Helps improve English writing and grammar.",
        "prompt_prefix": "You are an English writing helper. Improve clarity, grammar, and tone.\n\n",
    },
    "quant": {
        "description": "Supports quantitative and analytical reasoning.",
        "prompt_prefix": "You are a quantitative reasoning assistant. Show concise, correct math.\n\n",
    },
}
PRESETS_KEY = "presets"

if DEFAULT_PRESET not in STATIC_PRESET_DEFINITIONS:
    raise AssertionError("DEFAULT_PRESET must exist in STATIC_PRESET_DEFINITIONS")

MAX_HISTORY = 10
DEFAULT_SESSION_NAME = "default"
HTTP_CLIENT_KEY = "http_client"
TELEGRAM_MESSAGE_MAX_LEN = 4096
STREAM_EDIT_INTERVAL_SEC = 1.0
SUPPORTED_DOCUMENT_EXTENSIONS = (
    ".txt",
    ".md",
    ".log",
    ".py",
    ".json",
    ".yaml",
    ".yml",
    ".csv",
)
SUPPORTED_DOCUMENT_EXTENSIONS_TEXT = ", ".join(SUPPORTED_DOCUMENT_EXTENSIONS)
MAX_DOCUMENT_BYTES = int(os.getenv("MAX_DOCUMENT_BYTES", "200000"))
MAX_DOCUMENT_PROMPT_CHARS = int(os.getenv("MAX_DOCUMENT_PROMPT_CHARS", "20000"))
BRAIN_ALERT_POLL_INTERVAL_SECONDS = float(
    os.getenv(
        "BRAIN_ALERT_POLL_INTERVAL_SECONDS",
        str(DEFAULT_BRAIN_ALERT_POLL_INTERVAL_SECONDS),
    )
)
BRAIN_ALERT_SCHEDULE_HOUR_LOCAL = int(
    os.getenv(
        "BRAIN_ALERT_SCHEDULE_HOUR_LOCAL",
        str(DEFAULT_BRAIN_ALERT_SCHEDULE_HOUR_LOCAL),
    )
)
BRAIN_ALERT_SCHEDULER_KEY = "brain_alert_scheduler"
DEFAULT_BRAIN_ALERT_TIME_LOCAL = f"{BRAIN_ALERT_SCHEDULE_HOUR_LOCAL:02d}:00"
BRAIN_ALERT_TIMEZONE_LABEL = os.getenv("BRAIN_ALERT_TIMEZONE_LABEL", "server local time")
DOCUMENT_SUMMARY_MODES_TEXT = ", ".join(SUPPORTED_DOCUMENT_SUMMARY_MODES)
DEFAULT_BRAIN_ALERT_MODE = "off"
SUPPORTED_BRAIN_ALERT_MODES = ("off", "notable", "all")
BRAIN_ALERT_ON_ALIAS_MODE = "notable"
BRAIN_ALERT_MODES_TEXT = ", ".join(["on", *SUPPORTED_BRAIN_ALERT_MODES])
HELP_LINES = [
    "사용 가능한 명령어",
    "/help - 명령어 안내",
    "/ctx - 현재 사용자 컨텍스트 요약",
    "/model - 현재 적용 중인 모델 확인",
    "/preset [name] - 현재 프리셋 확인, 목록 보기 또는 변경",
    "/reload_presets - 게이트웨이 프리셋 다시 불러오기",
    "/models - 사용 가능한 모델 목록",
    "/health - AI 게이트웨이 준비 상태 확인",
    "/brain - 시스템 브리핑 요약",
    "/brainalert [on|off|notable|all|time HH:MM] - 브리핑 알림 모드/시간 확인 또는 변경",
    "/session [name] - 현재 세션 확인 또는 변경",
    "/session_rename <old> <new> - 세션 이름 변경",
    "/session_clear <name> - 세션 기록만 비우기",
    "/session_delete <name> - 세션 삭제",
    "/sessions - 보유한 세션 목록 확인",
    "/docmode [summary|bullets|action|code] - 문서 요약 모드 확인 또는 변경",
    "/reset - 대화 기록 초기화",
    "/status - 봇 상태 확인",
    "/version - 실행 버전 정보 확인",
]
HELP_MESSAGE = "\n".join(HELP_LINES)

VERSION_ENV_KEYS = ("APP_VERSION", "VERSION")
COMMIT_ENV_KEYS = ("GIT_COMMIT_SHA", "COMMIT_SHA", "GITHUB_SHA")


def build_state_payload() -> dict[str, object]:
    return build_persisted_state_payload(
        runtime_state.conversations,
        runtime_state.user_active_sessions,
        runtime_state.user_selected_models,
        runtime_state.user_selected_presets,
        runtime_state.user_document_summary_modes,
        runtime_state.user_brain_alert_modes,
        runtime_state.user_brain_alert_times,
    )


def save_bot_state() -> None:
    persist_bot_state(
        STATE_FILE_PATH,
        LOCAL_DATA_DIR,
        runtime_state.conversations,
        runtime_state.user_active_sessions,
        runtime_state.user_selected_models,
        runtime_state.user_selected_presets,
        runtime_state.user_document_summary_modes,
        runtime_state.user_brain_alert_modes,
        runtime_state.user_brain_alert_times,
        logger,
    )


def request_state_save(reason: str = "unspecified") -> None:
    # Centralized save-intent entrypoint for future save throttling.
    _ = reason
    save_bot_state()


def load_bot_state() -> None:
    loaded_state = load_bot_state_from_file(
        STATE_FILE_PATH,
        normalize_session_name,
        DEFAULT_SESSION_NAME,
        MAX_HISTORY,
        normalize_brain_alert_mode,
        logger,
    )

    runtime_state.conversations.clear()
    runtime_state.conversations.update(loaded_state["conversations"])
    runtime_state.user_active_sessions.clear()
    runtime_state.user_active_sessions.update(loaded_state["active_sessions"])
    runtime_state.user_selected_models.clear()
    runtime_state.user_selected_models.update(loaded_state["selected_models"])
    runtime_state.user_selected_presets.clear()
    runtime_state.user_selected_presets.update(loaded_state["selected_presets"])
    runtime_state.user_document_summary_modes.clear()
    runtime_state.user_document_summary_modes.update(loaded_state["document_summary_modes"])
    runtime_state.user_brain_alert_modes.clear()
    runtime_state.user_brain_alert_modes.update(loaded_state["brain_alert_modes"])
    runtime_state.user_brain_alert_times.clear()
    runtime_state.user_brain_alert_times.update(loaded_state["brain_alert_times"])
    runtime_state.user_brain_alert_sent_windows.clear()


def get_static_presets() -> dict[str, dict[str, str]]:
    return {
        name: {
            PRESET_DESCRIPTION_FIELD: preset[PRESET_DESCRIPTION_FIELD],
            PRESET_PROMPT_PREFIX_FIELD: preset[PRESET_PROMPT_PREFIX_FIELD],
        }
        for name, preset in STATIC_PRESET_DEFINITIONS.items()
    }


def normalize_gateway_presets(payload) -> dict[str, dict[str, str]]:
    if isinstance(payload, dict) and isinstance(payload.get("presets"), list):
        source = payload["presets"]
    elif isinstance(payload, list):
        source = payload
    else:
        source = []

    normalized: dict[str, dict[str, str]] = {}
    for item in source:
        if not isinstance(item, dict):
            continue

        raw_name = item.get("name")
        if not isinstance(raw_name, str):
            continue

        name = normalize_preset_name(raw_name)
        if not name:
            continue

        description = item.get("description")
        prompt_prefix = item.get("prompt_prefix")
        normalized[name] = {
            PRESET_DESCRIPTION_FIELD: description.strip() if isinstance(description, str) else "",
            PRESET_PROMPT_PREFIX_FIELD: prompt_prefix if isinstance(prompt_prefix, str) else "",
        }

    return normalized


def get_presets_from_bot_data(
    bot_data: dict | None = None,
) -> dict[str, dict[str, str]]:
    if isinstance(bot_data, dict):
        presets = bot_data.get(PRESETS_KEY)
        if isinstance(presets, dict) and presets:
            return presets
    return get_static_presets()


def get_supported_preset_names(bot_data: dict | None = None) -> tuple[str, ...]:
    return get_preset_names(get_presets_from_bot_data(bot_data))


async def load_gateway_presets(app) -> dict[str, bool]:
    fallback_presets = get_static_presets()
    client = app.bot_data.get(HTTP_CLIENT_KEY)
    if client is None:
        app.bot_data[PRESETS_KEY] = fallback_presets
        return {"loaded_from_gateway": False, "used_fallback": True}

    request_id = uuid.uuid4().hex[:12]
    try:
        response = await client.get(
            AI_GATEWAY_PRESETS_PATH,
            headers={"X-Request-Id": request_id},
        )
        response.raise_for_status()
        gateway_presets = normalize_gateway_presets(response.json())
        if gateway_presets:
            app.bot_data[PRESETS_KEY] = gateway_presets
            return {"loaded_from_gateway": True, "used_fallback": False}

        app.bot_data[PRESETS_KEY] = fallback_presets
        return {"loaded_from_gateway": False, "used_fallback": True}
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
        logger.warning("preset_load_failed request_id=%s error=%s", request_id, error)
        app.bot_data[PRESETS_KEY] = fallback_presets
        return {"loaded_from_gateway": False, "used_fallback": True}


def normalize_session_name(raw_name: str) -> str:
    return normalize_session_name_with_state(raw_name, DEFAULT_SESSION_NAME)


def get_active_session_name(user_id: int) -> str:
    return get_active_session_name_with_state(
        user_id,
        runtime_state.user_active_sessions,
        DEFAULT_SESSION_NAME,
    )


def ensure_user_sessions(user_id: int) -> dict[str, list[str]]:
    return ensure_user_sessions_with_state(
        user_id,
        runtime_state.conversations,
        DEFAULT_SESSION_NAME,
        MAX_HISTORY,
    )


def get_session_history(user_id: int, session_name: str | None = None) -> list[str]:
    return get_session_history_with_state(
        user_id,
        runtime_state.conversations,
        runtime_state.user_active_sessions,
        DEFAULT_SESSION_NAME,
        MAX_HISTORY,
        session_name,
    )


def get_session_reset_token(user_id: int, session_name: str) -> int:
    per_session_tokens = runtime_state.user_reset_tokens.get(user_id)
    if not isinstance(per_session_tokens, dict):
        per_session_tokens = {}
        runtime_state.user_reset_tokens[user_id] = per_session_tokens

    normalized_session_name = normalize_session_name(session_name)
    token = per_session_tokens.get(normalized_session_name)
    if not isinstance(token, int):
        token = 0
        per_session_tokens[normalized_session_name] = token

    return token


def increment_session_reset_token(user_id: int, session_name: str) -> int:
    current_token = get_session_reset_token(user_id, session_name)
    next_token = current_token + 1
    runtime_state.user_reset_tokens[user_id][normalize_session_name(session_name)] = next_token
    return next_token


def sanitize_version_value(value: str, max_length: int = 64) -> str:
    normalized = value.strip()
    if not normalized:
        return ""

    safe_value = re.sub(r"[^A-Za-z0-9._-]", "", normalized)
    if not safe_value:
        return ""

    return safe_value[:max_length]


def first_sanitized_env(keys: tuple[str, ...], max_length: int = 64) -> str | None:
    for key in keys:
        raw_value = os.getenv(key)
        if not raw_value:
            continue

        sanitized = sanitize_version_value(raw_value, max_length=max_length)
        if sanitized:
            return sanitized

    return None


def build_version_message() -> str:
    app_version = first_sanitized_env(VERSION_ENV_KEYS)
    commit_sha = first_sanitized_env(COMMIT_ENV_KEYS, max_length=40)

    version_parts = []
    if app_version:
        version_parts.append(f"app={app_version}")
    if commit_sha:
        version_parts.append(f"commit={commit_sha[:7]}")

    if not version_parts:
        return "version: version info unavailable"

    return "version: " + " ".join(version_parts)


def build_status_message(context: ContextTypes.DEFAULT_TYPE) -> str:
    client = context.application.bot_data.get(HTTP_CLIENT_KEY)
    client_status = "초기화됨" if client is not None else "미초기화"

    lines = [
        "봇 상태 요약",
        "- 서비스 상태: 실행 중",
        f"- AI 게이트웨이: {AI_GATEWAY_BASE_URL or '미설정'}",
        (
            "- HTTP 타임아웃(초): "
            f"connect={HTTP_TIMEOUT_CONFIG['connect']}, "
            f"read={HTTP_TIMEOUT_CONFIG['read']}, "
            f"write={HTTP_TIMEOUT_CONFIG['write']}, "
            f"pool={HTTP_TIMEOUT_CONFIG['pool']}"
        ),
        f"- HTTP 클라이언트: {client_status}",
        "- 기본 동작: 사용자별 모델 미선택 시 게이트웨이 기본 모델을 사용",
    ]

    if client is None:
        lines.append("- 안내: AI 호출용 클라이언트가 아직 준비되지 않았습니다.")

    return "\n".join(lines)


def fit_telegram_text(text: str) -> str:
    if len(text) <= TELEGRAM_MESSAGE_MAX_LEN:
        return text
    return text[: TELEGRAM_MESSAGE_MAX_LEN - 1] + "…"


def split_telegram_text(text: str, limit: int = 4000) -> list[str]:
    if limit <= 0:
        raise ValueError("limit must be positive")

    if len(text) <= limit:
        return [text]

    chunks: list[str] = []
    remaining = text
    delimiters = ("\n\n", "\n", " ")

    while len(remaining) > limit:
        current_window = remaining[:limit]
        split_at = -1

        for delimiter in delimiters:
            delimiter_idx = current_window.rfind(delimiter)
            if delimiter_idx != -1:
                split_at = delimiter_idx + len(delimiter)
                break

        if split_at <= 0:
            split_at = limit

        chunks.append(remaining[:split_at])
        remaining = remaining[split_at:]

    chunks.append(remaining)
    return chunks


def extract_stream_delta(raw_line: str) -> tuple[str, bool]:
    line = raw_line.strip()
    if not line:
        return "", False

    payload = line[len("data:") :].strip() if line.startswith("data:") else line
    if payload == "[DONE]":
        return "", True

    candidate_payloads = [payload]

    # Some gateways can serialize streamed chunks into Python bytes literals
    # like b'{"response":"..."}' or concatenated forms.
    bytes_literal_chunks = re.findall(r"b'(?:\\.|[^'])*'|b\"(?:\\.|[^\"])*\"", payload)
    for chunk in bytes_literal_chunks:
        try:
            decoded = ast.literal_eval(chunk)
            if isinstance(decoded, bytes):
                candidate_payloads.append(decoded.decode("utf-8", errors="ignore"))
        except (SyntaxError, ValueError):
            if chunk.startswith("b'") and chunk.endswith("'"):
                raw_inner = chunk[2:-1]
            elif chunk.startswith('b"') and chunk.endswith('"'):
                raw_inner = chunk[2:-1]
            else:
                continue

            # Fallback for non-standard bytes literal-like strings that contain
            # non-ASCII characters directly.
            normalized_inner = (
                raw_inner.replace("\\\\", "\\")
                .replace('\\"', '"')
                .replace("\\'", "'")
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\r", "\r")
            )
            candidate_payloads.append(normalized_inner)

    decoder = json.JSONDecoder()
    collected_delta: list[str] = []
    is_done = False

    for candidate in candidate_payloads:
        text = candidate.strip()
        if not text:
            continue
        if text == "[DONE]":
            is_done = True
            continue

        # Support both single JSON payload and concatenated JSON payloads.
        index = 0
        while index < len(text):
            while index < len(text) and text[index].isspace():
                index += 1
            if index >= len(text):
                break

            try:
                obj, next_index = decoder.raw_decode(text, index)
            except json.JSONDecodeError:
                break

            index = next_index
            if not isinstance(obj, dict):
                continue

            if obj.get("done") is True:
                is_done = True

            for key in ("delta", "content", "token", "text", "response"):
                value = obj.get(key)
                if isinstance(value, str) and value:
                    collected_delta.append(value)
                    break

    return "".join(collected_delta), is_done


def get_user_lock(user_id):
    if user_id not in runtime_state.user_locks:
        runtime_state.user_locks[user_id] = asyncio.Lock()
    return runtime_state.user_locks[user_id]


def get_user_finalize_condition(user_id):
    lock = get_user_lock(user_id)
    if user_id not in runtime_state.user_finalize_conditions:
        runtime_state.user_finalize_conditions[user_id] = asyncio.Condition(lock)
    return runtime_state.user_finalize_conditions[user_id]


session_handlers = build_session_handlers(
    SessionCommandDependencies(
        default_session_name=DEFAULT_SESSION_NAME,
        user_active_sessions=runtime_state.user_active_sessions,
        get_user_lock=get_user_lock,
        save_bot_state=request_state_save,
        increment_session_reset_token=increment_session_reset_token,
        normalize_session_name=normalize_session_name,
        get_active_session_name=get_active_session_name,
        ensure_user_sessions=ensure_user_sessions,
        get_session_history=get_session_history,
    )
)
session_command = session_handlers.session_command
sessions_command = session_handlers.sessions_command
session_rename_command = session_handlers.session_rename_command
session_delete_command = session_handlers.session_delete_command
session_clear_command = session_handlers.session_clear_command


def get_user_selected_model(user_id: int) -> str | None:
    selected_model = runtime_state.user_selected_models.get(user_id)
    if not isinstance(selected_model, str):
        return None

    normalized_model = selected_model.strip()
    return normalized_model or None


def get_user_selected_preset(
    user_id: int, presets: dict[str, dict[str, str]] | None = None
) -> str | None:
    selected_preset = runtime_state.user_selected_presets.get(user_id)
    if not isinstance(selected_preset, str):
        return None

    normalized_preset = normalize_preset_name(selected_preset)
    available_presets = presets if presets is not None else get_static_presets()
    if normalized_preset not in available_presets:
        return None

    return normalized_preset


def resolve_active_preset(
    user_id: int, presets: dict[str, dict[str, str]] | None = None
) -> str:
    available_presets = presets if presets is not None else get_static_presets()
    if DEFAULT_PRESET not in available_presets and available_presets:
        return next(iter(available_presets.keys()))
    return get_user_selected_preset(user_id, available_presets) or DEFAULT_PRESET


def build_prompt_with_preset(
    history_lines: list[str],
    active_preset: str,
    presets: dict[str, dict[str, str]] | None = None,
) -> str:
    available_presets = presets if presets is not None else get_static_presets()
    return build_prompt_with_preset_helper(history_lines, active_preset, available_presets)


def build_gateway_payload(
    prompt: str,
    selected_model: str | None = None,
    selected_preset: str | None = None,
) -> dict[str, str]:
    payload = {"prompt": prompt}
    if selected_model:
        payload["model"] = selected_model
    if selected_preset:
        payload["preset"] = selected_preset
    return payload


def is_supported_document(file_name: str | None) -> bool:
    return is_supported_document_helper(file_name, SUPPORTED_DOCUMENT_EXTENSIONS)


def build_supported_document_filter():
    extension_filters = [
        filters.Document.FileExtension(extension.lstrip("."))
        for extension in SUPPORTED_DOCUMENT_EXTENSIONS
    ]
    merged_filter = extension_filters[0]
    for extension_filter in extension_filters[1:]:
        merged_filter = merged_filter | extension_filter
    return merged_filter


def build_document_summary_prompt(file_name: str, content: str, mode: str | None = None) -> str:
    return build_document_summary_prompt_helper(file_name, content, mode=mode)


def normalize_document_summary_mode(mode: str | None) -> str:
    return normalize_document_summary_mode_helper(mode)


def get_user_document_summary_mode(user_id: int) -> str:
    selected_mode = runtime_state.user_document_summary_modes.get(user_id)
    return normalize_document_summary_mode(selected_mode)


def normalize_brain_alert_mode(mode: str | None) -> str:
    if not isinstance(mode, str):
        return DEFAULT_BRAIN_ALERT_MODE

    normalized_mode = mode.strip().lower()
    if normalized_mode == "on":
        return BRAIN_ALERT_ON_ALIAS_MODE
    if normalized_mode in SUPPORTED_BRAIN_ALERT_MODES:
        return normalized_mode
    return DEFAULT_BRAIN_ALERT_MODE


def get_user_brain_alert_mode(user_id: int) -> str:
    selected_mode = runtime_state.user_brain_alert_modes.get(user_id)
    return normalize_brain_alert_mode(selected_mode)


def get_user_brain_alert_time(user_id: int) -> str:
    selected_time = runtime_state.user_brain_alert_times.get(user_id)
    if not isinstance(selected_time, str):
        return DEFAULT_BRAIN_ALERT_TIME_LOCAL
    normalized_time = selected_time.strip()
    if is_valid_brain_alert_time_text(normalized_time):
        return normalized_time
    return DEFAULT_BRAIN_ALERT_TIME_LOCAL


def should_send_brain_alert(mode: str, brain_payload: dict | None) -> bool:
    normalized_mode = normalize_brain_alert_mode(mode)
    if normalized_mode == "off":
        return False
    if normalized_mode == "all":
        return True
    if normalized_mode == "notable":
        if not isinstance(brain_payload, dict):
            return False
        return brain_payload.get("has_notable_changes") is True
    return False


def build_scheduled_brain_alert_message(brain_payload: dict | None) -> str:
    automatic_prefix = "⏰ 자동 브레인 브리핑"
    return f"{automatic_prefix}\n\n{render_brain_payload(brain_payload)}"


async def brainalert_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    requested_mode = " ".join(context.args).strip().lower() if context.args else ""

    if not requested_mode:
        current_mode = get_user_brain_alert_mode(user_id)
        current_time = get_user_brain_alert_time(user_id)
        await update.message.reply_text(
            f"현재 브리핑 알림 모드: {current_mode}\n"
            f"현재 브리핑 알림 시간: {current_time} ({BRAIN_ALERT_TIMEZONE_LABEL})"
        )
        return

    if context.args and context.args[0].lower() == "time":
        if len(context.args) != 2:
            await update.message.reply_text("시간 설정 형식: /brainalert time HH:MM")
            return

        requested_time = context.args[1].strip()
        if not is_valid_brain_alert_time_text(requested_time):
            await update.message.reply_text(
                "지원하지 않는 시간 형식입니다. HH:MM(24시간제)로 입력해 주세요."
            )
            return

        lock = get_user_lock(user_id)
        async with lock:
            runtime_state.user_brain_alert_times[user_id] = requested_time
            runtime_state.user_brain_alert_sent_windows.pop(user_id, None)
            request_state_save("brainalert_time_change")
        await update.message.reply_text(
            f"브리핑 알림 시간이 변경되었습니다: {requested_time} ({BRAIN_ALERT_TIMEZONE_LABEL})"
        )
        return

    if requested_mode not in {"on", *SUPPORTED_BRAIN_ALERT_MODES}:
        await update.message.reply_text(
            "지원하지 않는 브리핑 알림 모드입니다. "
            f"사용 가능: {BRAIN_ALERT_MODES_TEXT}"
        )
        return
    normalized_requested_mode = normalize_brain_alert_mode(requested_mode)

    lock = get_user_lock(user_id)
    async with lock:
        runtime_state.user_brain_alert_modes[user_id] = normalized_requested_mode
        request_state_save("brainalert_change")
    await update.message.reply_text(
        f"브리핑 알림 모드가 변경되었습니다: {normalized_requested_mode}"
    )


async def docmode_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    requested_mode = " ".join(context.args).strip().lower() if context.args else ""

    if not requested_mode:
        current_mode = get_user_document_summary_mode(user_id)
        await update.message.reply_text(
            f"현재 문서 요약 모드: {current_mode}\n"
            f"사용 가능: {DOCUMENT_SUMMARY_MODES_TEXT}"
        )
        return

    if requested_mode not in SUPPORTED_DOCUMENT_SUMMARY_MODES:
        await update.message.reply_text(
            "지원하지 않는 문서 요약 모드입니다. "
            f"사용 가능: {DOCUMENT_SUMMARY_MODES_TEXT}"
        )
        return

    lock = get_user_lock(user_id)
    async with lock:
        runtime_state.user_document_summary_modes[user_id] = requested_mode
        request_state_save("docmode_change")
    await update.message.reply_text(f"문서 요약 모드가 변경되었습니다: {requested_mode}")


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lock = get_user_lock(user_id)
    async with lock:
        active_session = get_active_session_name(user_id)
        get_session_history(user_id, active_session).clear()
        increment_session_reset_token(user_id, active_session)
        request_state_save("reset")
    await update.message.reply_text("대화 기록을 초기화했습니다.")


async def model_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    selected_model = get_user_selected_model(user_id)

    requested_model = " ".join(context.args).strip() if context.args else ""

    if not requested_model:
        if selected_model:
            await update.message.reply_text(f"현재 모델: {selected_model}")
            return

        await update.message.reply_text("현재 모델: 기본 모델 사용")
        return

    if requested_model.lower() in MODEL_RESET_ALIASES:
        lock = get_user_lock(user_id)
        async with lock:
            runtime_state.user_selected_models.pop(user_id, None)
            request_state_save("model_reset")
        await update.message.reply_text(
            "모델 설정을 초기화했습니다. 기본 모델을 사용합니다."
        )
        return

    client = context.application.bot_data.get(HTTP_CLIENT_KEY)
    if client is None:
        await update.message.reply_text("지금은 모델을 변경할 수 없어요.")
        return

    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()
    logger.info(
        f"model_validate_start request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} requested_model={requested_model}"
    )

    try:
        response = await client.get(
            AI_GATEWAY_MODELS_PATH,
            headers={"X-Request-Id": request_id},
        )
        response.raise_for_status()
        available_models = extract_model_names(response.json())
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.warning(
            f"model_validate_failed request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} error={error}"
        )
        await update.message.reply_text(
            "모델 확인에 실패했어요. 잠시 후 다시 시도해주세요."
        )
        return

    if requested_model not in available_models:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.info(
            f"model_validate_not_found request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} requested_model={requested_model}"
        )
        await update.message.reply_text("사용할 수 없는 모델이에요.")
        return

    lock = get_user_lock(user_id)
    async with lock:
        runtime_state.user_selected_models[user_id] = requested_model
        request_state_save("model_change")

    latency_ms = int((time.monotonic() - request_start_ts) * 1000)
    logger.info(
        f"model_validate_success request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} latency_ms={latency_ms} selected_model={requested_model}"
    )
    await update.message.reply_text(f"모델이 변경되었습니다: {requested_model}")


async def preset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    requested_preset = normalize_preset_name(" ".join(context.args)) if context.args else ""

    presets = get_presets_from_bot_data(context.application.bot_data)

    if requested_preset:
        if not has_preset(presets, requested_preset):
            supported_presets_text = ", ".join(presets.keys())
            await update.message.reply_text(
                f"지원하지 않는 프리셋입니다. 사용 가능: {supported_presets_text}"
            )
            return

        lock = get_user_lock(user_id)
        async with lock:
            runtime_state.user_selected_presets[user_id] = requested_preset
            request_state_save("preset_change")
        await update.message.reply_text(f"프리셋이 변경되었습니다: {requested_preset}")
        return

    active_preset = resolve_active_preset(user_id, presets)
    await update.message.reply_text(build_preset_overview_message(active_preset, presets))


def build_preset_overview_message(
    active_preset: str,
    presets: dict[str, dict[str, str]],
) -> str:
    preset_names = ", ".join(presets.keys())
    lines = [
        f"현재 프리셋: {active_preset}",
        f"사용 가능: {preset_names}",
        "설명:",
    ]
    for preset_name, preset_definition in presets.items():
        marker = "✅" if preset_name == active_preset else "•"
        description = preset_definition.get(PRESET_DESCRIPTION_FIELD, "").strip()
        description_text = description if description else "설명 없음"
        lines.append(f"{marker} {preset_name}: {description_text}")
    return "\n".join(lines)


def build_ctx_message(user_id: int, presets: dict[str, dict[str, str]] | None = None) -> str:
    available_presets = (
        presets if presets is not None else get_presets_from_bot_data(None)
    )
    active_session = get_active_session_name(user_id)
    selected_model = get_user_selected_model(user_id) or "기본 모델 사용"
    active_preset = resolve_active_preset(user_id, available_presets)
    history_line_count = len(get_session_history(user_id, active_session))
    in_flight = runtime_state.user_in_flight_requests.get(user_id, False)
    in_flight_text = "있음" if in_flight else "없음"

    return "\n".join(
        [
            "현재 컨텍스트",
            f"- 세션: {active_session}",
            f"- 모델: {selected_model}",
            f"- 프리셋: {active_preset}",
            f"- 기록 줄 수: {history_line_count}",
            f"- 요청 처리 중: {in_flight_text}",
        ]
    )


async def ctx_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    presets = get_presets_from_bot_data(context.application.bot_data)
    await update.message.reply_text(build_ctx_message(user_id, presets))


async def _begin_message_turn_with_inflight_guard(
    user_id: int,
    user_text: str,
    presets: dict[str, dict[str, str]],
    lock: asyncio.Lock,
    request_id: str,
    chat_id: int | None,
    update: Update,
) -> dict[str, object] | None:
    # This reserves the user's single in-flight slot for the whole turn.
    # We still keep the lock narrow to shared-state bookkeeping, but later
    # messages from the same user are rejected until this turn finalizes.
    async with lock:
        active_session = get_active_session_name(user_id)
        history = get_session_history(user_id, active_session)
        if user_id not in runtime_state.user_turn_counters:
            runtime_state.user_turn_counters[user_id] = 0
        if user_id not in runtime_state.user_next_turn_to_finalize:
            runtime_state.user_next_turn_to_finalize[user_id] = 1
        if user_id not in runtime_state.user_in_flight_requests:
            runtime_state.user_in_flight_requests[user_id] = False

        if runtime_state.user_in_flight_requests[user_id]:
            logger.info(
                f"request_rejected_inflight request_id={request_id} "
                f"user_id={user_id} chat_id={chat_id}"
            )
            await update.message.reply_text(
                "이전 요청을 처리 중입니다. 잠시 후 다시 보내주세요."
            )
            return None

        runtime_state.user_in_flight_requests[user_id] = True

        old_history = history[:]
        reset_token = get_session_reset_token(user_id, active_session)
        runtime_state.user_turn_counters[user_id] += 1
        turn_id = runtime_state.user_turn_counters[user_id]
        new_history = old_history + [f"User: {user_text}"]
        new_history = new_history[-MAX_HISTORY:]
        selected_model = get_user_selected_model(user_id)
        active_preset = resolve_active_preset(user_id, presets)

    try:
        prompt = "\n".join(new_history) + "\nAI:"
        payload = build_gateway_payload(
            prompt,
            selected_model=selected_model,
            selected_preset=active_preset,
        )
    except Exception:
        async with lock:
            runtime_state.user_in_flight_requests[user_id] = False
        finalize_condition = get_user_finalize_condition(user_id)
        async with finalize_condition:
            if runtime_state.user_next_turn_to_finalize.get(user_id, 1) == turn_id:
                runtime_state.user_next_turn_to_finalize[user_id] = turn_id + 1
                finalize_condition.notify_all()
        raise

    return {
        "active_session": active_session,
        "reset_token": reset_token,
        "turn_id": turn_id,
        "payload": payload,
    }


async def _call_gateway_with_stream_fallback(
    client: httpx.AsyncClient,
    payload: dict[str, str],
    request_id: str,
    user_id: int,
    chat_id: int | None,
    waiting_msg,
    request_start_ts: float,
) -> str:
    gateway_headers = {"X-Request-Id": request_id}
    stream_result = ""
    stream_completed_normally = False
    stream_failed = False
    last_rendered_text = ""
    last_edit_ts = 0.0

    try:
        async with client.stream(
            "POST",
            AI_GATEWAY_STREAM_PATH,
            json=payload,
            headers=gateway_headers,
        ) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                delta, done = extract_stream_delta(line)
                if done:
                    stream_completed_normally = True
                    break

                if not delta:
                    continue

                if stream_result and delta.startswith(stream_result):
                    stream_result = delta
                else:
                    stream_result += delta
                now = time.monotonic()

                if now - last_edit_ts < STREAM_EDIT_INTERVAL_SEC:
                    continue

                draft_text = fit_telegram_text(f"초안 작성 중…\n\n{stream_result}")
                if draft_text != last_rendered_text:
                    try:
                        await waiting_msg.edit_text(draft_text)
                        last_rendered_text = draft_text
                        last_edit_ts = now
                    except Exception as stream_edit_error:
                        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
                        logger.warning(
                            f"telegram_stream_edit_failed request_id={request_id} "
                            f"user_id={user_id} chat_id={chat_id} latency_ms={latency_ms} "
                            f"error={stream_edit_error}"
                        )
    except (httpx.HTTPStatusError, httpx.RequestError) as stream_error:
        stream_failed = True
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        stream_error_type = type(stream_error).__name__
        logger.warning(
            f"streaming_fallback request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} "
            f"error_type={stream_error_type} error={stream_error}"
        )

    result = stream_result.strip()
    should_fallback = stream_failed or not result or not stream_completed_normally
    if should_fallback:
        if result and not stream_completed_normally:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.warning(
                f"streaming_fallback_partial_discard request_id={request_id} "
                f"user_id={user_id} chat_id={chat_id} latency_ms={latency_ms}"
            )
        fallback_resp = await client.post(
            AI_GATEWAY_CHAT_PATH, json=payload, headers=gateway_headers
        )
        fallback_resp.raise_for_status()
        result = fallback_resp.json()["response"]

    return result


async def _deliver_telegram_response(
    update: Update,
    waiting_msg,
    result: str,
    request_id: str,
    user_id: int,
    chat_id: int | None,
    request_start_ts: float,
) -> bool:
    response_delivered = True
    try:
        await _send_chunked_message_via_waiting_message(update, waiting_msg, result)
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.info(
            f"response_delivered request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms}"
        )
    except Exception as edit_error:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.error(
            f"telegram_message_edit_failed request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} error={edit_error}"
        )
        try:
            await _send_chunked_message_as_replies(update, result)
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.info(
                f"response_delivered request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms}"
            )
        except Exception as reply_error:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"telegram_fallback_reply_failed request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={reply_error}"
            )
            error_summary = str(reply_error).strip() or type(reply_error).__name__
            if len(error_summary) > 120:
                error_summary = error_summary[:117] + "..."
            try:
                await update.message.reply_text(
                    f"AI 응답 전송 중 오류가 발생했습니다. ({error_summary})"
                )
            except Exception as notify_error:
                latency_ms = int((time.monotonic() - request_start_ts) * 1000)
                logger.error(
                    f"telegram_error_notice_send_failed request_id={request_id} "
                    f"user_id={user_id} chat_id={chat_id} latency_ms={latency_ms} "
                    f"error={notify_error}"
                )
            # Never let Telegram send failures skip finalization ordering.
            response_delivered = False

    return response_delivered


async def _finalize_message_turn(
    user_id: int,
    turn_id: int,
    response_delivered: bool,
    active_session: str,
    reset_token: int,
    user_text: str,
    result: str,
    request_id: str,
    chat_id: int | None,
    request_start_ts: float,
) -> None:
    finalize_condition = get_user_finalize_condition(user_id)
    # Reacquire the shared per-user lock only for finalization ordering and history
    # mutation. Turn numbers still serialize this section to preserve ordering.
    async with finalize_condition:
        while runtime_state.user_next_turn_to_finalize.get(user_id, 1) != turn_id:
            await finalize_condition.wait()

        try:
            if not response_delivered:
                return

            if get_session_reset_token(user_id, active_session) != reset_token:
                latency_ms = int((time.monotonic() - request_start_ts) * 1000)
                logger.info(
                    f"conversation_reset_skip_history_update request_id={request_id} "
                    f"user_id={user_id} chat_id={chat_id} latency_ms={latency_ms}"
                )
                return

            current_history = get_session_history(user_id, active_session)
            updated_history = current_history + [
                f"User: {user_text}",
                f"AI: {result}",
            ]
            ensure_user_sessions(user_id)[active_session] = updated_history[-MAX_HISTORY:]
            request_state_save("conversation_finalize")
        finally:
            runtime_state.user_next_turn_to_finalize[user_id] = turn_id + 1
            finalize_condition.notify_all()


async def _create_waiting_message(
    update: Update,
    waiting_text: str,
    *,
    error_event_name: str,
    user_id: int | None = None,
    chat_id: int | None = None,
    request_id: str | None = None,
    request_start_ts: float | None = None,
    turn_id: int | None = None,
    advance_finalize_on_failure: bool = False,
):
    try:
        return await update.message.reply_text(waiting_text)
    except Exception as waiting_msg_error:
        if request_id is not None and request_start_ts is not None and user_id is not None:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"{error_event_name} request_id={request_id} "
                f"user_id={user_id} chat_id={chat_id} latency_ms={latency_ms} "
                f"error={waiting_msg_error}"
            )

        if advance_finalize_on_failure and user_id is not None and turn_id is not None:
            finalize_condition = get_user_finalize_condition(user_id)
            async with finalize_condition:
                if runtime_state.user_next_turn_to_finalize.get(user_id, 1) == turn_id:
                    runtime_state.user_next_turn_to_finalize[user_id] = turn_id + 1
                    finalize_condition.notify_all()

        return None


async def _send_chunked_message_via_waiting_message(
    update: Update,
    waiting_msg,
    text: str,
) -> None:
    chunks = split_telegram_text(text)
    await waiting_msg.edit_text(chunks[0])
    for chunk in chunks[1:]:
        await update.message.reply_text(chunk)


async def _send_chunked_message_as_replies(update: Update, text: str) -> None:
    for chunk in split_telegram_text(text):
        await update.message.reply_text(chunk)


async def _create_chat_waiting_message(
    update: Update,
    user_id: int,
    chat_id: int | None,
    request_id: str,
    request_start_ts: float,
    turn_id: int,
):
    return await _create_waiting_message(
        update=update,
        waiting_text="생각 중…",
        error_event_name="telegram_waiting_message_failed",
        user_id=user_id,
        chat_id=chat_id,
        request_id=request_id,
        request_start_ts=request_start_ts,
        turn_id=turn_id,
        advance_finalize_on_failure=True,
    )


def _map_gateway_request_error(error: Exception) -> tuple[str, str, dict[str, str]]:
    if isinstance(error, httpx.HTTPStatusError):
        return (
            "gateway_error",
            "죄송합니다. AI 서버에서 오류가 발생했습니다. 잠시 후 다시 시도해주세요.",
            {},
        )
    if isinstance(error, httpx.ConnectTimeout):
        return (
            "gateway_connect_timeout",
            "AI 서버 연결이 지연되고 있어요. 잠시 후 다시 시도해주세요.",
            {},
        )
    if isinstance(error, httpx.ReadTimeout):
        return (
            "gateway_read_timeout",
            "응답이 오래 걸리고 있어요. 잠시 후 다시 시도해주세요.",
            {},
        )
    if isinstance(error, httpx.WriteTimeout):
        return (
            "gateway_write_timeout",
            "요청 전송이 지연되고 있어요. 잠시 후 다시 시도해주세요.",
            {},
        )
    if isinstance(error, httpx.PoolTimeout):
        return (
            "gateway_pool_timeout",
            "요청이 몰리고 있어요. 잠시 후 다시 시도해주세요.",
            {},
        )
    if isinstance(error, httpx.ConnectError):
        return (
            "gateway_connect_error",
            "죄송합니다. AI 서버와의 연결에 실패했습니다. 잠시 후 다시 시도해주세요.",
            {},
        )
    if isinstance(error, httpx.RequestError):
        return (
            "gateway_request_error",
            "죄송합니다. AI 서버와의 연결에 실패했습니다. 잠시 후 다시 시도해주세요.",
            {"error_type": type(error).__name__},
        )
    if isinstance(error, (ValueError, KeyError)):
        return (
            "gateway_response_parse_error",
            "죄송합니다. AI 응답을 처리하는 중 오류가 발생했습니다.",
            {},
        )
    return ("gateway_unexpected_error", "알 수 없는 오류가 발생했습니다.", {})


async def _handle_gateway_request_error(
    waiting_msg,
    error: Exception,
    request_id: str,
    user_id: int,
    chat_id: int | None,
    request_start_ts: float,
) -> None:
    event_name, user_message, extra_fields = _map_gateway_request_error(error)
    latency_ms = int((time.monotonic() - request_start_ts) * 1000)
    extra_parts = " ".join(f"{key}={value}" for key, value in extra_fields.items())
    extra_segment = f"{extra_parts} " if extra_parts else ""
    logger.error(
        f"{event_name} request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} latency_ms={latency_ms} {extra_segment}error={error}"
    )
    await waiting_msg.edit_text(user_message)


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_text = update.message.text
    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()

    logger.info(
        f"request_start request_id={request_id} user_id={user_id} chat_id={chat_id}"
    )

    presets = get_presets_from_bot_data(context.application.bot_data)
    lock = get_user_lock(user_id)
    # Keep the lock scope minimal while reserving one in-flight request per user.
    # The network call runs outside the lock, but concurrent messages are still
    # rejected until the current turn clears user_in_flight_requests.
    request_state = await _begin_message_turn_with_inflight_guard(
        user_id=user_id,
        user_text=user_text,
        presets=presets,
        lock=lock,
        request_id=request_id,
        chat_id=chat_id,
        update=update,
    )
    if request_state is None:
        return

    active_session = request_state["active_session"]
    reset_token = request_state["reset_token"]
    turn_id = request_state["turn_id"]
    payload = request_state["payload"]

    try:
        waiting_msg = await _create_chat_waiting_message(
            update=update,
            user_id=user_id,
            chat_id=chat_id,
            request_id=request_id,
            request_start_ts=request_start_ts,
            turn_id=turn_id,
        )
        if waiting_msg is None:
            return

        client = context.application.bot_data.get(HTTP_CLIENT_KEY)

        if client is None:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"http_client_missing request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms}"
            )
            await waiting_msg.edit_text(
                "죄송합니다. AI 서버 연결이 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요."
            )
            return

        try:
            result = await _call_gateway_with_stream_fallback(
                client=client,
                payload=payload,
                request_id=request_id,
                user_id=user_id,
                chat_id=chat_id,
                waiting_msg=waiting_msg,
                request_start_ts=request_start_ts,
            )
        except Exception as e:
            await _handle_gateway_request_error(
                waiting_msg=waiting_msg,
                error=e,
                request_id=request_id,
                user_id=user_id,
                chat_id=chat_id,
                request_start_ts=request_start_ts,
            )
            return

        response_delivered = await _deliver_telegram_response(
            update=update,
            waiting_msg=waiting_msg,
            result=result,
            request_id=request_id,
            user_id=user_id,
            chat_id=chat_id,
            request_start_ts=request_start_ts,
        )
        await _finalize_message_turn(
            user_id=user_id,
            turn_id=turn_id,
            response_delivered=response_delivered,
            active_session=active_session,
            reset_token=reset_token,
            user_text=user_text,
            result=result,
            request_id=request_id,
            chat_id=chat_id,
            request_start_ts=request_start_ts,
        )
    finally:
        async with lock:
            runtime_state.user_in_flight_requests[user_id] = False


async def handle_document(update: Update, context: ContextTypes.DEFAULT_TYPE):
    document = update.message.document
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()

    if document is None:
        await update.message.reply_text("문서 정보를 확인할 수 없어요.")
        return

    lock = get_user_lock(user_id)
    async with lock:
        if user_id not in runtime_state.user_in_flight_requests:
            runtime_state.user_in_flight_requests[user_id] = False

        if runtime_state.user_in_flight_requests[user_id]:
            logger.info(
                "document_request_rejected_inflight request_id=%s user_id=%s chat_id=%s",
                request_id,
                user_id,
                chat_id,
            )
            await update.message.reply_text(
                "이전 요청을 처리 중입니다. 잠시 후 다시 보내주세요."
            )
            return

        runtime_state.user_in_flight_requests[user_id] = True

    try:
        file_name = document.file_name or "unknown"
        if not is_supported_document(file_name):
            await update.message.reply_text(
                "지원하지 않는 파일 형식입니다. "
                f"지원 형식: {SUPPORTED_DOCUMENT_EXTENSIONS_TEXT}"
            )
            return

        if document.file_size and document.file_size > MAX_DOCUMENT_BYTES:
            await update.message.reply_text(
                f"파일이 너무 큽니다. 최대 {MAX_DOCUMENT_BYTES}바이트까지 처리할 수 있어요."
            )
            return

        waiting_msg = await _create_waiting_message(
            update=update,
            waiting_text="파일을 읽고 요약 중…",
            error_event_name="document_waiting_message_failed",
            user_id=user_id,
            chat_id=chat_id,
            request_id=request_id,
            request_start_ts=request_start_ts,
        )
        if waiting_msg is None:
            return

        try:
            client = context.application.bot_data.get(HTTP_CLIENT_KEY)
            if client is None:
                await waiting_msg.edit_text(
                    "죄송합니다. AI 서버 연결이 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요."
                )
                return

            summary = await summarize_document_text(
                document=document,
                telegram_bot=context.bot,
                client=client,
                request_id=request_id,
                chat_path=AI_GATEWAY_CHAT_PATH,
                max_document_bytes=MAX_DOCUMENT_BYTES,
                max_document_prompt_chars=MAX_DOCUMENT_PROMPT_CHARS,
                mode=get_user_document_summary_mode(user_id),
            )
            try:
                await _send_chunked_message_via_waiting_message(
                    update, waiting_msg, summary
                )
            except Exception as edit_error:
                logger.warning(
                    "document_summary_edit_failed request_id=%s user_id=%s chat_id=%s error=%s",
                    request_id,
                    user_id,
                    chat_id,
                    edit_error,
                )
                await _send_chunked_message_as_replies(update, summary)
        except DocumentValidationError as error:
            await waiting_msg.edit_text(error.message)
        except httpx.HTTPStatusError as error:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.warning(
                "document_summary_http_status_error request_id=%s user_id=%s chat_id=%s latency_ms=%s error=%s",
                request_id,
                user_id,
                chat_id,
                latency_ms,
                error,
            )
            await waiting_msg.edit_text(
                "요약 요청 처리 중 서버 오류가 발생했어요. 잠시 후 다시 시도해주세요."
            )
        except (httpx.RequestError, ValueError, KeyError) as error:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.warning(
                "document_summary_request_error request_id=%s user_id=%s chat_id=%s latency_ms=%s error=%s",
                request_id,
                user_id,
                chat_id,
                latency_ms,
                error,
            )
            await waiting_msg.edit_text(
                "문서 요약 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."
            )
    finally:
        async with lock:
            runtime_state.user_in_flight_requests[user_id] = False


async def init_http_client(app):
    timeout = httpx.Timeout(
        connect=HTTP_TIMEOUT_CONFIG["connect"],
        read=HTTP_TIMEOUT_CONFIG["read"],
        write=HTTP_TIMEOUT_CONFIG["write"],
        pool=HTTP_TIMEOUT_CONFIG["pool"],
    )
    limits = httpx.Limits(
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        max_connections=MAX_CONNECTIONS,
    )
    logger.info(
        "http_client_timeout_config connect=%s read=%s write=%s pool=%s",
        HTTP_TIMEOUT_CONFIG["connect"],
        HTTP_TIMEOUT_CONFIG["read"],
        HTTP_TIMEOUT_CONFIG["write"],
        HTTP_TIMEOUT_CONFIG["pool"],
    )
    app.bot_data[HTTP_CLIENT_KEY] = httpx.AsyncClient(
        base_url=AI_GATEWAY_BASE_URL,
        timeout=timeout,
        limits=limits,
    )
    await load_gateway_presets(app)
    scheduler = BrainAlertScheduler(
        user_brain_alert_modes=runtime_state.user_brain_alert_modes,
        user_brain_alert_times=runtime_state.user_brain_alert_times,
        last_sent_windows=runtime_state.user_brain_alert_sent_windows,
        send_alert_for_user=lambda user_id, mode: send_scheduled_brain_alert(
            app, user_id, mode
        ),
        logger=logger,
        poll_interval_seconds=BRAIN_ALERT_POLL_INTERVAL_SECONDS,
        default_time_local=DEFAULT_BRAIN_ALERT_TIME_LOCAL,
    )
    scheduler.start()
    app.bot_data[BRAIN_ALERT_SCHEDULER_KEY] = scheduler


async def close_http_client(app):
    scheduler = app.bot_data.pop(BRAIN_ALERT_SCHEDULER_KEY, None)
    if scheduler is not None:
        await scheduler.stop()

    client = app.bot_data.pop(HTTP_CLIENT_KEY, None)
    if client is not None:
        await client.aclose()


async def send_scheduled_brain_alert(app, user_id: int, mode: str) -> bool:
    client = app.bot_data.get(HTTP_CLIENT_KEY)
    if client is None:
        return False

    request_id = uuid.uuid4().hex[:12]
    try:
        brain_payload = await post_agent_brain(client, payload={}, request_id=request_id)
    except GatewayClientError as error:
        logger.warning(
            "brain_alert_schedule_fetch_failed user_id=%s mode=%s error=%s",
            user_id,
            mode,
            error.code,
        )
        return False

    if not should_send_brain_alert(mode, brain_payload):
        return False

    final_message = build_scheduled_brain_alert_message(brain_payload)
    message_chunks = split_telegram_text(final_message)
    try:
        await app.bot.send_message(chat_id=user_id, text=message_chunks[0])
        for chunk in message_chunks[1:]:
            await app.bot.send_message(chat_id=user_id, text=chunk)
    except Exception as error:  # pragma: no cover - runtime safety
        logger.warning(
            "brain_alert_schedule_telegram_send_failed user_id=%s mode=%s error=%s",
            user_id,
            mode,
            error,
        )
        return False

    return True




async def _post_agent_brain_bridge(
    client: httpx.AsyncClient,
    payload: dict,
    request_id: str | None = None,
) -> dict:
    return await post_agent_brain(client, payload=payload, request_id=request_id)


configure_operational_dependencies(
    OperationalCommandDependencies(
        load_gateway_presets=load_gateway_presets,
        get_presets_from_bot_data=get_presets_from_bot_data,
        help_message=HELP_MESSAGE,
        build_status_message=build_status_message,
        build_version_message=build_version_message,
        logger=logger,
        http_client_key=HTTP_CLIENT_KEY,
        ai_gateway_ready_path=AI_GATEWAY_READY_PATH,
        post_agent_brain=_post_agent_brain_bridge,
        split_telegram_text=split_telegram_text,
        ai_gateway_models_path=AI_GATEWAY_MODELS_PATH,
        extract_model_names=extract_model_names,
    )
)

def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN이 설정되지 않았습니다.")
    if not AI_GATEWAY_BASE_URL:
        raise ValueError("AI_GATEWAY_BASE_URL이 설정되지 않았습니다.")

    load_bot_state()

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(init_http_client)
        .post_shutdown(close_http_client)
        .build()
    )

    register_operational_handlers(app)
    app.add_handler(CommandHandler("ctx", ctx_command))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("preset", preset_command))
    app.add_handler(CommandHandler("session", session_command))
    app.add_handler(CommandHandler("session_rename", session_rename_command))
    app.add_handler(CommandHandler("session_clear", session_clear_command))
    app.add_handler(CommandHandler("session_delete", session_delete_command))
    app.add_handler(CommandHandler("sessions", sessions_command))
    app.add_handler(CommandHandler("brainalert", brainalert_command))
    app.add_handler(CommandHandler("docmode", docmode_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(build_supported_document_filter(), handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
