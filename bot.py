import os

import requests
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

MAX_HISTORY = 10


async def reset(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    conversations[user_id] = []
    await update.message.reply_text("대화 기록을 초기화했습니다.")


async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    user_text = update.message.text

    if user_id not in conversations:
        conversations[user_id] = []

    history = conversations[user_id]

    history.append(f"User: {user_text}")
    history = history[-MAX_HISTORY:]

    prompt = "\n".join(history) + "\nAI:"

    payload = {"prompt": prompt}

    r = requests.post(AI_GATEWAY, json=payload, timeout=120)
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
