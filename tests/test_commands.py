import asyncio

import httpx

import bot


def test_reset_command_clears_conversation_and_replies(make_update_context):
    user_id = 42
    bot.conversations[user_id] = ["User: hi", "AI: hello"]
    bot.user_reset_tokens[user_id] = 7

    update, context = make_update_context(user_id=user_id, text="/reset", client=None)

    asyncio.run(bot.reset(update, context))

    assert bot.conversations[user_id] == []
    assert bot.user_reset_tokens[user_id] == 8
    assert update.message.replies == ["대화 기록을 초기화했습니다."]


def test_help_command_replies_with_supported_commands(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "사용 가능한 명령어" in reply
    assert "/help" in reply
    assert "/model" in reply
    assert "/reset" in reply
    assert "/status" in reply


def test_status_command_shows_korean_summary_without_secrets(make_update_context):
    update, context = make_update_context(text="/status", client=object())

    asyncio.run(bot.status_command(update, context))

    reply = update.message.replies[0]
    assert "봇 상태 요약" in reply
    assert "서비스 상태: 실행 중" in reply
    expected_gateway = bot.AI_GATEWAY_BASE_URL or "미설정"
    assert f"AI 게이트웨이: {expected_gateway}" in reply
    assert (
        "HTTP 타임아웃(초): "
        f"connect={bot.HTTP_TIMEOUT_CONFIG['connect']}, "
        f"read={bot.HTTP_TIMEOUT_CONFIG['read']}, "
        f"write={bot.HTTP_TIMEOUT_CONFIG['write']}, "
        f"pool={bot.HTTP_TIMEOUT_CONFIG['pool']}"
    ) in reply
    assert "HTTP 클라이언트: 초기화됨" in reply
    assert "BOT_TOKEN" not in reply


def test_status_command_marks_missing_http_client(make_update_context):
    update, context = make_update_context(text="/status", client=None)

    asyncio.run(bot.status_command(update, context))

    reply = update.message.replies[0]
    assert "HTTP 클라이언트: 미초기화" in reply
    assert "아직 준비되지 않았습니다" in reply


def test_help_command_includes_models_command(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "/models" in reply


def test_model_command_shows_selected_model(make_update_context):
    user_id = 52
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    update, context = make_update_context(user_id=user_id, text="/model", client=None)

    asyncio.run(bot.model_command(update, context))

    assert update.message.replies[-1] == "현재 모델: gpt-4o-mini"


def test_model_command_shows_default_behavior_when_unset(make_update_context):
    update, context = make_update_context(text="/model", client=None)

    asyncio.run(bot.model_command(update, context))

    assert update.message.replies[-1] == "현재 모델: 기본 모델 사용"


def test_model_command_sets_selected_model_when_valid(make_update_context):
    user_id = 88
    client = FakeModelsClient(payload={"models": [{"id": "gpt-4o-mini"}, {"id": "claude-3-5"}]})
    update, context = make_update_context(
        user_id=user_id,
        text="/model gpt-4o-mini",
        client=client,
        args=["gpt-4o-mini"],
    )

    asyncio.run(bot.model_command(update, context))

    assert len(client.calls) == 1
    assert client.calls[0]["path"] == bot.AI_GATEWAY_MODELS_PATH
    assert "X-Request-Id" in client.calls[0]["headers"]
    assert bot.user_selected_models[user_id] == "gpt-4o-mini"
    assert update.message.replies[-1] == "모델이 변경되었습니다: gpt-4o-mini"


def test_model_command_rejects_invalid_model_name(make_update_context):
    user_id = 89
    client = FakeModelsClient(payload={"models": [{"id": "gpt-4o-mini"}]})
    update, context = make_update_context(
        user_id=user_id,
        text="/model bad-model",
        client=client,
        args=["bad-model"],
    )

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert update.message.replies[-1] == "사용할 수 없는 모델이에요."


def test_model_command_handles_missing_client_when_setting(make_update_context):
    update, context = make_update_context(text="/model gpt-4o-mini", client=None, args=["gpt-4o-mini"])

    asyncio.run(bot.model_command(update, context))

    assert update.message.replies[-1] == "지금은 모델을 변경할 수 없어요."




def test_model_command_resets_selection_with_default_keyword(make_update_context):
    user_id = 90
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    update, context = make_update_context(
        user_id=user_id,
        text="/model default",
        client=None,
        args=["default"],
    )

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert update.message.replies[-1] == "모델을 기본값으로 되돌렸어요."


def test_model_command_resets_selection_with_reset_keyword_case_insensitive(make_update_context):
    user_id = 91
    bot.user_selected_models[user_id] = "claude-3-5"
    update, context = make_update_context(
        user_id=user_id,
        text="/model RESET",
        client=None,
        args=["RESET"],
    )

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert update.message.replies[-1] == "모델을 기본값으로 되돌렸어요."
def test_model_command_handles_gateway_request_error(make_update_context):
    request = httpx.Request("GET", "http://test/models")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(
        text="/model gpt-4o-mini",
        client=client,
        args=["gpt-4o-mini"],
    )

    asyncio.run(bot.model_command(update, context))

    assert update.message.replies[-1] == "모델 확인에 실패했어요. 잠시 후 다시 시도해주세요."


def test_model_command_handles_gateway_status_error(make_update_context):
    request = httpx.Request("GET", "http://test/models")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    client = FakeModelsClient(status_error=status_error)
    update, context = make_update_context(
        text="/model gpt-4o-mini",
        client=client,
        args=["gpt-4o-mini"],
    )

    asyncio.run(bot.model_command(update, context))

    assert update.message.replies[-1] == "모델 확인에 실패했어요. 잠시 후 다시 시도해주세요."


class FakeGetResponse:
    def __init__(self, payload=None, status_error=None, json_error=None):
        self._payload = payload
        self._status_error = status_error
        self._json_error = json_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeModelsClient:
    def __init__(self, payload=None, get_error=None, status_error=None, json_error=None):
        self.payload = payload
        self.get_error = get_error
        self.status_error = status_error
        self.json_error = json_error
        self.calls = []

    async def get(self, path, headers=None):
        self.calls.append({"path": path, "headers": headers})
        if self.get_error is not None:
            raise self.get_error
        return FakeGetResponse(
            payload=self.payload,
            status_error=self.status_error,
            json_error=self.json_error,
        )


def test_models_command_fetches_gateway_models(make_update_context):
    client = FakeModelsClient(payload={"models": [{"id": "gpt-4o-mini"}, {"id": "claude-3-5"}]})
    update, context = make_update_context(text="/models", client=client)

    asyncio.run(bot.models_command(update, context))

    assert len(client.calls) == 1
    assert client.calls[0]["path"] == bot.AI_GATEWAY_MODELS_PATH
    assert "X-Request-Id" in client.calls[0]["headers"]
    assert update.message.replies[-1] == "사용 가능한 모델 목록\n- gpt-4o-mini\n- claude-3-5"


def test_models_command_handles_gateway_failure(make_update_context):
    request = httpx.Request("GET", "http://test/models")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/models", client=client)

    asyncio.run(bot.models_command(update, context))

    assert update.message.replies[-1] == "죄송해요. 모델 목록을 불러오지 못했어요. 잠시 후 다시 시도해주세요."




def test_models_command_handles_gateway_status_error(make_update_context):
    request = httpx.Request("GET", "http://test/models")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    client = FakeModelsClient(status_error=status_error)
    update, context = make_update_context(text="/models", client=client)

    asyncio.run(bot.models_command(update, context))

    assert update.message.replies[-1] == "죄송해요. 모델 목록을 불러오지 못했어요. 잠시 후 다시 시도해주세요."


def test_models_command_handles_missing_client(make_update_context):
    update, context = make_update_context(text="/models", client=None)

    asyncio.run(bot.models_command(update, context))

    assert update.message.replies[-1] == "죄송해요. 지금은 모델 목록을 가져올 수 없어요."
