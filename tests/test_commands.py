import asyncio
import json

import httpx

import bot


def test_reset_command_clears_conversation_and_replies(make_update_context):
    user_id = 42
    bot.ensure_user_sessions(user_id)[bot.DEFAULT_SESSION_NAME] = [
        "User: hi",
        "AI: hello",
    ]
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
    update, context = make_update_context(
        user_id=user_id, text="/docmode", client=None, args=[]
    )

    asyncio.run(bot.docmode_command(update, context))

    assert update.message.replies[-1] == (
        f"현재 문서 요약 모드: summary\n사용 가능: {bot.DOCUMENT_SUMMARY_MODES_TEXT}"
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


def test_health_command_treats_http_200_as_ready_without_body_inspection(
    make_update_context,
):
    client = FakeModelsClient(json_error=ValueError("invalid payload"))
    update, context = make_update_context(text="/health", client=client)

    asyncio.run(bot.health_command(update, context))

    assert update.message.replies[-1] == "게이트웨이가 정상적으로 준비되어 있어요."


def test_health_command_handles_gateway_failure(make_update_context):
    request = httpx.Request("GET", "http://test/health/ready")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/health", client=client)

    asyncio.run(bot.health_command(update, context))

    assert (
        update.message.replies[-1] == "게이트웨이 상태가 불안정하거나 사용할 수 없어요."
    )


def test_health_command_handles_gateway_status_error(make_update_context):
    request = httpx.Request("GET", "http://test/health/ready")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError(
        "service unavailable", request=request, response=response
    )
    client = FakeModelsClient(status_error=status_error)
    update, context = make_update_context(text="/health", client=client)

    asyncio.run(bot.health_command(update, context))

    assert (
        update.message.replies[-1] == "게이트웨이 상태가 불안정하거나 사용할 수 없어요."
    )


def test_health_command_handles_missing_client(make_update_context):
    update, context = make_update_context(text="/health", client=None)

    asyncio.run(bot.health_command(update, context))

    assert (
        update.message.replies[-1]
        == "게이트웨이에 연결할 수 없어요. 잠시 후 다시 시도해주세요."
    )


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
    client = FakeModelsClient(
        payload={"models": [{"id": "gpt-4o-mini"}, {"id": "claude-3-5"}]}
    )
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
    update, context = make_update_context(
        text="/model gpt-4o-mini", client=None, args=["gpt-4o-mini"]
    )

    asyncio.run(bot.model_command(update, context))

    assert update.message.replies[-1] == "지금은 모델을 변경할 수 없어요."


def test_model_command_resets_selected_model_with_default_alias(make_update_context):
    user_id = 90
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    update, context = make_update_context(
        user_id=user_id, text="/model default", client=None, args=["default"]
    )

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert (
        update.message.replies[-1]
        == "모델 설정을 초기화했습니다. 기본 모델을 사용합니다."
    )


def test_model_command_resets_selected_model_with_reset_alias(make_update_context):
    user_id = 91
    bot.user_selected_models[user_id] = "claude-3-5"
    update, context = make_update_context(
        user_id=user_id, text="/model reset", client=None, args=["reset"]
    )

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert (
        update.message.replies[-1]
        == "모델 설정을 초기화했습니다. 기본 모델을 사용합니다."
    )


def test_model_command_resets_selected_model_with_mixed_case_alias(make_update_context):
    user_id = 92
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    update, context = make_update_context(
        user_id=user_id, text="/model DEFAULT", client=None, args=["DEFAULT"]
    )

    asyncio.run(bot.model_command(update, context))

    assert bot.user_selected_models.get(user_id) is None
    assert (
        update.message.replies[-1]
        == "모델 설정을 초기화했습니다. 기본 모델을 사용합니다."
    )


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


def test_preset_command_sets_supported_preset_with_case_normalization(
    make_update_context,
):
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
        "지원하지 않는 프리셋입니다. 사용 가능: "
        + ", ".join(bot.get_static_presets().keys())
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
                {
                    "name": "normal",
                    "description": "Balanced assistant for general use.",
                    "prompt_prefix": "",
                },
                {
                    "name": "coder",
                    "description": "Focused on programming and debugging tasks.",
                    "prompt_prefix": "You are a practical coding assistant. Be precise and production-minded.\n\n",
                },
                {
                    "name": "english",
                    "description": "Helps improve English writing and grammar.",
                    "prompt_prefix": "You are an English writing helper. Improve clarity, grammar, and tone.\n\n",
                },
                {
                    "name": "quant",
                    "description": "Supports quantitative and analytical reasoning.",
                    "prompt_prefix": "You are a quantitative reasoning assistant. Show concise, correct math.\n\n",
                },
            ]
        }
    )
    update, context = make_update_context(text="/reload_presets", client=client)

    asyncio.run(bot.reload_presets_command(update, context))

    assert (
        update.message.replies[-1]
        == "프리셋을 다시 불러왔습니다: normal, coder, english, quant"
    )


def test_reload_presets_command_falls_back_safely_on_gateway_failure(
    make_update_context,
):
    request = httpx.Request("GET", "http://test/presets")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/reload_presets", client=client)

    asyncio.run(bot.reload_presets_command(update, context))

    assert context.application.bot_data[bot.PRESETS_KEY] == bot.get_static_presets()
    assert (
        update.message.replies[-1]
        == "게이트웨이 프리셋을 불러오지 못해 기본 프리셋으로 유지합니다."
    )


def test_preset_command_uses_refreshed_values_after_reload(make_update_context):
    client = FakeModelsClient(
        payload={
            "presets": [
                {
                    "name": "research",
                    "description": "Research",
                    "prompt_prefix": "Preset: research.\n\n",
                }
            ]
        }
    )
    reload_update, reload_context = make_update_context(
        text="/reload_presets", client=client
    )

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
        "현재 세션: default\n전체 세션 수: 1\n\n보유한 세션:\n- default"
    )


def test_session_command_no_arg_multiple_sessions(make_update_context):
    user_id = 320
    bot.ensure_user_sessions(user_id)["trading"] = ["User: market"]
    bot.ensure_user_sessions(user_id)["coding"] = ["User: python"]
    bot.user_active_sessions[user_id] = "trading"
    update, context = make_update_context(user_id=user_id, text="/session", client=None)

    asyncio.run(bot.session_command(update, context))

    assert update.message.replies[-1] == (
        "현재 세션: trading\n전체 세션 수: 2\n\n보유한 세션:\n- coding\n- trading"
    )


def test_session_command_switches_session(make_update_context):
    user_id = 321
    update, context = make_update_context(
        user_id=user_id, text="/session work", client=None, args=["work"]
    )

    asyncio.run(bot.session_command(update, context))

    assert bot.user_active_sessions[user_id] == "work"
    assert bot.get_session_history(user_id, "work") == []
    assert update.message.replies[-1] == "세션 변경: work"


def test_session_command_switches_to_trimmed_name(make_update_context):
    user_id = 322
    long_name = "x" * 50
    update, context = make_update_context(
        user_id=user_id, text=f"/session {long_name}", client=None, args=[long_name]
    )

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
    client = FakeModelsClient(
        payload={"models": [{"id": "gpt-4o-mini"}, {"id": "claude-3-5"}]}
    )
    update, context = make_update_context(text="/models", client=client)

    asyncio.run(bot.models_command(update, context))

    assert len(client.calls) == 1
    assert client.calls[0]["path"] == bot.AI_GATEWAY_MODELS_PATH
    assert "X-Request-Id" in client.calls[0]["headers"]
    assert isinstance(client.calls[0]["headers"]["X-Request-Id"], str)
    assert client.calls[0]["headers"]["X-Request-Id"]
    assert (
        update.message.replies[-1]
        == "사용 가능한 모델 목록\n- gpt-4o-mini\n- claude-3-5"
    )


def test_models_command_handles_gateway_failure(make_update_context):
    request = httpx.Request("GET", "http://test/models")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/models", client=client)

    asyncio.run(bot.models_command(update, context))

    assert (
        update.message.replies[-1]
        == "죄송해요. 모델 목록을 불러오지 못했어요. 잠시 후 다시 시도해주세요."
    )


def test_models_command_handles_gateway_status_error(make_update_context):
    request = httpx.Request("GET", "http://test/models")
    response = httpx.Response(503, request=request)
    status_error = httpx.HTTPStatusError(
        "service unavailable", request=request, response=response
    )
    client = FakeModelsClient(status_error=status_error)
    update, context = make_update_context(text="/models", client=client)

    asyncio.run(bot.models_command(update, context))

    assert (
        update.message.replies[-1]
        == "죄송해요. 모델 목록을 불러오지 못했어요. 잠시 후 다시 시도해주세요."
    )


def test_models_command_handles_missing_client(make_update_context):
    update, context = make_update_context(text="/models", client=None)

    asyncio.run(bot.models_command(update, context))

    assert (
        update.message.replies[-1] == "죄송해요. 지금은 모델 목록을 가져올 수 없어요."
    )


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


def test_load_bot_state_normalizes_invalid_document_mode_to_default(
    tmp_path, monkeypatch
):
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


def test_load_bot_state_replaces_existing_state_instead_of_merging(
    tmp_path, monkeypatch
):
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

    assert bot.conversations == {
        2: {bot.DEFAULT_SESSION_NAME: ["User: new", "AI: value"]}
    }
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


def test_load_bot_state_normalizes_presets_and_strips_model_values(
    tmp_path, monkeypatch
):
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
    def __init__(
        self,
        post_payload=None,
        get_payload=None,
        get_payloads=None,
        post_error=None,
        get_error=None,
        status_error=None,
    ):
        self.post_payload = (
            post_payload if post_payload is not None else {"job_id": "job-123"}
        )
        self.get_payload = (
            get_payload
            if get_payload is not None
            else {
                "queue": {"pending": 1, "running": 0, "completed": 2, "failed": 0},
                "worker": {"status": "online"},
            }
        )
        self.get_payloads = get_payloads or {}
        self.post_error = post_error
        self.get_error = get_error
        self.status_error = status_error
        self.calls = []

    async def post(self, path, json=None, headers=None):
        self.calls.append(
            {"method": "POST", "path": path, "json": json, "headers": headers}
        )
        if self.post_error is not None:
            raise self.post_error
        return FakeGetResponse(
            payload=self.post_payload, status_error=self.status_error
        )

    async def get(self, path, headers=None):
        self.calls.append({"method": "GET", "path": path, "headers": headers})
        if self.get_error is not None:
            raise self.get_error
        payload = self.get_payloads.get(path, self.get_payload)
        if isinstance(payload, list):
            payload = payload.pop(0) if payload else self.get_payload
        return FakeGetResponse(payload=payload, status_error=self.status_error)


def test_wiki_ask_creates_job_without_accepted_message_by_default(
    make_update_context, monkeypatch
):
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
    assert update.message.replies == []


def test_legacy_wiki_capture_creates_no_job_and_returns_obsidian_guidance(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"job_id": "cap-1"})
    update, context = make_update_context(
        text="/wiki capture quick note",
        client=client,
        args=["capture", "quick", "note"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == []
    assert update.message.replies == [
        "/wiki capture는 더 이상 지원하지 않습니다. 새 메모 작성이나 편집은 Obsidian 앱에서 "
        "직접 해주세요. 사용 가능한 명령은 /wiki help에서 확인할 수 있습니다."
    ]


def test_wiki_disallowed_user_is_rejected(make_update_context, monkeypatch, caplog):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "999")
    client = FakeObsidianClient()
    update, context = make_update_context(
        user_id=123, text="/wiki ingest", client=client, args=["ingest"]
    )

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
    update, context = make_update_context(
        text="/wiki", client=FakeObsidianClient(), args=[]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == bot.WIKI_HELP_MESSAGE


def test_wiki_help_does_not_advertise_capture_and_describes_ingest_source_notes():
    assert "capture" not in bot.WIKI_HELP_MESSAGE
    assert "/wiki capture" not in bot.HELP_MESSAGE
    assert "Obsidian에서 직접 작성한 소스 메모 처리" in bot.WIKI_HELP_MESSAGE
    assert "/wiki update <파일 경로 또는 수정 내용 설명>" in bot.WIKI_HELP_MESSAGE
    assert "/wiki lint [instruction]" in bot.WIKI_HELP_MESSAGE
    assert "/wiki refactor --preview <instruction>" in bot.WIKI_HELP_MESSAGE
    assert "refactor" in bot.HELP_MESSAGE
    assert "ask|save|ingest|update|lint|refactor|status|result" in bot.HELP_MESSAGE
    assert "/wiki draft <topic> - (선택)" in bot.HELP_MESSAGE
    assert "선택: ask→save와 별개" in bot.WIKI_HELP_MESSAGE
    assert "/wiki save <ask_job_id>" in bot.WIKI_HELP_MESSAGE


def test_wiki_save_creates_job_with_exact_source_job_payload(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"job_id": "save-1"})
    update, context = make_update_context(
        user_id=123,
        chat_id=456,
        text="/wiki save ask-42",
        client=client,
        args=["save", "ask-42"],
    )
    update.message.message_id = 789

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["json"] == {
        "command": "save",
        "payload": {"source_job_id": "ask-42"},
        "telegram_chat_id": 456,
        "telegram_message_id": 789,
        "requested_by": 123,
    }
    assert update.message.replies == []


def test_wiki_save_missing_or_blank_id_creates_no_job(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")

    for args in (["save"], ["save", "  "]):
        client = FakeObsidianClient()
        update, context = make_update_context(
            text="/wiki save", client=client, args=args
        )

        asyncio.run(bot.wiki_command(update, context))

        assert client.calls == []
        assert update.message.replies == [bot.WIKI_SAVE_USAGE_MESSAGE]


def test_wiki_ask_requires_question(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    update, context = make_update_context(
        text="/wiki ask", client=FakeObsidianClient(), args=["ask"]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == bot.WIKI_HELP_MESSAGE


def test_legacy_wiki_capture_without_text_returns_obsidian_guidance(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    update, context = make_update_context(
        text="/wiki capture", client=FakeObsidianClient(), args=["capture"]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == bot.WIKI_CAPTURE_REMOVED_MESSAGE


def test_wiki_ingest_creates_ingest_job(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"id": 55})
    update, context = make_update_context(
        text="/wiki ingest", client=client, args=["ingest"]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["json"]["command"] == "ingest"
    assert client.calls[0]["json"]["payload"] == {}
    assert update.message.replies == []


def test_wiki_update_creates_job_with_exact_raw_instruction(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"job_id": "update-1"})
    update, context = make_update_context(
        user_id=123,
        chat_id=456,
        text="/wiki update   Projects/계획.md의  제목을  변경해줘\n둘째 줄 유지   ",
        client=client,
        args=[
            "update",
            "Projects/계획.md의",
            "제목을",
            "변경해줘",
            "둘째",
            "줄",
            "유지",
        ],
    )
    update.message.message_id = 789

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["path"] == bot.OBSIDIAN_JOBS_PATH
    assert client.calls[0]["json"] == {
        "command": "update",
        "payload": {
            "instruction": "Projects/계획.md의  제목을  변경해줘\n둘째 줄 유지"
        },
        "telegram_chat_id": 456,
        "telegram_message_id": 789,
        "requested_by": 123,
    }
    assert update.message.replies == []


def test_wiki_update_missing_or_blank_instruction_creates_no_job(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")

    for text, args in (
        ("/wiki update", ["update"]),
        ("/wiki update   \n ", ["update"]),
    ):
        client = FakeObsidianClient()
        update, context = make_update_context(text=text, client=client, args=args)

        asyncio.run(bot.wiki_command(update, context))

        assert client.calls == []
        assert update.message.replies == [bot.WIKI_UPDATE_USAGE_MESSAGE]


def test_wiki_lint_without_instruction_creates_exact_empty_payload(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"job_id": "lint-1"})
    update, context = make_update_context(
        user_id=123,
        chat_id=456,
        text="/wiki lint",
        client=client,
        args=["lint"],
    )
    update.message.message_id = 789

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == [
        {
            "method": "POST",
            "path": bot.OBSIDIAN_JOBS_PATH,
            "json": {
                "command": "lint",
                "payload": {},
                "telegram_chat_id": 456,
                "telegram_message_id": 789,
                "requested_by": 123,
            },
            "headers": client.calls[0]["headers"],
        }
    ]
    assert update.message.replies == []


def test_wiki_lint_with_instruction_creates_exact_instruction_payload(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"job_id": "lint-2"})
    update, context = make_update_context(
        user_id=123,
        chat_id=456,
        text="/wiki lint   Fix  headings only\nKeep links unchanged   ",
        client=client,
        args=["lint", "Fix", "headings", "only", "Keep", "links", "unchanged"],
    )
    update.message.message_id = 790

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["path"] == bot.OBSIDIAN_JOBS_PATH
    assert client.calls[0]["json"] == {
        "command": "lint",
        "payload": {"instruction": "Fix  headings only\nKeep links unchanged"},
        "telegram_chat_id": 456,
        "telegram_message_id": 790,
        "requested_by": 123,
    }
    assert update.message.replies == []


def test_wiki_refactor_preview_creates_job_with_exact_payload(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"job_id": "refactor-1"})
    update, context = make_update_context(
        user_id=123,
        chat_id=456,
        text="/wiki refactor --preview   Rename  headings\nKeep links unchanged   ",
        client=client,
        args=["refactor", "--preview", "Rename", "headings", "Keep", "links"],
    )
    update.message.message_id = 791

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["json"] == {
        "command": "refactor",
        "payload": {
            "mode": "preview",
            "instruction": "Rename  headings\nKeep links unchanged",
        },
        "telegram_chat_id": 456,
        "telegram_message_id": 791,
        "requested_by": 123,
    }
    assert "preview" not in client.calls[0]["json"]["payload"]
    assert "request" not in client.calls[0]["json"]["payload"]
    assert update.message.replies == []


def test_wiki_refactor_missing_preview_creates_no_job(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient()
    update, context = make_update_context(
        text="/wiki refactor rename headings",
        client=client,
        args=["refactor", "rename", "headings"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == []
    assert update.message.replies == [bot.WIKI_REFACTOR_USAGE_MESSAGE]


def test_wiki_refactor_blank_instruction_creates_no_job(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient()
    update, context = make_update_context(
        text="/wiki refactor --preview   \n ",
        client=client,
        args=["refactor", "--preview"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == []
    assert update.message.replies == [bot.WIKI_REFACTOR_USAGE_MESSAGE]


def test_wiki_refactor_apply_like_request_creates_no_job(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient()
    update, context = make_update_context(
        text="/wiki refactor --apply rename headings",
        client=client,
        args=["refactor", "--apply", "rename", "headings"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == []
    assert update.message.replies == [bot.WIKI_REFACTOR_USAGE_MESSAGE]


def test_existing_wiki_job_commands_keep_their_payloads(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    cases = [
        (
            "/wiki ask existing question",
            ["ask", "existing", "question"],
            "ask",
            {"question": "existing question"},
        ),
        ("/wiki ingest", ["ingest"], "ingest", {}),
        (
            "/wiki draft existing topic",
            ["draft", "existing", "topic"],
            "draft",
            {"topic": "existing topic"},
        ),
    ]

    for text, args, command, payload in cases:
        client = FakeObsidianClient()
        update, context = make_update_context(text=text, client=client, args=args)

        asyncio.run(bot.wiki_command(update, context))

        assert client.calls[0]["json"]["command"] == command
        assert client.calls[0]["json"]["payload"] == payload


def test_wiki_draft_creates_job_without_accepted_message_by_default(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(post_payload={"job_id": "draft-1"})
    update, context = make_update_context(
        text="/wiki draft trip plan",
        client=client,
        args=["draft", "trip", "plan"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["json"]["command"] == "draft"
    assert client.calls[0]["json"]["payload"] == {"topic": "trip plan"}
    assert update.message.replies == []


def test_wiki_send_accepted_message_env_restores_old_behavior(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_SEND_ACCEPTED_MESSAGE", "true")
    client = FakeObsidianClient(post_payload={"job_id": "draft-accepted"})
    update, context = make_update_context(
        text="/wiki draft quick note",
        client=client,
        args=["draft", "quick", "note"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == (
        "위키 draft 작업을 접수했어요.\n"
        "job_id=draft-accepted\n\n"
        "완료되면 이 채팅방으로 결과를 보내드릴게요.\n"
        "수동 확인: /wiki result draft-accepted"
    )


def test_wiki_gateway_failure_returns_friendly_error(make_update_context, monkeypatch):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    request = httpx.Request("POST", "http://test/obsidian/jobs")
    client = FakeObsidianClient(post_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(
        text="/wiki ingest", client=client, args=["ingest"]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert (
        update.message.replies[-1]
        == "위키 작업을 접수하지 못했어요. 잠시 후 다시 시도해주세요."
    )


def test_wiki_status_calls_gateway_with_auth_and_renders_queue_counts(
    make_update_context, monkeypatch
):
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
    update, context = make_update_context(
        text="/wiki status", client=client, args=["status"]
    )

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


def test_wiki_status_with_job_id_fetches_job_and_replies_with_answer(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_TELEGRAM_INTERNAL_TOKEN", "detail-token")
    client = FakeObsidianClient(
        get_payloads={
            "/obsidian/jobs/job-42": {
                "job_id": "job-42",
                "status": "succeeded",
                "result_text": json.dumps(
                    {"answer": "여행지는 제주였어요.", "references": ["Trips/Jeju.md"]}
                ),
            }
        }
    )
    update, context = make_update_context(
        text="/wiki status job-42",
        client=client,
        args=["status", "job-42"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == [
        {
            "method": "GET",
            "path": "/obsidian/jobs/job-42",
            "headers": {
                "X-Request-Id": client.calls[0]["headers"]["X-Request-Id"],
                "Authorization": "Bearer detail-token",
            },
        }
    ]
    assert (
        update.message.replies[-1] == "여행지는 제주였어요.\n\n참고:\n- Trips/Jeju.md"
    )


def test_wiki_result_with_job_id_fetches_job_and_replies_with_answer(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(
        get_payloads={
            "/obsidian/jobs/job-43": {
                "job_id": "job-43",
                "status": "succeeded",
                "result_text": json.dumps({"answer": "결과 본문입니다."}),
            }
        }
    )
    update, context = make_update_context(
        text="/wiki result job-43",
        client=client,
        args=["result", "job-43"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["path"] == "/obsidian/jobs/job-43"
    assert update.message.replies[-1] == "결과 본문입니다."


def test_wiki_result_with_job_id_sends_internal_auth_token(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_TELEGRAM_INTERNAL_TOKEN", "result-token")
    client = FakeObsidianClient(
        get_payloads={
            "/obsidian/jobs/job-auth": {
                "job_id": "job-auth",
                "status": "succeeded",
                "result_text": "ok",
            }
        }
    )
    update, context = make_update_context(
        text="/wiki result job-auth",
        client=client,
        args=["result", "job-auth"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls[0]["method"] == "GET"
    assert client.calls[0]["path"] == "/obsidian/jobs/job-auth"
    assert client.calls[0]["headers"]["Authorization"] == "Bearer result-token"


def test_wiki_ask_auto_result_polling_immediate_success(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS", "1")

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)
    client = FakeObsidianClient(
        post_payload={"job_id": "ask-fast"},
        get_payloads={
            "/obsidian/jobs/ask-fast": [
                {
                    "job_id": "ask-fast",
                    "status": "succeeded",
                    "result_text": json.dumps({"answer": "바로 완료"}),
                }
            ]
        },
    )
    update, context = make_update_context(
        text="/wiki ask now?",
        client=client,
        args=["ask", "now?"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert [call["method"] for call in client.calls] == ["POST", "GET", "POST"]
    assert client.calls[-1]["path"] == "/obsidian/jobs/ask-fast/notified"
    assert update.message.replies[-1] == "바로 완료\n\n저장: /wiki save ask-fast"


def test_wiki_ask_result_does_not_duplicate_worker_save_hint(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS", "1")
    worker_answer = "완료 답변\n\n저장하려면 /wiki save ask-hinted"
    client = FakeObsidianClient(
        post_payload={"job_id": "ask-hinted"},
        get_payloads={
            "/obsidian/jobs/ask-hinted": [
                {
                    "job_id": "ask-hinted",
                    "status": "succeeded",
                    "result_text": json.dumps({"answer": worker_answer}),
                }
            ]
        },
    )
    update, context = make_update_context(
        text="/wiki ask hinted?", client=client, args=["ask", "hinted?"]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == worker_answer
    assert update.message.replies[-1].lower().count("/wiki save") == 1


def test_wiki_ask_result_adds_current_hint_for_other_or_bare_save_command(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS", "1")
    cases = [
        "다른 답변 저장: /wiki save ask-other",
        "명령 형식은 /wiki save 다음에 작업 ID를 붙입니다.",
    ]

    for index, worker_answer in enumerate(cases):
        job_id = f"ask-current-{index}"
        client = FakeObsidianClient(
            post_payload={"job_id": job_id},
            get_payloads={
                f"/obsidian/jobs/{job_id}": [
                    {
                        "job_id": job_id,
                        "status": "succeeded",
                        "result_text": json.dumps({"answer": worker_answer}),
                    }
                ]
            },
        )
        update, context = make_update_context(
            text="/wiki ask save syntax?",
            client=client,
            args=["ask", "save", "syntax?"],
        )

        asyncio.run(bot.wiki_command(update, context))

        assert update.message.replies[-1] == (
            f"{worker_answer}\n\n저장: /wiki save {job_id}"
        )


def test_wiki_save_hint_matching_respects_exact_job_id_token_boundaries():
    job_id = "ask-12"

    assert bot.contains_wiki_save_command_for_job("/wiki save ask-12", job_id)
    assert bot.contains_wiki_save_command_for_job("저장: /WIKI SAVE ask-12.", job_id)
    assert bot.contains_wiki_save_command_for_job("`/wiki save ask-12`", job_id)
    assert not bot.contains_wiki_save_command_for_job("/wiki save", job_id)
    assert not bot.contains_wiki_save_command_for_job("/wiki save ask-123", job_id)
    assert not bot.contains_wiki_save_command_for_job("/wiki save ask-1", job_id)


def test_wiki_ask_auto_result_failed_and_expired_do_not_include_save_hint(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS", "1")
    cases = [
        (
            "failed",
            {"error_text": "worker timeout"},
            "위키 작업이 실패했어요. job_id=ask-failed\n오류: worker timeout",
        ),
        (
            "expired",
            {},
            "작업은 완료됐지만 결과 보관 기간이 지나 표시할 내용이 없어요. 다시 요청해 주세요.",
        ),
    ]

    for status, extra_payload, expected_message in cases:
        job_id = f"ask-{status}"
        client = FakeObsidianClient(
            post_payload={"job_id": job_id},
            get_payloads={
                f"/obsidian/jobs/{job_id}": [
                    {"job_id": job_id, "status": status, **extra_payload}
                ]
            },
        )
        update, context = make_update_context(
            text="/wiki ask terminal?",
            client=client,
            args=["ask", "terminal?"],
        )

        asyncio.run(bot.wiki_command(update, context))

        assert update.message.replies[-1] == expected_message
        assert "/wiki save" not in update.message.replies[-1]


def test_wiki_ask_auto_result_send_failure_does_not_mark_notified(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS", "1")

    async def fake_send_chunked_message_as_replies(_update, _text):
        raise RuntimeError("telegram send failed")

    monkeypatch.setattr(
        bot, "_send_chunked_message_as_replies", fake_send_chunked_message_as_replies
    )
    client = FakeObsidianClient(
        post_payload={"job_id": "ask-send-fail"},
        get_payloads={
            "/obsidian/jobs/ask-send-fail": [
                {
                    "job_id": "ask-send-fail",
                    "status": "succeeded",
                    "result_text": json.dumps({"answer": "바로 완료"}),
                }
            ]
        },
    )
    update, context = make_update_context(
        text="/wiki ask now?",
        client=client,
        args=["ask", "now?"],
    )

    try:
        asyncio.run(bot.wiki_command(update, context))
    except RuntimeError as error:
        assert str(error) == "telegram send failed"
    else:
        raise AssertionError("expected telegram send failure")

    assert [call["method"] for call in client.calls] == ["POST", "GET"]
    assert all(not call["path"].endswith("/notified") for call in client.calls)


def test_wiki_ask_auto_result_polling_timeout_sends_no_message_by_default(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS", "1")

    async def fake_sleep(_seconds):
        return None

    monkeypatch.setattr(bot.asyncio, "sleep", fake_sleep)
    client = FakeObsidianClient(
        post_payload={"job_id": "ask-slow"},
        get_payloads={
            "/obsidian/jobs/ask-slow": [{"job_id": "ask-slow", "status": "running"}]
        },
    )
    update, context = make_update_context(
        text="/wiki ask later?",
        client=client,
        args=["ask", "later?"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert [call["method"] for call in client.calls] == ["POST", "GET"]
    assert update.message.replies == []


def test_wiki_ask_auto_result_polling_slow_detail_sends_no_message_by_default(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    monkeypatch.setenv("OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS", "1")

    class HangingObsidianClient(FakeObsidianClient):
        async def get(self, path, headers=None):
            self.calls.append({"method": "GET", "path": path, "headers": headers})
            await asyncio.Event().wait()

    client = HangingObsidianClient(post_payload={"job_id": "ask-hang"})
    update, context = make_update_context(
        text="/wiki ask hanging?",
        client=client,
        args=["ask", "hanging?"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert [call["method"] for call in client.calls] == ["POST", "GET"]
    assert update.message.replies == []


def test_wiki_job_result_processing_status_returns_processing_message(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(
        get_payloads={
            "/obsidian/jobs/job-running": {
                "job_id": "job-running",
                "status": "running",
            }
        }
    )
    update, context = make_update_context(
        text="/wiki result job-running",
        client=client,
        args=["result", "job-running"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert (
        update.message.replies[-1]
        == "위키 작업이 아직 처리 중이에요. job_id=job-running status=running"
    )


def test_wiki_job_result_failed_status_includes_error_text(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(
        get_payloads={
            "/obsidian/jobs/job-failed": {
                "job_id": "job-failed",
                "command": "ask",
                "status": "failed",
                "error_text": "worker timeout",
            }
        }
    )
    update, context = make_update_context(
        text="/wiki result job-failed",
        client=client,
        args=["result", "job-failed"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert (
        update.message.replies[-1]
        == "위키 작업이 실패했어요. job_id=job-failed\n오류: worker timeout"
    )


def test_wiki_job_failure_extracts_actual_cause_from_json_and_error_markers():
    cases = [
        ('{"error":{"message":"OpenCode model unavailable"}}', "OpenCode model unavailable"),
        ('"worker command failed"', "worker command failed"),
        (
            '{"error":"OPENCODE_ERROR","message":"command exited with status 7"}',
            "command exited with status 7",
        ),
        (
            '{"error":"OPENCODE_ERROR","detail":"model credentials unavailable"}',
            "model credentials unavailable",
        ),
        (
            '{"error":"descriptive worker failure","message":"secondary message"}',
            "descriptive worker failure",
        ),
        ("OPENCODE_ERROR: command exited with status 7", "command exited with status 7"),
        ("[ERROR] missing API key", "missing API key"),
        ("ordinary malformed error {", "ordinary malformed error {"),
    ]

    for error_text, cause in cases:
        message = bot.build_obsidian_job_result_message(
            {"job_id": "failed-1", "status": "failed", "error_text": error_text}
        )

        assert message == f"위키 작업이 실패했어요. job_id=failed-1\n오류: {cause}"
        assert "{\"error\"" not in message
        assert "OPENCODE_ERROR" not in message


def test_wiki_job_failure_preserves_valid_json_with_unsupported_scalar_shape():
    for error_text in ("1", "1.5", "false", "null", '["timeout"]'):
        message = bot.build_obsidian_job_result_message(
            {"job_id": "failed-scalar", "status": "failed", "error_text": error_text}
        )

        assert message == (
            f"위키 작업이 실패했어요. job_id=failed-scalar\n오류: {error_text}"
        )


def test_wiki_job_failure_with_only_marker_uses_existing_safe_fallback():
    message = bot.build_obsidian_job_result_message(
        {
            "job_id": "failed-marker-only",
            "status": "failed",
            "error_text": '{"error":"OPENCODE_ERROR"}',
        }
    )

    assert message == "위키 작업이 실패했어요. job_id=failed-marker-only"


def test_successful_wiki_job_rendering_is_unchanged_by_failure_normalization():
    message = bot.build_obsidian_job_result_message(
        {
            "job_id": "success-1",
            "status": "succeeded",
            "result_text": json.dumps(
                {"answer": "완료 답변", "references": ["Notes/Source.md"]}
            ),
        }
    )

    assert message == "완료 답변\n\n참고:\n- Notes/Source.md"


def test_wiki_job_result_expired_status_returns_retry_message(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(
        get_payloads={
            "/obsidian/jobs/job-expired": {
                "job_id": "job-expired",
                "command": "ask",
                "status": "expired",
            }
        }
    )
    update, context = make_update_context(
        text="/wiki result job-expired",
        client=client,
        args=["result", "job-expired"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert update.message.replies[-1] == (
        "작업은 완료됐지만 결과 보관 기간이 지나 표시할 내용이 없어요. 다시 요청해 주세요."
    )
    assert (
        update.message.replies[-1]
        != "위키 작업은 완료됐지만 표시할 결과가 비어 있어요."
    )


def test_wiki_job_result_blank_result_uses_safe_fallback(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    client = FakeObsidianClient(
        get_payloads={
            "/obsidian/jobs/job-blank": {
                "job_id": "job-blank",
                "status": "succeeded",
                "result_text": "   ",
            }
        }
    )
    update, context = make_update_context(
        text="/wiki result job-blank",
        client=client,
        args=["result", "job-blank"],
    )

    asyncio.run(bot.wiki_command(update, context))

    assert (
        update.message.replies[-1]
        == "작업은 완료됐지만 결과 보관 기간이 지나 표시할 내용이 없어요. 다시 요청해 주세요."
    )


def test_wiki_status_gateway_failure_returns_friendly_error(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "123")
    request = httpx.Request("GET", "http://test/obsidian/status")
    client = FakeObsidianClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(
        text="/wiki status", client=client, args=["status"]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert (
        update.message.replies[-1]
        == "위키 상태를 불러오지 못했어요. 잠시 후 다시 시도해주세요."
    )


def test_wiki_status_disallowed_user_does_not_call_gateway(
    make_update_context, monkeypatch
):
    monkeypatch.setenv("ALLOWED_TELEGRAM_USER_IDS", "999")
    client = FakeObsidianClient()
    update, context = make_update_context(
        user_id=123, text="/wiki status", client=client, args=["status"]
    )

    asyncio.run(bot.wiki_command(update, context))

    assert client.calls == []
    assert update.message.replies[-1] == bot.build_wiki_denied_message(123)


class FakeTelegramBot:
    def __init__(self, fail=False):
        self.fail = fail
        self.sent_messages = []

    async def send_message(self, chat_id, text):
        if self.fail:
            raise RuntimeError("telegram down")
        self.sent_messages.append({"chat_id": chat_id, "text": text})


def make_fake_app(client, telegram_bot=None):
    from types import SimpleNamespace

    return SimpleNamespace(
        bot_data={bot.HTTP_CLIENT_KEY: client},
        bot=telegram_bot or FakeTelegramBot(),
    )


def test_init_http_client_registers_obsidian_notification_polling_task(monkeypatch):
    from types import SimpleNamespace

    fake_task = object()
    started = []

    async def fake_load_gateway_presets(_app):
        return None

    def fake_create_task(coro):
        coro.close()
        started.append(True)
        return fake_task

    monkeypatch.setattr(bot, "AI_GATEWAY_BASE_URL", "http://gateway.local")
    monkeypatch.setattr(bot, "load_gateway_presets", fake_load_gateway_presets)
    monkeypatch.setattr(bot.asyncio, "create_task", fake_create_task)
    app = SimpleNamespace(bot_data={})

    asyncio.run(bot.init_http_client(app))

    assert started == [True]
    assert app.bot_data[bot.OBSIDIAN_NOTIFICATION_TASK_KEY] is fake_task
    asyncio.run(app.bot_data[bot.HTTP_CLIENT_KEY].aclose())


def test_obsidian_notification_poll_fetches_next_completed_job(monkeypatch):
    monkeypatch.setenv("OBSIDIAN_TELEGRAM_INTERNAL_TOKEN", "notify-token")
    client = FakeObsidianClient(get_payload={})
    app = make_fake_app(client)

    asyncio.run(bot.poll_obsidian_job_notification_once(app))

    assert client.calls == [
        {
            "method": "GET",
            "path": bot.OBSIDIAN_NOTIFICATIONS_NEXT_PATH,
            "headers": {
                "X-Request-Id": client.calls[0]["headers"]["X-Request-Id"],
                "Authorization": "Bearer notify-token",
            },
        }
    ]


def test_obsidian_notification_succeeded_job_sends_rendered_answer_and_marks_notified(
    monkeypatch,
):
    monkeypatch.setenv("OBSIDIAN_TELEGRAM_INTERNAL_TOKEN", "notify-token")
    client = FakeObsidianClient(
        get_payload={
            "job_id": "job-100",
            "command": "ingest",
            "status": "succeeded",
            "telegram_chat_id": 777,
            "result_text": json.dumps({"answer": "완료 답변", "references": ["A.md"]}),
        }
    )
    telegram_bot = FakeTelegramBot()
    app = make_fake_app(client, telegram_bot)

    delivered = asyncio.run(bot.poll_obsidian_job_notification_once(app))

    assert delivered is True
    assert telegram_bot.sent_messages == [
        {"chat_id": 777, "text": "완료 답변\n\n참고:\n- A.md"}
    ]
    assert client.calls[-1]["method"] == "POST"
    assert client.calls[-1]["path"] == "/obsidian/jobs/job-100/notified"


def test_obsidian_notification_ask_result_includes_job_id_save_hint():
    client = FakeObsidianClient(
        get_payload={
            "job_id": "ask-100",
            "command": "ask",
            "status": "succeeded",
            "telegram_chat_id": 777,
            "result_text": json.dumps({"answer": "완료 답변"}),
        }
    )
    telegram_bot = FakeTelegramBot()
    app = make_fake_app(client, telegram_bot)

    delivered = asyncio.run(bot.poll_obsidian_job_notification_once(app))

    assert delivered is True
    assert telegram_bot.sent_messages == [
        {"chat_id": 777, "text": "완료 답변\n\n저장: /wiki save ask-100"}
    ]


def test_obsidian_notification_failed_job_sends_error_and_marks_notified():
    client = FakeObsidianClient(
        get_payload={
            "job_id": "job-fail",
            "status": "failed",
            "telegram_chat_id": 888,
            "error_text": "worker timeout",
        }
    )
    telegram_bot = FakeTelegramBot()
    app = make_fake_app(client, telegram_bot)

    delivered = asyncio.run(bot.poll_obsidian_job_notification_once(app))

    assert delivered is True
    assert telegram_bot.sent_messages == [
        {
            "chat_id": 888,
            "text": "위키 작업이 실패했어요. job_id=job-fail\n오류: worker timeout",
        }
    ]
    assert client.calls[-1]["path"] == "/obsidian/jobs/job-fail/notified"


def test_obsidian_notification_send_failure_does_not_mark_notified(caplog):
    client = FakeObsidianClient(
        get_payload={
            "job_id": "job-send-fail",
            "status": "succeeded",
            "telegram_chat_id": 999,
            "result_text": "hello",
        }
    )
    app = make_fake_app(client, FakeTelegramBot(fail=True))

    with caplog.at_level("WARNING"):
        delivered = asyncio.run(bot.poll_obsidian_job_notification_once(app))

    assert delivered is False
    assert [call["method"] for call in client.calls] == ["GET"]
    assert "obsidian_notification_send_failed" in caplog.text


def test_obsidian_notification_empty_response_sends_nothing():
    client = FakeObsidianClient(get_payload=None)
    telegram_bot = FakeTelegramBot()
    app = make_fake_app(client, telegram_bot)

    delivered = asyncio.run(bot.poll_obsidian_job_notification_once(app))

    assert delivered is False
    assert telegram_bot.sent_messages == []
    assert [call["method"] for call in client.calls] == ["GET"]


def test_wiki_accepted_messages_mention_automatic_delivery():
    assert (
        "완료되면 이 채팅방으로 결과를 보내드릴게요."
        in bot.build_wiki_accepted_message("ask", "a1")
    )
