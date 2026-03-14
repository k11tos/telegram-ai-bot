import asyncio
import ast
import json
import logging
import os
import re
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

SUPPORTED_PRESETS = ("normal", "coder", "english", "quant")
DEFAULT_PRESET = "normal"
PRESET_PROMPT_PREFIXES = {
    "normal": "",
    "coder": "Preset: coder. Focus on practical coding help.",
    "english": "Preset: english. Reply in English unless asked otherwise.",
    "quant": "Preset: quant. Prefer quantitative reasoning and clear assumptions.",
}

if set(PRESET_PROMPT_PREFIXES.keys()) != set(SUPPORTED_PRESETS):
    raise AssertionError("SUPPORTED_PRESETS and PRESET_PROMPT_PREFIXES keys must match exactly")

MAX_HISTORY = 10
HTTP_CLIENT_KEY = "http_client"
TELEGRAM_MESSAGE_MAX_LEN = 4096
STREAM_EDIT_INTERVAL_SEC = 1.0
HELP_LINES = [
    "사용 가능한 명령어",
    "/help - 명령어 안내",
    "/model - 현재 적용 중인 모델 확인",
    "/preset [name] - 현재 프리셋 확인 또는 변경",
    "/models - 사용 가능한 모델 목록",
    "/health - AI 게이트웨이 준비 상태 확인",
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
    return {
        "version": 1,
        "conversations": {str(user_id): history for user_id, history in conversations.items()},
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
    loaded_conversations: dict[int, list[str]] = {}
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
                    for user_id, history in normalized_conversations.items():
                        if not isinstance(history, list):
                            continue
                        cleaned_history = [line for line in history if isinstance(line, str)]
                        if cleaned_history:
                            loaded_conversations[user_id] = cleaned_history[-MAX_HISTORY:]

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
                        if normalized_preset in SUPPORTED_PRESETS:
                            loaded_presets[user_id] = normalized_preset

    conversations.clear()
    conversations.update(loaded_conversations)
    user_selected_models.clear()
    user_selected_models.update(loaded_models)
    user_selected_presets.clear()
    user_selected_presets.update(loaded_presets)


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


def get_user_selected_preset(user_id: int) -> str | None:
    selected_preset = user_selected_presets.get(user_id)
    if not isinstance(selected_preset, str):
        return None

    normalized_preset = selected_preset.strip().lower()
    if normalized_preset not in SUPPORTED_PRESETS:
        return None

    return normalized_preset


def resolve_active_preset(user_id: int) -> str:
    return get_user_selected_preset(user_id) or DEFAULT_PRESET


def build_prompt_with_preset(history_lines: list[str], active_preset: str) -> str:
    prompt = "\n".join(history_lines) + "\nAI:"
    preset_prefix = PRESET_PROMPT_PREFIXES.get(active_preset, "")
    if not preset_prefix:
        return prompt

    return f"{preset_prefix}\n\n{prompt}"


def build_gateway_payload(prompt: str, selected_model: str | None = None) -> dict[str, str]:
    payload = {"prompt": prompt}
    if selected_model:
        payload["model"] = selected_model
    return payload


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lock = get_user_lock(user_id)
    async with lock:
        conversations[user_id] = []
        user_reset_tokens[user_id] = user_reset_tokens.get(user_id, 0) + 1
        save_bot_state()
    await update.message.reply_text("대화 기록을 초기화했습니다.")


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

    if requested_preset:
        if requested_preset not in SUPPORTED_PRESETS:
            supported_presets_text = ", ".join(SUPPORTED_PRESETS)
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

    active_preset = resolve_active_preset(user_id)
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

    lock = get_user_lock(user_id)

    # Keep the per-user lock scope minimal: only protect shared state reads/writes
    # needed to prepare this turn. The potentially slow AI call runs without holding
    # the lock so later messages from the same user can start in parallel.
    async with lock:
        if user_id not in conversations:
            conversations[user_id] = []
        if user_id not in user_reset_tokens:
            user_reset_tokens[user_id] = 0
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

        old_history = conversations[user_id][:]
        reset_token = user_reset_tokens[user_id]
        user_turn_counters[user_id] += 1
        turn_id = user_turn_counters[user_id]
        new_history = old_history + [f"User: {user_text}"]
        new_history = new_history[-MAX_HISTORY:]
        selected_model = get_user_selected_model(user_id)
        active_preset = resolve_active_preset(user_id)

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

        prompt = build_prompt_with_preset(new_history, active_preset)
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
            final_text = fit_telegram_text(result)
            await waiting_msg.edit_text(final_text)
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
                await update.message.reply_text(fit_telegram_text(result))
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

                if user_reset_tokens.get(user_id, 0) != reset_token:
                    latency_ms = int((time.monotonic() - request_start_ts) * 1000)
                    logger.info(
                        f"conversation_reset_skip_history_update request_id={request_id} "
                        f"user_id={user_id} chat_id={chat_id} latency_ms={latency_ms}"
                    )
                    return

                current_history = conversations.get(user_id, [])
                updated_history = current_history + [f"User: {user_text}", f"AI: {result}"]
                conversations[user_id] = updated_history[-MAX_HISTORY:]
                save_bot_state()
            finally:
                user_next_turn_to_finalize[user_id] = turn_id + 1
                finalize_condition.notify_all()
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
    app.add_handler(CommandHandler("models", models_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("version", version_command))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
