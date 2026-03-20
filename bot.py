import asyncio
import ast
import json
import logging
import os
import re
import shlex
import time
import uuid

import httpx
from dotenv import load_dotenv
from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    ContextTypes,
    MessageHandler,
    filters,
)

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN")
AI_GATEWAY_BASE_URL = os.getenv("AI_GATEWAY_BASE_URL")
AI_GATEWAY_CHAT_PATH = "/chat"
AI_GATEWAY_STREAM_PATH = "/generate_stream"
AI_GATEWAY_MODELS_PATH = "/models"
AI_GATEWAY_PRESETS_PATH = "/presets"
AI_GATEWAY_READY_PATH = "/health/ready"
AI_GATEWAY_AGENT_BRAIN_PATH = "/agent/brain"
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
                legacy_timeout if legacy_timeout is not None else DEFAULT_CONNECT_TIMEOUT,
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

# 사용자별 대화 저장
conversations = {}
user_active_sessions = {}
user_locks = {}
user_reset_tokens = {}
user_turn_counters = {}
user_next_turn_to_finalize = {}
user_finalize_conditions = {}
user_in_flight_requests = {}
user_selected_models = {}
user_selected_presets = {}
MODEL_RESET_ALIASES = {"default", "reset"}

LOCAL_DATA_DIR = os.getenv("LOCAL_DATA_DIR", "data")
STATE_FILE_NAME = "bot_state.json"
STATE_FILE_PATH = os.path.join(LOCAL_DATA_DIR, STATE_FILE_NAME)

DEFAULT_PRESET = "normal"
STATIC_PRESET_DEFINITIONS = {
    "normal": {"description": "기본 응답 스타일", "prompt_prefix": ""},
    "coder": {
        "description": "실용적인 코딩 중심 답변",
        "prompt_prefix": "Preset: coder. Focus on practical coding help.\n\n",
    },
    "english": {
        "description": "영어 우선 답변",
        "prompt_prefix": "Preset: english. Reply in English unless asked otherwise.\n\n",
    },
    "quant": {
        "description": "정량적 추론 중심 답변",
        "prompt_prefix": "Preset: quant. Prefer quantitative reasoning and clear assumptions.\n\n",
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
SUPPORTED_DOCUMENT_EXTENSIONS = (".txt", ".md", ".log", ".py", ".json", ".yaml", ".yml", ".csv")
SUPPORTED_DOCUMENT_EXTENSIONS_TEXT = ", ".join(SUPPORTED_DOCUMENT_EXTENSIONS)
MAX_DOCUMENT_BYTES = int(os.getenv("MAX_DOCUMENT_BYTES", "200000"))
MAX_DOCUMENT_PROMPT_CHARS = int(os.getenv("MAX_DOCUMENT_PROMPT_CHARS", "20000"))
HELP_LINES = [
    "사용 가능한 명령어",
    "/help - 명령어 안내",
    "/model - 현재 적용 중인 모델 확인",
    "/preset [name] - 현재 프리셋 확인 또는 변경",
    "/reload_presets - 게이트웨이 프리셋 다시 불러오기",
    "/models - 사용 가능한 모델 목록",
    "/health - AI 게이트웨이 준비 상태 확인",
    "/brain - 시스템 브리핑 요약",
    "/session [name] - 현재 세션 확인 또는 변경",
    "/session_rename <old> <new> - 세션 이름 변경",
    "/session_clear <name> - 세션 기록만 비우기",
    "/session_delete <name> - 세션 삭제",
    "/sessions - 보유한 세션 목록 확인",
    "/reset - 대화 기록 초기화",
    "/status - 봇 상태 확인",
    "/version - 실행 버전 정보 확인",
]
HELP_MESSAGE = "\n".join(HELP_LINES)

VERSION_ENV_KEYS = ("APP_VERSION", "VERSION")
COMMIT_ENV_KEYS = ("GIT_COMMIT_SHA", "COMMIT_SHA", "GITHUB_SHA")


def _normalize_int_key_mapping(source: dict) -> dict[int, object]:
    normalized: dict[int, object] = {}
    for key, value in source.items():
        try:
            normalized[int(key)] = value
        except (TypeError, ValueError):
            continue
    return normalized


def build_state_payload() -> dict[str, object]:
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
    }


def save_bot_state() -> None:
    try:
        os.makedirs(LOCAL_DATA_DIR, exist_ok=True)
        payload = build_state_payload()
        temp_path = f"{STATE_FILE_PATH}.tmp"
        with open(temp_path, "w", encoding="utf-8") as state_file:
            json.dump(payload, state_file, ensure_ascii=False, separators=(",", ":"))
        os.replace(temp_path, STATE_FILE_PATH)
    except OSError as error:
        logger.warning("state_save_failed path=%s error=%s", STATE_FILE_PATH, error)


def load_bot_state() -> None:
    loaded_conversations: dict[int, dict[str, list[str]]] = {}
    loaded_active_sessions: dict[int, str] = {}
    loaded_models: dict[int, str] = {}
    loaded_presets: dict[int, str] = {}

    if not os.path.exists(STATE_FILE_PATH):
        logger.info("state_file_missing path=%s", STATE_FILE_PATH)
    else:
        try:
            with open(STATE_FILE_PATH, "r", encoding="utf-8") as state_file:
                payload = json.load(state_file)
        except (OSError, json.JSONDecodeError) as error:
            logger.warning("state_load_failed path=%s error=%s", STATE_FILE_PATH, error)
        else:
            if not isinstance(payload, dict):
                logger.warning("state_load_invalid_root path=%s", STATE_FILE_PATH)
            else:
                raw_conversations = payload.get("conversations", {})
                if isinstance(raw_conversations, dict):
                    normalized_conversations = _normalize_int_key_mapping(raw_conversations)
                    for user_id, raw_history in normalized_conversations.items():
                        per_session_histories: dict[str, list[str]] = {}
                        if isinstance(raw_history, list):
                            # Backward-compatible shape: user -> history list
                            cleaned_history = [line for line in raw_history if isinstance(line, str)]
                            per_session_histories[DEFAULT_SESSION_NAME] = cleaned_history[-MAX_HISTORY:]
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
                                    -MAX_HISTORY:
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

    conversations.clear()
    conversations.update(loaded_conversations)
    user_active_sessions.clear()
    user_active_sessions.update(loaded_active_sessions)
    user_selected_models.clear()
    user_selected_models.update(loaded_models)
    user_selected_presets.clear()
    user_selected_presets.update(loaded_presets)


def get_static_presets() -> dict[str, dict[str, str]]:
    return {
        name: {
            "description": preset["description"],
            "prompt_prefix": preset["prompt_prefix"],
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

        name = raw_name.strip().lower()
        if not name:
            continue

        description = item.get("description")
        prompt_prefix = item.get("prompt_prefix")
        normalized[name] = {
            "description": description.strip() if isinstance(description, str) else "",
            "prompt_prefix": prompt_prefix if isinstance(prompt_prefix, str) else "",
        }

    return normalized


def get_presets_from_bot_data(bot_data: dict | None = None) -> dict[str, dict[str, str]]:
    if isinstance(bot_data, dict):
        presets = bot_data.get(PRESETS_KEY)
        if isinstance(presets, dict) and presets:
            return presets
    return get_static_presets()


def get_supported_preset_names(bot_data: dict | None = None) -> tuple[str, ...]:
    return tuple(get_presets_from_bot_data(bot_data).keys())


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


async def reload_presets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    reload_result = await load_gateway_presets(context.application)
    presets = get_presets_from_bot_data(context.application.bot_data)

    if reload_result["loaded_from_gateway"] and not reload_result["used_fallback"]:
        preset_names = ", ".join(presets.keys())
        await update.message.reply_text(f"프리셋을 다시 불러왔습니다: {preset_names}")
        return

    await update.message.reply_text("게이트웨이 프리셋을 불러오지 못해 기본 프리셋으로 유지합니다.")


def normalize_session_name(raw_name: str) -> str:
    normalized = raw_name.strip()
    if not normalized:
        return DEFAULT_SESSION_NAME
    return normalized[:32]


def get_active_session_name(user_id: int) -> str:
    session_name = user_active_sessions.get(user_id)
    if not isinstance(session_name, str):
        return DEFAULT_SESSION_NAME
    return normalize_session_name(session_name)


def ensure_user_sessions(user_id: int) -> dict[str, list[str]]:
    raw_per_session = conversations.get(user_id)
    if isinstance(raw_per_session, dict):
        per_session = raw_per_session
    elif isinstance(raw_per_session, list):
        # Backward-compatibility for already-loaded in-memory legacy shape.
        per_session = {DEFAULT_SESSION_NAME: [line for line in raw_per_session if isinstance(line, str)]}
        conversations[user_id] = per_session
    else:
        per_session = {}
        conversations[user_id] = per_session

    for session_name, history in list(per_session.items()):
        if not isinstance(session_name, str) or not isinstance(history, list):
            per_session.pop(session_name, None)
            continue

        normalized_name = normalize_session_name(session_name)
        cleaned_history = [line for line in history if isinstance(line, str)][-MAX_HISTORY:]
        if normalized_name != session_name:
            per_session.pop(session_name, None)
        per_session[normalized_name] = cleaned_history

    return per_session


def get_session_history(user_id: int, session_name: str | None = None) -> list[str]:
    active_session = normalize_session_name(session_name or get_active_session_name(user_id))
    per_session = ensure_user_sessions(user_id)
    return per_session.setdefault(active_session, [])




def get_session_reset_token(user_id: int, session_name: str) -> int:
    per_session_tokens = user_reset_tokens.get(user_id)
    if not isinstance(per_session_tokens, dict):
        per_session_tokens = {}
        user_reset_tokens[user_id] = per_session_tokens

    normalized_session_name = normalize_session_name(session_name)
    token = per_session_tokens.get(normalized_session_name)
    if not isinstance(token, int):
        token = 0
        per_session_tokens[normalized_session_name] = token

    return token


def increment_session_reset_token(user_id: int, session_name: str) -> int:
    current_token = get_session_reset_token(user_id, session_name)
    next_token = current_token + 1
    user_reset_tokens[user_id][normalize_session_name(session_name)] = next_token
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
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


def get_user_finalize_condition(user_id):
    lock = get_user_lock(user_id)
    if user_id not in user_finalize_conditions:
        user_finalize_conditions[user_id] = asyncio.Condition(lock)
    return user_finalize_conditions[user_id]


def get_user_selected_model(user_id: int) -> str | None:
    selected_model = user_selected_models.get(user_id)
    if not isinstance(selected_model, str):
        return None

    normalized_model = selected_model.strip()
    return normalized_model or None


def get_user_selected_preset(user_id: int, presets: dict[str, dict[str, str]] | None = None) -> str | None:
    selected_preset = user_selected_presets.get(user_id)
    if not isinstance(selected_preset, str):
        return None

    normalized_preset = selected_preset.strip().lower()
    available_presets = presets if presets is not None else get_static_presets()
    if normalized_preset not in available_presets:
        return None

    return normalized_preset


def resolve_active_preset(user_id: int, presets: dict[str, dict[str, str]] | None = None) -> str:
    available_presets = presets if presets is not None else get_static_presets()
    if DEFAULT_PRESET not in available_presets and available_presets:
        return next(iter(available_presets.keys()))
    return get_user_selected_preset(user_id, available_presets) or DEFAULT_PRESET


def build_prompt_with_preset(
    history_lines: list[str],
    active_preset: str,
    presets: dict[str, dict[str, str]] | None = None,
) -> str:
    prompt = "\n".join(history_lines) + "\nAI:"
    available_presets = presets if presets is not None else get_static_presets()
    preset_definition = available_presets.get(active_preset, {})
    preset_prefix = preset_definition.get("prompt_prefix", "")
    if not preset_prefix:
        return prompt

    return f"{preset_prefix}{prompt}"


def build_gateway_payload(prompt: str, selected_model: str | None = None) -> dict[str, str]:
    payload = {"prompt": prompt}
    if selected_model:
        payload["model"] = selected_model
    return payload


class GatewayClientError(Exception):
    """Controlled gateway client error for recoverable request/response failures."""


async def post_agent_brain(
    client: httpx.AsyncClient,
    payload: dict,
    request_id: str | None = None,
) -> dict:
    headers = {"X-Request-Id": request_id} if request_id else None

    try:
        response = await client.post(
            AI_GATEWAY_AGENT_BRAIN_PATH,
            json=payload,
            headers=headers,
        )
        response.raise_for_status()
    except (httpx.TimeoutException, httpx.RequestError, httpx.HTTPStatusError) as error:
        raise GatewayClientError("agent_brain_request_failed") from error

    try:
        body = response.json()
    except ValueError as error:
        raise GatewayClientError("agent_brain_invalid_json") from error

    if not isinstance(body, dict):
        raise GatewayClientError("agent_brain_malformed_response")

    return body


def format_brain_overall_status(overall_status: str | None) -> str:
    normalized_status = overall_status.strip().lower() if isinstance(overall_status, str) else ""

    if normalized_status == "ok":
        return "현재 즉시 대응이 필요한 징후는 없습니다."
    if normalized_status == "partial":
        return "일부 상태 정보가 누락되어 있어 확인이 필요합니다."

    return "상태 정보를 확인하지 못했어요."


def build_brain_message(overall_status: str | None, message_lines: list[str]) -> str:
    formatted_status = format_brain_overall_status(overall_status)

    normalized_lines = [line.strip() for line in message_lines if isinstance(line, str) and line.strip()]
    if not normalized_lines:
        normalized_lines = ["브리핑 세부 항목이 비어 있어요."]

    section_lines = "\n".join(f"- {line}" for line in normalized_lines)

    return "\n".join(
        [
            "📊 오늘 브리핑",
            "",
            "[서버]",
            section_lines,
            "",
            "[상태]",
            f"- {formatted_status}",
        ]
    )


def is_supported_document(file_name: str | None) -> bool:
    if not isinstance(file_name, str):
        return False
    lowered = file_name.lower()
    return any(lowered.endswith(ext) for ext in SUPPORTED_DOCUMENT_EXTENSIONS)


def build_supported_document_filter():
    extension_filters = [
        filters.Document.FileExtension(extension.lstrip("."))
        for extension in SUPPORTED_DOCUMENT_EXTENSIONS
    ]
    merged_filter = extension_filters[0]
    for extension_filter in extension_filters[1:]:
        merged_filter = merged_filter | extension_filter
    return merged_filter


def build_document_summary_prompt(file_name: str, content: str) -> str:
    return (
        "다음 문서를 한국어로 간결하게 요약해줘.\n"
        "요구사항:\n"
        "- 핵심 내용을 3~5개 bullet로 정리\n"
        "- 전체 요약은 짧고 명확하게 유지\n"
        f"- 파일명: {file_name}\n\n"
        f"문서 원문:\n{content}"
    )


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lock = get_user_lock(user_id)
    async with lock:
        active_session = get_active_session_name(user_id)
        get_session_history(user_id, active_session).clear()
        increment_session_reset_token(user_id, active_session)
        save_bot_state()
    await update.message.reply_text("대화 기록을 초기화했습니다.")


async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    requested_session = " ".join(context.args).strip() if context.args else ""

    if not requested_session:
        active_session = get_active_session_name(user_id)
        per_session = ensure_user_sessions(user_id)
        per_session.setdefault(active_session, get_session_history(user_id, active_session))
        session_names = sorted(per_session.keys())
        available_sessions_lines = "\n".join(f"- {name}" for name in session_names)
        if not available_sessions_lines:
            available_sessions_lines = "- (none)"

        await update.message.reply_text(
            "\n".join(
                [
                    f"현재 세션: {active_session}",
                    f"전체 세션 수: {len(session_names)}",
                    "",
                    "보유한 세션:",
                    available_sessions_lines,
                ]
            )
        )
        return

    next_session = normalize_session_name(requested_session)
    lock = get_user_lock(user_id)
    async with lock:
        user_active_sessions[user_id] = next_session
        get_session_history(user_id, next_session)
        save_bot_state()
    await update.message.reply_text(f"세션 변경: {next_session}")


async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    active_session = get_active_session_name(user_id)
    session_names = sorted(ensure_user_sessions(user_id).keys())

    available_sessions_lines = "\n".join(f"- {name}" for name in session_names)
    if not available_sessions_lines:
        available_sessions_lines = "- (none)"

    await update.message.reply_text(
        "\n".join(
            [
                f"현재 세션: {active_session}",
                "",
                "보유한 세션 목록:",
                available_sessions_lines,
            ]
        )
    )


async def session_rename_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id

    raw_text = update.message.text if update.message and isinstance(update.message.text, str) else ""
    args_text = raw_text.partition(" ")[2].strip()

    parsed_args: list[str] = []
    if args_text:
        try:
            parsed_args = shlex.split(args_text)
        except ValueError:
            parsed_args = []

    if len(parsed_args) >= 2:
        old_name, new_name = parsed_args[0], parsed_args[1]
    elif len(context.args) >= 2:
        old_name, new_name = context.args[0], context.args[1]
    else:
        await update.message.reply_text("기존 세션 이름과 새 세션 이름을 모두 입력해주세요.")
        return

    old_session = normalize_session_name(old_name)
    new_session = normalize_session_name(new_name)

    if old_session == new_session:
        await update.message.reply_text("변경 전/후 세션 이름이 같아요. 다른 이름을 입력해주세요.")
        return

    if old_session == DEFAULT_SESSION_NAME:
        await update.message.reply_text("기본 세션 이름은 변경할 수 없어요.")
        return

    if new_session == DEFAULT_SESSION_NAME:
        await update.message.reply_text("기본 세션 이름으로는 변경할 수 없어요.")
        return

    renamed = False
    duplicate_name = False
    active_session = get_active_session_name(user_id)
    lock = get_user_lock(user_id)
    async with lock:
        per_session = ensure_user_sessions(user_id)
        if old_session not in per_session:
            pass
        elif new_session in per_session:
            duplicate_name = True
        else:
            per_session[new_session] = per_session.pop(old_session)
            if active_session == old_session:
                user_active_sessions[user_id] = new_session
            save_bot_state()
            renamed = True

    if duplicate_name:
        await update.message.reply_text(f"이미 존재하는 세션 이름이에요: {new_session}")
        return

    if not renamed:
        await update.message.reply_text(f"세션을 찾을 수 없어요: {old_session}")
        return

    await update.message.reply_text(f"세션 이름이 변경되었습니다: {old_session} → {new_session}")


async def session_delete_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    requested_session = " ".join(context.args).strip() if context.args else ""

    if not requested_session:
        await update.message.reply_text("삭제할 세션 이름을 입력해주세요.")
        return

    target_session = normalize_session_name(requested_session)
    active_session = get_active_session_name(user_id)

    if target_session == active_session:
        await update.message.reply_text("현재 사용 중인 세션은 삭제할 수 없어요.")
        return

    if target_session == DEFAULT_SESSION_NAME:
        await update.message.reply_text("기본 세션은 삭제할 수 없어요.")
        return

    deleted = False
    lock = get_user_lock(user_id)
    async with lock:
        per_session = ensure_user_sessions(user_id)
        if target_session in per_session:
            per_session.pop(target_session, None)
            save_bot_state()
            deleted = True

    if not deleted:
        await update.message.reply_text(f"세션을 찾을 수 없어요: {target_session}")
        return

    await update.message.reply_text(f"세션이 삭제되었습니다: {target_session}")


async def session_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    requested_session = " ".join(context.args).strip() if context.args else ""

    if not requested_session:
        await update.message.reply_text("비울 세션 이름을 입력해주세요.")
        return

    target_session = normalize_session_name(requested_session)

    cleared = False
    lock = get_user_lock(user_id)
    async with lock:
        per_session = ensure_user_sessions(user_id)
        if target_session in per_session:
            per_session[target_session] = []
            if target_session == get_active_session_name(user_id):
                increment_session_reset_token(user_id, target_session)
            save_bot_state()
            cleared = True

    if not cleared:
        await update.message.reply_text(f"세션을 찾을 수 없어요: {target_session}")
        return

    await update.message.reply_text(f"세션 기록을 비웠습니다: {target_session}")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(HELP_MESSAGE)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_status_message(context))


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(build_version_message())


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()
    logger.info(f"health_check_start request_id={request_id} user_id={user_id} chat_id={chat_id}")

    client = context.application.bot_data.get(HTTP_CLIENT_KEY)
    if client is None:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.error(
            f"health_check_client_missing request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms}"
        )
        await update.message.reply_text("게이트웨이에 연결할 수 없어요. 잠시 후 다시 시도해주세요.")
        return

    try:
        response = await client.get(
            AI_GATEWAY_READY_PATH,
            headers={"X-Request-Id": request_id},
        )
        response.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as error:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.warning(
            f"health_check_failed request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} error={error}"
        )
        await update.message.reply_text("게이트웨이 상태가 불안정하거나 사용할 수 없어요.")
        return

    latency_ms = int((time.monotonic() - request_start_ts) * 1000)
    logger.info(
        f"health_check_success request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} latency_ms={latency_ms}"
    )
    await update.message.reply_text("게이트웨이가 정상적으로 준비되어 있어요.")


async def brain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    request_id = uuid.uuid4().hex[:12]

    client = context.application.bot_data.get(HTTP_CLIENT_KEY)
    if client is None:
        await update.message.reply_text("게이트웨이에 연결할 수 없어요. 잠시 후 다시 시도해주세요.")
        return

    try:
        brain_payload = await post_agent_brain(
            client,
            payload={},
            request_id=request_id,
        )
    except GatewayClientError as error:
        logger.warning(
            "brain_command_failed request_id=%s user_id=%s chat_id=%s error=%s",
            request_id,
            user_id,
            chat_id,
            error,
        )
        await update.message.reply_text("브리핑 정보를 불러오지 못했어요. 잠시 후 다시 시도해주세요.")
        return

    overall_status = brain_payload.get("overall_status")
    message_lines = brain_payload.get("message_lines")
    if not isinstance(message_lines, list):
        message_lines = []

    await update.message.reply_text(build_brain_message(overall_status, message_lines))


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
            user_selected_models.pop(user_id, None)
            save_bot_state()
        await update.message.reply_text("모델 설정을 초기화했습니다. 기본 모델을 사용합니다.")
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
        await update.message.reply_text("모델 확인에 실패했어요. 잠시 후 다시 시도해주세요.")
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
        user_selected_models[user_id] = requested_model
        save_bot_state()

    latency_ms = int((time.monotonic() - request_start_ts) * 1000)
    logger.info(
        f"model_validate_success request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} latency_ms={latency_ms} selected_model={requested_model}"
    )
    await update.message.reply_text(f"모델이 변경되었습니다: {requested_model}")


async def preset_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    requested_preset = " ".join(context.args).strip().lower() if context.args else ""

    presets = get_presets_from_bot_data(context.application.bot_data)

    if requested_preset:
        if requested_preset not in presets:
            supported_presets_text = ", ".join(presets.keys())
            await update.message.reply_text(
                f"지원하지 않는 프리셋입니다. 사용 가능: {supported_presets_text}"
            )
            return

        lock = get_user_lock(user_id)
        async with lock:
            user_selected_presets[user_id] = requested_preset
            save_bot_state()
        await update.message.reply_text(f"프리셋이 변경되었습니다: {requested_preset}")
        return

    active_preset = resolve_active_preset(user_id, presets)
    await update.message.reply_text(f"현재 프리셋: {active_preset}")


def extract_model_names(payload) -> list[str]:
    if isinstance(payload, dict):
        if isinstance(payload.get("models"), list):
            source = payload["models"]
        elif isinstance(payload.get("data"), list):
            source = payload["data"]
        else:
            source = []
    elif isinstance(payload, list):
        source = payload
    else:
        source = []

    model_names = []
    for item in source:
        if isinstance(item, str):
            model_names.append(item)
        elif isinstance(item, dict):
            model_id = item.get("id") or item.get("name")
            if isinstance(model_id, str):
                model_names.append(model_id)

    return model_names


async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()
    logger.info(f"models_request_start request_id={request_id} user_id={user_id} chat_id={chat_id}")

    client = context.application.bot_data.get(HTTP_CLIENT_KEY)
    if client is None:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.error(
            f"models_http_client_missing request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms}"
        )
        await update.message.reply_text("죄송해요. 지금은 모델 목록을 가져올 수 없어요.")
        return

    try:
        response = await client.get(
            AI_GATEWAY_MODELS_PATH,
            headers={"X-Request-Id": request_id},
        )
        response.raise_for_status()
        model_names = extract_model_names(response.json())
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.warning(
            f"models_request_failed request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} error={error}"
        )
        await update.message.reply_text("죄송해요. 모델 목록을 불러오지 못했어요. 잠시 후 다시 시도해주세요.")
        return

    if not model_names:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        logger.info(
            f"models_request_empty request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms}"
        )
        await update.message.reply_text("현재 확인 가능한 모델이 없어요.")
        return

    displayed_models = model_names[:8]
    listed_models = "\n".join(f"- {name}" for name in displayed_models)
    if len(model_names) > len(displayed_models):
        listed_models += "\n- ..."

    latency_ms = int((time.monotonic() - request_start_ts) * 1000)
    logger.info(
        f"models_request_success request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} latency_ms={latency_ms} model_count={len(model_names)}"
    )
    await update.message.reply_text(f"사용 가능한 모델 목록\n{listed_models}")



async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    user_text = update.message.text
    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()

    logger.info(f"request_start request_id={request_id} user_id={user_id} chat_id={chat_id}")

    presets = get_presets_from_bot_data(context.application.bot_data)
    lock = get_user_lock(user_id)

    # Keep the per-user lock scope minimal: only protect shared state reads/writes
    # needed to prepare this turn. The potentially slow AI call runs without holding
    # the lock so later messages from the same user can start in parallel.
    async with lock:
        active_session = get_active_session_name(user_id)
        history = get_session_history(user_id, active_session)
        if user_id not in user_turn_counters:
            user_turn_counters[user_id] = 0
        if user_id not in user_next_turn_to_finalize:
            user_next_turn_to_finalize[user_id] = 1
        if user_id not in user_in_flight_requests:
            user_in_flight_requests[user_id] = False

        if user_in_flight_requests[user_id]:
            logger.info(
                f"request_rejected_inflight request_id={request_id} "
                f"user_id={user_id} chat_id={chat_id}"
            )
            await update.message.reply_text("이전 요청을 처리 중입니다. 잠시 후 다시 보내주세요.")
            return

        user_in_flight_requests[user_id] = True

        old_history = history[:]
        reset_token = get_session_reset_token(user_id, active_session)
        user_turn_counters[user_id] += 1
        turn_id = user_turn_counters[user_id]
        new_history = old_history + [f"User: {user_text}"]
        new_history = new_history[-MAX_HISTORY:]
        selected_model = get_user_selected_model(user_id)
        active_preset = resolve_active_preset(user_id, presets)

    try:
        try:
            waiting_msg = await update.message.reply_text("생각 중…")
        except Exception as waiting_msg_error:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"telegram_waiting_message_failed request_id={request_id} "
                f"user_id={user_id} chat_id={chat_id} latency_ms={latency_ms} "
                f"error={waiting_msg_error}"
            )
            finalize_condition = get_user_finalize_condition(user_id)
            async with finalize_condition:
                if user_next_turn_to_finalize.get(user_id, 1) == turn_id:
                    user_next_turn_to_finalize[user_id] = turn_id + 1
                    finalize_condition.notify_all()
            return

        prompt = build_prompt_with_preset(new_history, active_preset, presets)
        payload = build_gateway_payload(prompt, selected_model)
        gateway_headers = {"X-Request-Id": request_id}
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

        stream_result = ""
        stream_completed_normally = False
        stream_failed = False
        last_rendered_text = ""
        last_edit_ts = 0.0

        try:
            try:
                async with client.stream(
                    "POST", AI_GATEWAY_STREAM_PATH, json=payload, headers=gateway_headers
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
        except httpx.HTTPStatusError as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_error request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text(
                "죄송합니다. AI 서버에서 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
            )
            return
        except httpx.ConnectTimeout as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_connect_timeout request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text("AI 서버 연결이 지연되고 있어요. 잠시 후 다시 시도해주세요.")
            return
        except httpx.ReadTimeout as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_read_timeout request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text("응답이 오래 걸리고 있어요. 잠시 후 다시 시도해주세요.")
            return
        except httpx.WriteTimeout as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_write_timeout request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text("요청 전송이 지연되고 있어요. 잠시 후 다시 시도해주세요.")
            return
        except httpx.PoolTimeout as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_pool_timeout request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text("요청이 몰리고 있어요. 잠시 후 다시 시도해주세요.")
            return
        except httpx.ConnectError as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_connect_error request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text(
                "죄송합니다. AI 서버와의 연결에 실패했습니다. 잠시 후 다시 시도해주세요."
            )
            return
        except httpx.RequestError as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            request_error_type = type(e).__name__
            logger.error(
                f"gateway_request_error request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} "
                f"error_type={request_error_type} error={e}"
            )
            await waiting_msg.edit_text(
                "죄송합니다. AI 서버와의 연결에 실패했습니다. 잠시 후 다시 시도해주세요."
            )
            return
        except (ValueError, KeyError) as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_response_parse_error request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text("죄송합니다. AI 응답을 처리하는 중 오류가 발생했습니다.")
            return
        except Exception as e:
            latency_ms = int((time.monotonic() - request_start_ts) * 1000)
            logger.error(
                f"gateway_unexpected_error request_id={request_id} user_id={user_id} "
                f"chat_id={chat_id} latency_ms={latency_ms} error={e}"
            )
            await waiting_msg.edit_text("알 수 없는 오류가 발생했습니다.")
            return

        response_delivered = True
        try:
            final_chunks = split_telegram_text(result)
            await waiting_msg.edit_text(final_chunks[0])
            for chunk in final_chunks[1:]:
                await update.message.reply_text(chunk)
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
                final_chunks = split_telegram_text(result)
                for chunk in final_chunks:
                    await update.message.reply_text(chunk)
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

        finalize_condition = get_user_finalize_condition(user_id)
        # Reacquire the shared per-user lock only for finalization ordering and history
        # mutation. Turn numbers still serialize this section to preserve ordering.
        async with finalize_condition:
            while user_next_turn_to_finalize.get(user_id, 1) != turn_id:
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
                updated_history = current_history + [f"User: {user_text}", f"AI: {result}"]
                ensure_user_sessions(user_id)[active_session] = updated_history[-MAX_HISTORY:]
                save_bot_state()
            finally:
                user_next_turn_to_finalize[user_id] = turn_id + 1
                finalize_condition.notify_all()
    finally:
        async with lock:
            user_in_flight_requests[user_id] = False


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
        if user_id not in user_in_flight_requests:
            user_in_flight_requests[user_id] = False

        if user_in_flight_requests[user_id]:
            logger.info(
                "document_request_rejected_inflight request_id=%s user_id=%s chat_id=%s",
                request_id,
                user_id,
                chat_id,
            )
            await update.message.reply_text("이전 요청을 처리 중입니다. 잠시 후 다시 보내주세요.")
            return

        user_in_flight_requests[user_id] = True

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

        try:
            waiting_msg = await update.message.reply_text("파일을 읽고 요약 중…")
        except Exception:
            return

        try:
            telegram_file = await context.bot.get_file(document.file_id)
            file_bytes = bytes(await telegram_file.download_as_bytearray())
            if len(file_bytes) > MAX_DOCUMENT_BYTES:
                await waiting_msg.edit_text(
                    f"파일이 너무 큽니다. 최대 {MAX_DOCUMENT_BYTES}바이트까지 처리할 수 있어요."
                )
                return

            try:
                text_content = file_bytes.decode("utf-8")
            except UnicodeDecodeError:
                await waiting_msg.edit_text(
                    "UTF-8 텍스트 파일만 처리할 수 있어요. 인코딩을 확인한 뒤 다시 업로드해주세요."
                )
                return

            text_content = text_content[:MAX_DOCUMENT_PROMPT_CHARS]

            client = context.application.bot_data.get(HTTP_CLIENT_KEY)
            if client is None:
                await waiting_msg.edit_text(
                    "죄송합니다. AI 서버 연결이 아직 준비되지 않았습니다. 잠시 후 다시 시도해주세요."
                )
                return

            prompt = build_document_summary_prompt(file_name, text_content)
            payload = build_gateway_payload(prompt)
            response = await client.post(
                AI_GATEWAY_CHAT_PATH,
                json=payload,
                headers={"X-Request-Id": request_id},
            )
            response.raise_for_status()
            summary = response.json()["response"]
            summary_chunks = split_telegram_text(summary)
            try:
                await waiting_msg.edit_text(summary_chunks[0])
                for chunk in summary_chunks[1:]:
                    await update.message.reply_text(chunk)
            except Exception as edit_error:
                logger.warning(
                    "document_summary_edit_failed request_id=%s user_id=%s chat_id=%s error=%s",
                    request_id,
                    user_id,
                    chat_id,
                    edit_error,
                )
                for chunk in summary_chunks:
                    await update.message.reply_text(chunk)
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
            await waiting_msg.edit_text("요약 요청 처리 중 서버 오류가 발생했어요. 잠시 후 다시 시도해주세요.")
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
            await waiting_msg.edit_text("문서 요약 중 오류가 발생했어요. 잠시 후 다시 시도해주세요.")
    finally:
        async with lock:
            user_in_flight_requests[user_id] = False


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


async def close_http_client(app):
    client = app.bot_data.pop(HTTP_CLIENT_KEY, None)
    if client is not None:
        await client.aclose()


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

    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("model", model_command))
    app.add_handler(CommandHandler("preset", preset_command))
    app.add_handler(CommandHandler("reload_presets", reload_presets_command))
    app.add_handler(CommandHandler("models", models_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("brain", brain_command))
    app.add_handler(CommandHandler("session", session_command))
    app.add_handler(CommandHandler("session_rename", session_rename_command))
    app.add_handler(CommandHandler("session_clear", session_clear_command))
    app.add_handler(CommandHandler("session_delete", session_delete_command))
    app.add_handler(CommandHandler("sessions", sessions_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("version", version_command))
    app.add_handler(MessageHandler(build_supported_document_filter(), handle_document))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
