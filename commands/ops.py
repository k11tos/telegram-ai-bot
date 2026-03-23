import time
import uuid

import httpx
from telegram import Update
from telegram.ext import CommandHandler, ContextTypes

from brain_formatter import render_brain_payload
from gateway_client import GatewayClientError


async def reload_presets_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot as bot_module

    reload_result = await bot_module.load_gateway_presets(context.application)
    presets = bot_module.get_presets_from_bot_data(context.application.bot_data)

    if reload_result["loaded_from_gateway"] and not reload_result["used_fallback"]:
        preset_names = ", ".join(presets.keys())
        await update.message.reply_text(f"프리셋을 다시 불러왔습니다: {preset_names}")
        return

    await update.message.reply_text("게이트웨이 프리셋을 불러오지 못해 기본 프리셋으로 유지합니다.")


async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot as bot_module

    await update.message.reply_text(bot_module.HELP_MESSAGE)


async def status_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot as bot_module

    await update.message.reply_text(bot_module.build_status_message(context))


async def version_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot as bot_module

    await update.message.reply_text(bot_module.build_version_message())


async def health_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot as bot_module

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()
    bot_module.logger.info(
        f"health_check_start request_id={request_id} user_id={user_id} chat_id={chat_id}"
    )

    client = context.application.bot_data.get(bot_module.HTTP_CLIENT_KEY)
    if client is None:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        bot_module.logger.error(
            f"health_check_client_missing request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms}"
        )
        await update.message.reply_text("게이트웨이에 연결할 수 없어요. 잠시 후 다시 시도해주세요.")
        return

    try:
        response = await client.get(
            bot_module.AI_GATEWAY_READY_PATH,
            headers={"X-Request-Id": request_id},
        )
        response.raise_for_status()
    except (httpx.RequestError, httpx.HTTPStatusError) as error:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        bot_module.logger.warning(
            f"health_check_failed request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} error={error}"
        )
        await update.message.reply_text("게이트웨이 상태가 불안정하거나 사용할 수 없어요.")
        return

    latency_ms = int((time.monotonic() - request_start_ts) * 1000)
    bot_module.logger.info(
        f"health_check_success request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} latency_ms={latency_ms}"
    )
    await update.message.reply_text("게이트웨이가 정상적으로 준비되어 있어요.")


async def brain_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot as bot_module

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    request_id = uuid.uuid4().hex[:12]

    client = context.application.bot_data.get(bot_module.HTTP_CLIENT_KEY)
    if client is None:
        bot_module.logger.warning(
            "brain_gateway_client_missing request_id=%s user_id=%s chat_id=%s",
            request_id,
            user_id,
            chat_id,
        )
        await update.message.reply_text("gateway에 연결하지 못했습니다.")
        return

    try:
        brain_payload = await bot_module.post_agent_brain(
            client,
            payload={},
            request_id=request_id,
        )
    except GatewayClientError as error:
        if error.code == "agent_brain_timeout":
            bot_module.logger.warning(
                "brain_gateway_timeout request_id=%s user_id=%s chat_id=%s",
                request_id,
                user_id,
                chat_id,
            )
            fallback_message = "brain 응답이 지연되고 있습니다. 잠시 후 다시 시도해주세요."
        elif error.code == "agent_brain_connect_error":
            bot_module.logger.warning(
                "brain_gateway_connect_error request_id=%s user_id=%s chat_id=%s",
                request_id,
                user_id,
                chat_id,
            )
            fallback_message = "gateway에 연결하지 못했습니다."
        elif error.code in {"agent_brain_invalid_json", "agent_brain_malformed_response"}:
            bot_module.logger.warning(
                "brain_gateway_malformed_response request_id=%s user_id=%s chat_id=%s error=%s",
                request_id,
                user_id,
                chat_id,
                error.code,
            )
            fallback_message = "brain 응답 형식을 처리하지 못했습니다."
        else:
            bot_module.logger.warning(
                "brain_command_failed request_id=%s user_id=%s chat_id=%s error=%s",
                request_id,
                user_id,
                chat_id,
                error.code,
            )
            fallback_message = "gateway에 연결하지 못했습니다."

        await update.message.reply_text(fallback_message)
        return

    final_message = render_brain_payload(brain_payload)
    message_chunks = bot_module.split_telegram_text(final_message)
    await update.message.reply_text(message_chunks[0])
    for chunk in message_chunks[1:]:
        await update.message.reply_text(chunk)


async def models_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    import bot as bot_module

    user_id = update.effective_user.id
    chat_id = update.effective_chat.id if update.effective_chat else None
    request_id = uuid.uuid4().hex[:12]
    request_start_ts = time.monotonic()
    bot_module.logger.info(
        f"models_request_start request_id={request_id} user_id={user_id} chat_id={chat_id}"
    )

    client = context.application.bot_data.get(bot_module.HTTP_CLIENT_KEY)
    if client is None:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        bot_module.logger.error(
            f"models_http_client_missing request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms}"
        )
        await update.message.reply_text("죄송해요. 지금은 모델 목록을 가져올 수 없어요.")
        return

    try:
        response = await client.get(
            bot_module.AI_GATEWAY_MODELS_PATH,
            headers={"X-Request-Id": request_id},
        )
        response.raise_for_status()
        model_names = bot_module.extract_model_names(response.json())
    except (httpx.RequestError, httpx.HTTPStatusError, ValueError) as error:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        bot_module.logger.warning(
            f"models_request_failed request_id={request_id} user_id={user_id} "
            f"chat_id={chat_id} latency_ms={latency_ms} error={error}"
        )
        await update.message.reply_text("죄송해요. 모델 목록을 불러오지 못했어요. 잠시 후 다시 시도해주세요.")
        return

    if not model_names:
        latency_ms = int((time.monotonic() - request_start_ts) * 1000)
        bot_module.logger.info(
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
    bot_module.logger.info(
        f"models_request_success request_id={request_id} user_id={user_id} "
        f"chat_id={chat_id} latency_ms={latency_ms} model_count={len(model_names)}"
    )
    await update.message.reply_text(f"사용 가능한 모델 목록\n{listed_models}")


def register_operational_handlers(app):
    app.add_handler(CommandHandler("help", help_command))
    app.add_handler(CommandHandler("reload_presets", reload_presets_command))
    app.add_handler(CommandHandler("models", models_command))
    app.add_handler(CommandHandler("health", health_command))
    app.add_handler(CommandHandler("brain", brain_command))
    app.add_handler(CommandHandler("status", status_command))
    app.add_handler(CommandHandler("version", version_command))
