import asyncio
import json

import httpx

import bot


def test_reset_command_clears_conversation_and_replies(make_update_context):
    user_id = 42
    bot.ensure_user_sessions(user_id)[bot.DEFAULT_SESSION_NAME] = ["User: hi", "AI: hello"]
    bot.user_reset_tokens[user_id] = {bot.DEFAULT_SESSION_NAME: 7}

    update, context = make_update_context(user_id=user_id, text="/reset", client=None)

    asyncio.run(bot.reset(update, context))

    assert bot.get_session_history(user_id) == []
    assert bot.user_reset_tokens[user_id][bot.DEFAULT_SESSION_NAME] == 8
    assert update.message.replies == ["대화 기록을 초기화했습니다."]


def test_help_command_replies_with_supported_commands(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "사용 가능한 명령어" in reply
    assert "/help" in reply
    assert "/model" in reply
    assert "/preset" in reply
    assert "/reload_presets" in reply
    assert "/reset" in reply
    assert "/status" in reply
    assert "/version" in reply
    assert "/health" in reply
    assert "/session" in reply
    assert "/docmode" in reply


def test_build_version_message_includes_app_and_commit(monkeypatch):
    monkeypatch.setenv("APP_VERSION", "1.2.3")
    monkeypatch.setenv("GIT_COMMIT_SHA", "abcdef1234567890")

    assert bot.build_version_message() == "version: app=1.2.3 commit=abcdef1"


def test_build_version_message_uses_fallback_when_unset(monkeypatch):
    monkeypatch.delenv("APP_VERSION", raising=False)
    monkeypatch.delenv("VERSION", raising=False)
    monkeypatch.delenv("GIT_COMMIT_SHA", raising=False)
    monkeypatch.delenv("COMMIT_SHA", raising=False)
    monkeypatch.delenv("GITHUB_SHA", raising=False)

    assert bot.build_version_message() == "version: version info unavailable"


def test_build_version_message_sanitizes_env_values(monkeypatch):
    monkeypatch.setenv("VERSION", " release/v1.0.0 ")
    monkeypatch.setenv("GITHUB_SHA", "abc123456!@#")

    assert bot.build_version_message() == "version: app=releasev1.0.0 commit=abc1234"


def test_version_command_replies_with_version_summary(make_update_context, monkeypatch):
    monkeypatch.setenv("APP_VERSION", "2.0.0")
    monkeypatch.setenv("GIT_COMMIT_SHA", "1234567890abcdef")
    update, context = make_update_context(text="/version", client=None)

    asyncio.run(bot.version_command(update, context))

    assert update.message.replies[-1] == "version: app=2.0.0 commit=1234567"


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


def test_help_command_includes_ctx_command(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "/ctx" in reply


def test_help_command_includes_session_rename_command(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "/session_rename" in reply


def test_help_command_includes_session_clear_command(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "/session_clear" in reply


def test_docmode_command_shows_default_mode_when_unset(make_update_context):
    user_id = 900
    update, context = make_update_context(user_id=user_id, text="/docmode", client=None, args=[])

    asyncio.run(bot.docmode_command(update, context))

    assert update.message.replies[-1] == (
        "현재 문서 요약 모드: summary\n"
        f"사용 가능: {bot.DOCUMENT_SUMMARY_MODES_TEXT}"
    )


def test_docmode_command_switches_mode(make_update_context):
    user_id = 901
    update, context = make_update_context(
        user_id=user_id, text="/docmode bullets", client=None, args=["bullets"]
    )

    asyncio.run(bot.docmode_command(update, context))

    assert bot.user_document_summary_modes[user_id] == "bullets"
    assert update.message.replies[-1] == "문서 요약 모드가 변경되었습니다: bullets"


def test_docmode_command_handles_invalid_mode(make_update_context):
    user_id = 902
    update, context = make_update_context(
        user_id=user_id, text="/docmode unknown", client=None, args=["unknown"]
    )

    asyncio.run(bot.docmode_command(update, context))

    assert user_id not in bot.user_document_summary_modes
    assert update.message.replies[-1] == (
        "지원하지 않는 문서 요약 모드입니다. "
        f"사용 가능: {bot.DOCUMENT_SUMMARY_MODES_TEXT}"
    )


def test_health_command_reports_gateway_ready(make_update_context):
    client = FakeModelsClient(payload={"ok": True})
    update, context = make_update_context(text="/health", client=client)

    asyncio.run(bot.health_command(update, context))

    assert len(client.calls) == 1
    assert client.calls[0]["path"] == bot.AI_GATEWAY_READY_PATH
    assert "X-Request-Id" in client.calls[0]["headers"]
    assert isinstance(client.calls[0]["headers"]["X-Request-Id"], str)
    assert client.calls[0]["headers"]["X-Request-Id"]
    assert update.message.replies[-1] == "게이트웨이가 정상적으로 준비되어 있어요."


def test_health_command_treats_http_200_as_ready_without_body_inspection(make_update_context):
    client = FakeModelsClient(json_error=ValueError("invalid payload"))
    update, context = make_update_context(text="/health", client=client)

    asyncio.run(bot.health_command(update, context))

    assert update.message.replies[-1] == "게이트웨이가 정상적으로 준비되어 있어요."


def test_health_command_handles_gateway_failure(make_update_context):
    request = httpx.Request("GET", "http://test/health/ready")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/health", client=client)

    asyncio.run(bot.health_command(update, context))

    assert update.message.replies[-1] == "게이트웨이 상태가 불안정하거나 사용할 수 없어요."


def test_health_command_handles_gateway_status_error(make_update_context):
    request = httpx.Request("GET", "http://test/health/ready")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError("service unavailable", request=request, response=response)
    client = FakeModelsClient(status_error=status_error)
    update, context = make_update_context(text="/health", client=client)

    asyncio.run(bot.health_command(update, context))

    assert update.message.replies[-1] == "게이트웨이 상태가 불안정하거나 사용할 수 없어요."


def test_health_command_handles_missing_client(make_update_context):
    update, context = make_update_context(text="/health", client=None)

    asyncio.run(bot.health_command(update, context))

    assert update.message.replies[-1] == "게이트웨이에 연결할 수 없어요. 잠시 후 다시 시도해주세요."


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
    assert isinstance(client.calls[0]["headers"]["X-Request-Id"], str)
    assert client.calls[0]["headers"]["X-Request-Id"]
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


def test_model_command_resets_selected_model_with_default_alias(make_update_context):
    user_id = 90
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    update, context = make_update_context(user_id=user_id, text="/model default", client=None, args=["default"])

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert update.message.replies[-1] == "모델 설정을 초기화했습니다. 기본 모델을 사용합니다."


def test_model_command_resets_selected_model_with_reset_alias(make_update_context):
    user_id = 91
    bot.user_selected_models[user_id] = "claude-3-5"
    update, context = make_update_context(user_id=user_id, text="/model reset", client=None, args=["reset"])

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert update.message.replies[-1] == "모델 설정을 초기화했습니다. 기본 모델을 사용합니다."


def test_model_command_resets_selected_model_with_mixed_case_alias(make_update_context):
    user_id = 92
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    update, context = make_update_context(user_id=user_id, text="/model DEFAULT", client=None, args=["DEFAULT"])

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert update.message.replies[-1] == "모델 설정을 초기화했습니다. 기본 모델을 사용합니다."


def test_preset_command_shows_default_when_unset(make_update_context):
    update, context = make_update_context(text="/preset", client=None)

    asyncio.run(bot.preset_command(update, context))

    static_presets = bot.get_static_presets()
    assert update.message.replies[-1] == (
        f"현재 프리셋: normal\n"
        f"사용 가능: {', '.join(static_presets.keys())}\n"
        "설명:\n"
        f"✅ normal: {static_presets['normal']['description']}\n"
        f"• coder: {static_presets['coder']['description']}\n"
        f"• english: {static_presets['english']['description']}\n"
        f"• quant: {static_presets['quant']['description']}"
    )


def test_preset_command_sets_supported_preset(make_update_context):
    user_id = 96
    update, context = make_update_context(
        user_id=user_id,
        text="/preset english",
        client=None,
        args=["english"],
    )

    asyncio.run(bot.preset_command(update, context))

    assert bot.user_selected_presets[user_id] == "english"
    assert update.message.replies[-1] == "프리셋이 변경되었습니다: english"


def test_preset_command_sets_supported_preset_with_case_normalization(make_update_context):
    user_id = 97
    update, context = make_update_context(
        user_id=user_id,
        text="/preset Coder",
        client=None,
        args=["Coder"],
    )

    asyncio.run(bot.preset_command(update, context))

    assert bot.user_selected_presets[user_id] == "coder"
    assert update.message.replies[-1] == "프리셋이 변경되었습니다: coder"


def test_preset_command_rejects_unsupported_preset(make_update_context):
    user_id = 98
    update, context = make_update_context(
        user_id=user_id,
        text="/preset unknown",
        client=None,
        args=["unknown"],
    )

    asyncio.run(bot.preset_command(update, context))

    assert bot.user_selected_presets.get(user_id) is None
    assert update.message.replies[-1] == (
        "지원하지 않는 프리셋입니다. 사용 가능: " + ", ".join(bot.get_static_presets().keys())
    )


def test_preset_command_shows_selected_preset(make_update_context):
    user_id = 93
    bot.user_selected_presets[user_id] = "coder"
    update, context = make_update_context(user_id=user_id, text="/preset", client=None)

    asyncio.run(bot.preset_command(update, context))

    static_presets = bot.get_static_presets()
    assert update.message.replies[-1] == (
        f"현재 프리셋: coder\n"
        f"사용 가능: {', '.join(static_presets.keys())}\n"
        "설명:\n"
        f"• normal: {static_presets['normal']['description']}\n"
        f"✅ coder: {static_presets['coder']['description']}\n"
        f"• english: {static_presets['english']['description']}\n"
        f"• quant: {static_presets['quant']['description']}"
    )


def test_preset_command_falls_back_to_default_for_invalid_value(make_update_context):
    user_id = 94
    bot.user_selected_presets[user_id] = " invalid "
    update, context = make_update_context(user_id=user_id, text="/preset", client=None)

    asyncio.run(bot.preset_command(update, context))

    assert update.message.replies[-1].startswith("현재 프리셋: normal\n")


def test_preset_command_normalizes_selected_preset_value(make_update_context):
    user_id = 95
    bot.user_selected_presets[user_id] = " Coder "
    update, context = make_update_context(user_id=user_id, text="/preset", client=None)

    asyncio.run(bot.preset_command(update, context))

    assert update.message.replies[-1].startswith("현재 프리셋: coder\n")


def test_preset_command_shows_gateway_loaded_descriptions(make_update_context):
    bot_data = {
        bot.PRESETS_KEY: {
            "normal": {
                "description": "게이트웨이 기본",
                "prompt_prefix": "should not be shown",
            },
            "research": {
                "description": "자료 조사와 검증 중심",
                "prompt_prefix": "should not be shown",
            },
        }
    }
    update, context = make_update_context(text="/preset", client=None)
    context.application.bot_data = bot_data

    asyncio.run(bot.preset_command(update, context))

    assert update.message.replies[-1] == (
        "현재 프리셋: normal\n"
        "사용 가능: normal, research\n"
        "설명:\n"
        "✅ normal: 게이트웨이 기본\n"
        "• research: 자료 조사와 검증 중심"
    )
    assert "should not be shown" not in update.message.replies[-1]


def test_ctx_command_shows_default_state(make_update_context):
    update, context = make_update_context(text="/ctx", client=None)

    asyncio.run(bot.ctx_command(update, context))

    assert update.message.replies[-1] == (
        "현재 컨텍스트\n"
        "- 세션: default\n"
        "- 모델: 기본 모델 사용\n"
        "- 프리셋: normal\n"
        "- 기록 줄 수: 0\n"
        "- 요청 처리 중: 없음"
    )


def test_ctx_command_shows_custom_selected_model(make_update_context):
    user_id = 501
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    update, context = make_update_context(user_id=user_id, text="/ctx", client=None)

    asyncio.run(bot.ctx_command(update, context))

    assert "- 모델: gpt-4o-mini" in update.message.replies[-1]


def test_ctx_command_shows_custom_selected_preset(make_update_context):
    user_id = 502
    bot.user_selected_presets[user_id] = "coder"
    update, context = make_update_context(user_id=user_id, text="/ctx", client=None)

    asyncio.run(bot.ctx_command(update, context))

    assert "- 프리셋: coder" in update.message.replies[-1]


def test_ctx_command_shows_non_default_active_session(make_update_context):
    user_id = 503
    bot.user_active_sessions[user_id] = "work"
    bot.ensure_user_sessions(user_id)["work"] = ["User: hi", "AI: hello", "User: next"]
    update, context = make_update_context(user_id=user_id, text="/ctx", client=None)

    asyncio.run(bot.ctx_command(update, context))

    assert "- 세션: work" in update.message.replies[-1]
    assert "- 기록 줄 수: 3" in update.message.replies[-1]


def test_ctx_command_shows_inflight_false(make_update_context):
    user_id = 504
    bot.user_in_flight_requests[user_id] = False
    update, context = make_update_context(user_id=user_id, text="/ctx", client=None)

    asyncio.run(bot.ctx_command(update, context))

    assert "- 요청 처리 중: 없음" in update.message.replies[-1]


def test_ctx_command_shows_inflight_true(make_update_context):
    user_id = 505
    bot.user_in_flight_requests[user_id] = True
    update, context = make_update_context(user_id=user_id, text="/ctx", client=None)

    asyncio.run(bot.ctx_command(update, context))

    assert "- 요청 처리 중: 있음" in update.message.replies[-1]


def test_reload_presets_command_updates_presets_from_gateway(make_update_context):
    client = FakeModelsClient(
        payload={
            "presets": [
                {"name": "normal", "description": "Balanced assistant for general use.", "prompt_prefix": ""},
                {"name": "coder", "description": "Focused on programming and debugging tasks.", "prompt_prefix": "You are a practical coding assistant. Be precise and production-minded.\n\n"},
                {"name": "english", "description": "Helps improve English writing and grammar.", "prompt_prefix": "You are an English writing helper. Improve clarity, grammar, and tone.\n\n"},
                {"name": "quant", "description": "Supports quantitative and analytical reasoning.", "prompt_prefix": "You are a quantitative reasoning assistant. Show concise, correct math.\n\n"},
            ]
        }
    )
    update, context = make_update_context(text="/reload_presets", client=client)

    asyncio.run(bot.reload_presets_command(update, context))

    assert update.message.replies[-1] == "프리셋을 다시 불러왔습니다: normal, coder, english, quant"


def test_reload_presets_command_falls_back_safely_on_gateway_failure(make_update_context):
    request = httpx.Request("GET", "http://test/presets")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/reload_presets", client=client)

    asyncio.run(bot.reload_presets_command(update, context))

    assert context.application.bot_data[bot.PRESETS_KEY] == bot.get_static_presets()
    assert update.message.replies[-1] == "게이트웨이 프리셋을 불러오지 못해 기본 프리셋으로 유지합니다."


def test_preset_command_uses_refreshed_values_after_reload(make_update_context):
    client = FakeModelsClient(
        payload={
            "presets": [
                {"name": "research", "description": "Research", "prompt_prefix": "Preset: research.\n\n"}
            ]
        }
    )
    reload_update, reload_context = make_update_context(text="/reload_presets", client=client)

    asyncio.run(bot.reload_presets_command(reload_update, reload_context))

    preset_update, preset_context = make_update_context(
        text="/preset research",
        client=client,
        args=["research"],
    )
    preset_context.application = reload_context.application

    asyncio.run(bot.preset_command(preset_update, preset_context))

    assert preset_update.message.replies[-1] == "프리셋이 변경되었습니다: research"

def test_session_command_no_arg_single_session(make_update_context):
    update, context = make_update_context(text="/session", client=None)

    asyncio.run(bot.session_command(update, context))

    assert update.message.replies[-1] == (
        "현재 세션: default\n"
        "전체 세션 수: 1\n\n"
        "보유한 세션:\n"
        "- default"
    )


def test_session_command_no_arg_multiple_sessions(make_update_context):
    user_id = 320
    bot.ensure_user_sessions(user_id)["trading"] = ["User: market"]
    bot.ensure_user_sessions(user_id)["coding"] = ["User: python"]
    bot.user_active_sessions[user_id] = "trading"
    update, context = make_update_context(user_id=user_id, text="/session", client=None)

    asyncio.run(bot.session_command(update, context))

    assert update.message.replies[-1] == (
        "현재 세션: trading\n"
        "전체 세션 수: 2\n\n"
        "보유한 세션:\n"
        "- coding\n"
        "- trading"
    )


def test_session_command_switches_session(make_update_context):
    user_id = 321
    update, context = make_update_context(user_id=user_id, text="/session work", client=None, args=["work"])

    asyncio.run(bot.session_command(update, context))

    assert bot.user_active_sessions[user_id] == "work"
    assert bot.get_session_history(user_id, "work") == []
    assert update.message.replies[-1] == "세션 변경: work"


def test_session_command_switches_to_trimmed_name(make_update_context):
    user_id = 322
    long_name = "x" * 50
    update, context = make_update_context(user_id=user_id, text=f"/session {long_name}", client=None, args=[long_name])

    asyncio.run(bot.session_command(update, context))

    assert bot.user_active_sessions[user_id] == "x" * 32
    assert update.message.replies[-1] == f"세션 변경: {'x' * 32}"



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
    def __init__(
        self,
        payload=None,
        get_error=None,
        status_error=None,
        json_error=None,
    ):
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
    assert isinstance(client.calls[0]["headers"]["X-Request-Id"], str)
    assert client.calls[0]["headers"]["X-Request-Id"]
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


def test_main_registers_health_command_handler(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.handlers = []
            self.run_polling_called = False

        def add_handler(self, handler):
            self.handlers.append(handler)

        def run_polling(self):
            self.run_polling_called = True

    class FakeBuilder:
        def __init__(self):
            self.app = FakeApp()

        def token(self, value):
            self.token_value = value
            return self

        def post_init(self, callback):
            self.post_init_callback = callback
            return self

        def post_shutdown(self, callback):
            self.post_shutdown_callback = callback
            return self

        def build(self):
            return self.app

    fake_builder = FakeBuilder()

    monkeypatch.setattr(bot, "BOT_TOKEN", "dummy-token")
    monkeypatch.setattr(bot, "AI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: fake_builder)

    bot.main()

    health_handlers = [
        handler
        for handler in fake_builder.app.handlers
        if "health" in getattr(handler, "commands", set())
    ]
    assert len(health_handlers) == 1
    assert health_handlers[0].callback == bot.health_command
    assert fake_builder.app.run_polling_called is True


def test_main_registers_ctx_command_handler(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.handlers = []
            self.run_polling_called = False

        def add_handler(self, handler):
            self.handlers.append(handler)

        def run_polling(self):
            self.run_polling_called = True

    class FakeBuilder:
        def __init__(self):
            self.app = FakeApp()

        def token(self, value):
            self.token_value = value
            return self

        def post_init(self, callback):
            self.post_init_callback = callback
            return self

        def post_shutdown(self, callback):
            self.post_shutdown_callback = callback
            return self

        def build(self):
            return self.app

    fake_builder = FakeBuilder()

    monkeypatch.setattr(bot, "BOT_TOKEN", "dummy-token")
    monkeypatch.setattr(bot, "AI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: fake_builder)

    bot.main()

    ctx_handlers = [
        handler
        for handler in fake_builder.app.handlers
        if "ctx" in getattr(handler, "commands", set())
    ]
    assert len(ctx_handlers) == 1
    assert ctx_handlers[0].callback == bot.ctx_command
    assert fake_builder.app.run_polling_called is True


def test_save_bot_state_writes_json_file(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_path = state_dir / "bot_state.json"
    monkeypatch.setattr(bot, "LOCAL_DATA_DIR", str(state_dir))
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.ensure_user_sessions(10)[bot.DEFAULT_SESSION_NAME] = ["User: hi", "AI: hello"]
    bot.user_selected_models[10] = "gpt-4o-mini"
    bot.user_selected_presets[10] = "coder"
    bot.user_document_summary_modes[10] = "action"

    bot.save_bot_state()

    assert state_path.exists()
    payload = state_path.read_text(encoding="utf-8")
    assert '"version":1' in payload
    assert '"conversations":{"10":{"default":["User: hi","AI: hello"]}}' in payload
    assert '"selected_models":{"10":"gpt-4o-mini"}' in payload
    assert '"selected_presets":{"10":"coder"}' in payload
    assert '"document_summary_modes":{"10":"action"}' in payload


def test_load_bot_state_restores_saved_values(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    state_path.write_text(
        '{"version":1,"conversations":{"123":{"default":["User: a","AI: b"]}},"active_sessions":{"123":"default"},'
        '"selected_models":{"123":"gpt-4o-mini"},"selected_presets":{"123":"ENGLISH"},'
        '"document_summary_modes":{"123":"BULLETS"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "LOCAL_DATA_DIR", str(state_dir))
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.load_bot_state()

    assert bot.get_session_history(123) == ["User: a", "AI: b"]
    assert bot.user_selected_models[123] == "gpt-4o-mini"
    assert bot.user_selected_presets[123] == "english"
    assert bot.user_document_summary_modes[123] == "bullets"


def test_load_bot_state_normalizes_invalid_document_mode_to_default(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    state_path.write_text(
        '{"document_summary_modes":{"7":"UNKNOWN_MODE"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.load_bot_state()

    assert bot.user_document_summary_modes[7] == "summary"
    assert bot.get_user_document_summary_mode(7) == "summary"


def test_load_bot_state_ignores_malformed_json(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    state_path.write_text("{bad json", encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.load_bot_state()

    assert bot.conversations == {}
    assert bot.user_selected_models == {}
    assert bot.user_selected_presets == {}


def test_main_loads_state_before_running(monkeypatch):
    class FakeApp:
        def __init__(self):
            self.handlers = []

        def add_handler(self, handler):
            self.handlers.append(handler)

        def run_polling(self):
            return None

    class FakeBuilder:
        def __init__(self):
            self.app = FakeApp()

        def token(self, value):
            return self

        def post_init(self, callback):
            return self

        def post_shutdown(self, callback):
            return self

        def build(self):
            return self.app

    called = {"load": False}

    def fake_load():
        called["load"] = True

    monkeypatch.setattr(bot, "BOT_TOKEN", "dummy-token")
    monkeypatch.setattr(bot, "AI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setattr(bot, "ApplicationBuilder", lambda: FakeBuilder())
    monkeypatch.setattr(bot, "load_bot_state", fake_load)

    bot.main()

    assert called["load"] is True


def test_load_bot_state_replaces_existing_state_instead_of_merging(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    state_path.write_text(
        '{"version":1,"conversations":{"2":["User: new","AI: value"]},'
        '"selected_models":{"2":" new-model "},"selected_presets":{"2":"english"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.ensure_user_sessions(1)[bot.DEFAULT_SESSION_NAME] = ["User: stale", "AI: stale"]
    bot.user_selected_models[1] = "stale-model"
    bot.user_selected_presets[1] = "coder"

    bot.load_bot_state()

    assert bot.conversations == {2: {bot.DEFAULT_SESSION_NAME: ["User: new", "AI: value"]}}
    assert bot.user_selected_models == {2: "new-model"}
    assert bot.user_selected_presets == {2: "english"}


def test_load_bot_state_invalid_root_replaces_with_empty_state(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    state_path.write_text('["not-a-dict"]', encoding="utf-8")
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.ensure_user_sessions(1)[bot.DEFAULT_SESSION_NAME] = ["User: stale"]
    bot.user_selected_models[1] = "stale"
    bot.user_selected_presets[1] = "coder"

    bot.load_bot_state()

    assert bot.conversations == {}
    assert bot.user_selected_models == {}
    assert bot.user_selected_presets == {}


def test_load_bot_state_trims_and_filters_history_entries(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    valid_lines = [f"line-{index}" for index in range(bot.MAX_HISTORY + 2)]
    mixed_history = [valid_lines[0], None, 1, valid_lines[1], *valid_lines[2:]]
    state_path.write_text(
        json.dumps({"conversations": {"3": mixed_history}}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.load_bot_state()

    assert bot.get_session_history(3) == valid_lines[-bot.MAX_HISTORY :]


def test_load_bot_state_normalizes_presets_and_strips_model_values(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    state_path.write_text(
        '{"selected_models":{"1":"  gpt-4o-mini  ","2":"   "},'
        '"selected_presets":{"1":"NOT_SUPPORTED","2":" Coder "}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.load_bot_state()

    assert bot.user_selected_models == {1: "gpt-4o-mini"}
    assert bot.user_selected_presets == {1: "not_supported", 2: "coder"}


def test_load_bot_state_is_deterministic_across_repeated_calls(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    state_path.write_text('{"conversations":{"1":["User: a"]}}', encoding="utf-8")
    bot.load_bot_state()
    assert bot.conversations == {1: {bot.DEFAULT_SESSION_NAME: ["User: a"]}}

    state_path.write_text('{"conversations":{"2":["User: b"]}}', encoding="utf-8")
    bot.load_bot_state()
    assert bot.conversations == {2: {bot.DEFAULT_SESSION_NAME: ["User: b"]}}

    bot.load_bot_state()
    assert bot.conversations == {2: {bot.DEFAULT_SESSION_NAME: ["User: b"]}}


def test_load_bot_state_missing_file_clears_persisted_state(tmp_path, monkeypatch):
    missing_path = tmp_path / "state" / "bot_state.json"
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(missing_path))

    bot.ensure_user_sessions(1)[bot.DEFAULT_SESSION_NAME] = ["User: stale"]
    bot.user_selected_models[1] = "stale"
    bot.user_selected_presets[1] = "coder"

    bot.load_bot_state()

    assert bot.conversations == {}
    assert bot.user_selected_models == {}
    assert bot.user_selected_presets == {}

class FakeObsidianClient:
    def __init__(self, post_payload=None, get_payload=None, post_error=None, get_error=None, status_error=None):
        self.post_payload = post_payload if post_payload is not None else {"job_id": "job-123"}
        self.get_payload = get_payload if get_payload is not None else {
            "queue": {"pending": 1, "running": 0, "completed": 2, "failed": 0},
            "worker": {"status": "online"},
        }
        self.post_error = post_error
        self.get_error = get_error
        self.status_error = status_error
        self.calls = []

    async def post(self, path, json=None, headers=None):
        self.calls.append({"method": "POST", "path": path, "json": json, "headers": headers})
        if self.post_error is not None:
            raise self.post_error
        return FakeGetResponse(payload=self.post_payload, status_error=self.status_error)

    async def get(self, path, headers=None):
        self.calls.append({"method": "GET", "path": path, "headers": headers})
        if self.get_error is not None:
            raise self.get_error
        return FakeGetResponse(payload=self.get_payload, status_error=self.status_error)


def test_wiki_allowed_user_can_create_job(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_TELEGRAM_INTERNAL_TOKEN", "secret-token")
    client = FakeObsidianClient(post_payload={"job_id": "wiki-1"})
    update, context = make_update_context(
        user_id=123,
        chat_id=456,
        text="/wiki ask hello?",
        client=client,
        args=["ask", "hello?"],
    )
    update.message.message_id = 789

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == [
        {
            "method": "POST",
            "path": bot.OBSIDIAN_JOBS_PATH,
            "json": {
                "command": "ask",
                "payload": {"question": "hello?"},
                "telegram_chat_id": 456,
                "telegram_message_id": 789,
                "requested_by": 123,
            },
            "headers": {
                "X-Request-Id": client.calls[0]["headers"]["X-Request-Id"],
                "Authorization": "Bearer secret-token",
            },
        }
    ]
    assert update.message.replies[-1] == "위키 ask 작업을 접수했어요. job_id=wiki-1"


def test_wiki_disallowed_user_is_rejected(make_update_context, monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "999")
    client = FakeObsidianClient()
    update, context = make_update_context(user_id=123, text="/wiki ingest", client=client, args=["ingest"])

    with caplog.at_level("WARNING"):
        asyncio.run(bot.wiki_command(update, context))

    assert client.calls == []
    assert update.message.replies[-1] == bot.build_wiki_denied_message(123)
    assert "wiki_permission_denied" in caplog.text
    assert "user_id=123" in caplog.text
    assert "chat_id=456" in caplog.text
    assert "allowlist_empty=False" in caplog.text


def test_wiki_missing_subcommand_shows_help(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    update, context = make_update_context(text="/wiki", client=FakeObsidianClient(), args=[])

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == bot.WIKI_HELP_MESSAGE


def test_wiki_ask_requires_question(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    update, context = make_update_context(text="/wiki ask", client=FakeObsidianClient(), args=["ask"])

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == bot.WIKI_HELP_MESSAGE


def test_wiki_capture_requires_text(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    update, context = make_update_context(text="/wiki capture", client=FakeObsidianClient(), args=["capture"])

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == bot.WIKI_HELP_MESSAGE


def test_wiki_ingest_creates_ingest_job(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"id": 55})
    update, context = make_update_context(text="/wiki ingest", client=client, args=["ingest"])

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["json"]["command"] == "ingest"
    assert client.calls[0]["json"]["payload"] == {}
    assert update.message.replies[-1] == "위키 ingest 작업을 접수했어요. job_id=55"


def test_wiki_gateway_failure_returns_friendly_error(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    request = httpx.Request("POST", "http://test/obsidian/jobs")
    client = FakeObsidianClient(post_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/wiki ingest", client=client, args=["ingest"])

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == "위키 작업을 접수하지 못했어요. 잠시 후 다시 시도해주세요."


def test_wiki_status_calls_gateway_with_auth_and_renders_queue_counts(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_TELEGRAM_INTERNAL_TOKEN", "status-token")
    client = FakeObsidianClient(
        get_payload={
            "queue_counts": {
                "queued": 2,
                "running": 1,
                "succeeded": 7,
                "failed": 1,
                "expired": 0,
            },
            "last_finished_job": {
                "job_id": "job-done",
                "command": "ask",
                "status": "succeeded",
                "finished_at": "2026-06-13T00:00:00Z",
            },
        }
    )
    update, context = make_update_context(text="/wiki status", client=client, args=["status"])

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == [
        {
            "method": "GET",
            "path": bot.OBSIDIAN_STATUS_PATH,
            "headers": {
                "X-Request-Id": client.calls[0]["headers"]["X-Request-Id"],
                "Authorization": "Bearer status-token",
            },
        }
    ]
    reply = update.message.replies[-1]
    assert "- queued: 2" in reply
    assert "- running: 1" in reply
    assert "- succeeded: 7" in reply
    assert "- failed: 1" in reply
    assert "- expired: 0" in reply
    assert "gateway에서 worker 상태를 별도로 보고하지 않음" in reply
    assert "last_finished_job: job_id=job-done, command=ask, status=succeeded" in reply


def test_wiki_status_gateway_failure_returns_friendly_error(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    request = httpx.Request("GET", "http://test/obsidian/status")
    client = FakeObsidianClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/wiki status", client=client, args=["status"])

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == "위키 상태를 불러오지 못했어요. 잠시 후 다시 시도해주세요."


def test_wiki_status_disallowed_user_does_not_call_gateway(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "999")
    client = FakeObsidianClient()
    update, context = make_update_context(user_id=123, text="/wiki status", client=client, args=["status"])

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == []
    assert update.message.replies[-1] == bot.build_wiki_denied_message(123)
