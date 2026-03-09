import asyncio
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

# 사용자별 대화 저장
conversations = {}
user_locks = {}

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
    await update.message.reply_text("대화 기록을 초기화했습니다.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    lock = get_user_lock(user_id)

    if lock.locked():
        await update.message.reply_text("응답 생성 중입니다. 잠시만 기다려주세요.")
        return

    async with lock:
        if user_id not in conversations:
            conversations[user_id] = []

        history = conversations[user_id]

        history.append(f"User: {user_text}")
        history = history[-MAX_HISTORY:]

        prompt = "\n".join(history) + "\nAI:"

        payload = {"prompt": prompt}

        async with httpx.AsyncClient() as client:
            r = await client.post(AI_GATEWAY, json=payload, timeout=120.0)
            r.raise_for_status()
            result = r.json()["response"]

        history.append(f"AI: {result}")
        conversations[user_id] = history[-MAX_HISTORY:]

    await update.message.reply_text(result)


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
