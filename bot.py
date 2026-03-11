import asyncio
import logging
import os

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
AI_GATEWAY = os.getenv("AI_GATEWAY")
TIMEOUT = float(os.getenv("TIMEOUT", "300"))

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s", level=logging.INFO
)
logger = logging.getLogger(__name__)

# 사용자별 대화 저장
conversations = {}
user_locks = {}
user_reset_tokens = {}

MAX_HISTORY = 10


def get_user_lock(user_id):
    if user_id not in user_locks:
        user_locks[user_id] = asyncio.Lock()
    return user_locks[user_id]


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
    waiting_msg = await update.message.reply_text("AI가 답변을 생성 중입니다...")

    async with lock:
        if user_id not in conversations:
            conversations[user_id] = []
        if user_id not in user_reset_tokens:
            user_reset_tokens[user_id] = 0

        old_history = conversations[user_id][:]
        reset_token = user_reset_tokens[user_id]
        new_history = old_history + [f"User: {user_text}"]
        new_history = new_history[-MAX_HISTORY:]

    prompt = "\n".join(new_history) + "\nAI:"
    payload = {"prompt": prompt}

    try:
        async with httpx.AsyncClient() as client:
            r = await client.post(AI_GATEWAY, json=payload, timeout=TIMEOUT)
            r.raise_for_status()
            result = r.json()["response"]
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

    try:
        await waiting_msg.edit_text(result)
    except Exception as e:
        logger.error(f"Telegram message send failed: {e}")
        await update.message.reply_text("AI 응답 전송 중 오류가 발생했습니다.")
        return

    async with lock:
        if user_reset_tokens.get(user_id, 0) != reset_token:
            logger.info("Conversation reset detected during request; skipping stale history update.")
            return

        current_history = conversations.get(user_id, [])
        if current_history != old_history:
            logger.info("Conversation changed during request; appending completed turn to latest history.")
            updated_history = current_history + [f"User: {user_text}", f"AI: {result}"]
            conversations[user_id] = updated_history[-MAX_HISTORY:]
            return

        new_history.append(f"AI: {result}")
        conversations[user_id] = new_history[-MAX_HISTORY:]


def main():
    if not BOT_TOKEN:
        raise ValueError("BOT_TOKEN이 설정되지 않았습니다.")
    if not AI_GATEWAY:
        raise ValueError("AI_GATEWAY가 설정되지 않았습니다.")

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    app.add_handler(CommandHandler("reset", reset))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))

    app.run_polling()


if __name__ == "__main__":
    main()
