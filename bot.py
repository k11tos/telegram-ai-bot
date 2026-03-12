import asyncio
import ast
import json
import logging
import os
import re
import time

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
TIMEOUT = float(os.getenv("TIMEOUT", "300"))
AI_GATEWAY_BASE_URL = os.getenv("AI_GATEWAY_BASE_URL")
AI_GATEWAY_CHAT_PATH = "/chat"
AI_GATEWAY_STREAM_PATH = "/generate_stream"
MAX_KEEPALIVE_CONNECTIONS = int(os.getenv("MAX_KEEPALIVE_CONNECTIONS", "20"))
MAX_CONNECTIONS = int(os.getenv("MAX_CONNECTIONS", "100"))

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

MAX_HISTORY = 10
HTTP_CLIENT_KEY = "http_client"
TELEGRAM_MESSAGE_MAX_LEN = 4096
STREAM_EDIT_INTERVAL_SEC = 1.0


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


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    lock = get_user_lock(user_id)
    async with lock:
        conversations[user_id] = []
        user_reset_tokens[user_id] = user_reset_tokens.get(user_id, 0) + 1
    await update.message.reply_text("대화 기록을 초기화했습니다.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    lock = get_user_lock(user_id)
    waiting_msg = await update.message.reply_text("생각 중…")

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

        old_history = conversations[user_id][:]
        reset_token = user_reset_tokens[user_id]
        user_turn_counters[user_id] += 1
        turn_id = user_turn_counters[user_id]
        new_history = old_history + [f"User: {user_text}"]
        new_history = new_history[-MAX_HISTORY:]

    prompt = "\n".join(new_history) + "\nAI:"
    payload = {"prompt": prompt}
    client = context.application.bot_data.get(HTTP_CLIENT_KEY)

    if client is None:
        logger.error("Shared HTTP client is not initialized")
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
            async with client.stream("POST", AI_GATEWAY_STREAM_PATH, json=payload) as response:
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
                            logger.warning(f"Telegram stream edit failed: {stream_edit_error}")
        except (httpx.HTTPStatusError, httpx.RequestError) as stream_error:
            stream_failed = True
            logger.warning(f"Streaming request failed; will fall back to /chat: {stream_error}")

        result = stream_result.strip()
        should_fallback = stream_failed or not result or not stream_completed_normally
        if should_fallback:
            if result and not stream_completed_normally:
                logger.warning(
                    "Discarding partial streamed output due to missing completion signal; "
                    "falling back to /chat response"
                )
            fallback_resp = await client.post(AI_GATEWAY_CHAT_PATH, json=payload)
            fallback_resp.raise_for_status()
            result = fallback_resp.json()["response"]
    except httpx.HTTPStatusError as e:
        logger.error(f"HTTP error occurred: {e}")
        await waiting_msg.edit_text(
            "죄송합니다. AI 서버에서 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
        )
        return
    except httpx.RequestError as e:
        logger.error(f"Request error occurred: {e}")
        await waiting_msg.edit_text(
            "죄송합니다. AI 서버와의 연결에 실패했습니다. 잠시 후 다시 시도해주세요."
        )
        return
    except (ValueError, KeyError) as e:
        logger.error(f"Failed to parse JSON response: {e}")
        await waiting_msg.edit_text("죄송합니다. AI 응답을 처리하는 중 오류가 발생했습니다.")
        return
    except Exception as e:
        logger.error(f"Unexpected error: {e}")
        await waiting_msg.edit_text("알 수 없는 오류가 발생했습니다.")
        return

    response_delivered = True
    try:
        final_text = fit_telegram_text(result)
        await waiting_msg.edit_text(final_text)
    except Exception as edit_error:
        logger.error(f"Telegram message edit failed: {edit_error}")
        try:
            await update.message.reply_text(fit_telegram_text(result))
        except Exception as reply_error:
            logger.error(f"Telegram fallback reply failed: {reply_error}")
            error_summary = str(reply_error).strip() or type(reply_error).__name__
            if len(error_summary) > 120:
                error_summary = error_summary[:117] + "..."
            await update.message.reply_text(
                f"AI 응답 전송 중 오류가 발생했습니다. ({error_summary})"
            )
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
                logger.info("Conversation reset detected during request; skipping stale history update.")
                return

            current_history = conversations.get(user_id, [])
            updated_history = current_history + [f"User: {user_text}", f"AI: {result}"]
            conversations[user_id] = updated_history[-MAX_HISTORY:]
        finally:
            user_next_turn_to_finalize[user_id] = turn_id + 1
            finalize_condition.notify_all()


async def init_http_client(app):
    timeout = httpx.Timeout(TIMEOUT)
    limits = httpx.Limits(
        max_keepalive_connections=MAX_KEEPALIVE_CONNECTIONS,
        max_connections=MAX_CONNECTIONS,
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

    app = (
        ApplicationBuilder()
        .token(BOT_TOKEN)
        .post_init(init_http_client)
        .post_shutdown(close_http_client)
        .build()
    )

    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
