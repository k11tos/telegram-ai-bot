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
    assert "/reset" in reply
    assert "/status" in reply
    assert "/version" in reply
    assert "/health" in reply
    assert "/session" in reply


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


def test_help_command_includes_reload_presets_command(make_update_context):
    update, context = make_update_context(text="/help", client=None)

    asyncio.run(bot.help_command(update, context))

    reply = update.message.replies[0]
    assert "/reload_presets" in reply


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

    assert update.message.replies[-1] == "현재 프리셋: normal"


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

    assert update.message.replies[-1] == "현재 프리셋: coder"


def test_preset_command_falls_back_to_default_for_invalid_value(make_update_context):
    user_id = 94
    bot.user_selected_presets[user_id] = " invalid "
    update, context = make_update_context(user_id=user_id, text="/preset", client=None)

    asyncio.run(bot.preset_command(update, context))

    assert update.message.replies[-1] == "현재 프리셋: normal"


def test_preset_command_normalizes_selected_preset_value(make_update_context):
    user_id = 95
    bot.user_selected_presets[user_id] = " Coder "
    update, context = make_update_context(user_id=user_id, text="/preset", client=None)

    asyncio.run(bot.preset_command(update, context))

    assert update.message.replies[-1] == "현재 프리셋: coder"


def test_reload_presets_command_uses_gateway_data(make_update_context):
    client = FakeModelsClient(
        payload={
            "presets": [
                {"name": "normal", "description": "기본", "prompt_prefix": ""},
                {"name": "coder", "description": "코딩", "prompt_prefix": "Coder: "},
                {"name": "english", "description": "영어", "prompt_prefix": "English: "},
                {"name": "quant", "description": "정량", "prompt_prefix": "Quant: "},
            ]
        }
    )
    update, context = make_update_context(text="/reload_presets", client=client)

    asyncio.run(bot.reload_presets_command(update, context))

    assert update.message.replies[-1] == "프리셋을 다시 불러왔습니다: normal, coder, english, quant"
    assert bot.get_presets_from_bot_data(context.application.bot_data)["coder"]["prompt_prefix"] == "Coder: "


def test_reload_presets_command_falls_back_safely_on_failure(make_update_context):
    request = httpx.Request("GET", "http://test/presets")
    client = FakeModelsClient(get_error=httpx.RequestError("down", request=request))
    update, context = make_update_context(text="/reload_presets", client=client)

    asyncio.run(bot.reload_presets_command(update, context))

    assert update.message.replies[-1] == "게이트웨이 프리셋을 불러오지 못해 기본 프리셋으로 유지합니다."
    assert bot.get_presets_from_bot_data(context.application.bot_data) == bot.get_static_presets()


def test_preset_command_uses_reloaded_values_after_reload(make_update_context):
    client = FakeModelsClient(
        payload={
            "presets": [
                {"name": "normal", "description": "기본", "prompt_prefix": ""},
                {"name": "focus", "description": "집중", "prompt_prefix": "Focus: "},
            ]
        }
    )
    reload_update, reload_context = make_update_context(text="/reload_presets", client=client)
    asyncio.run(bot.reload_presets_command(reload_update, reload_context))

    preset_update, preset_context = make_update_context(
        text="/preset focus",
        client=client,
        args=["focus"],
    )
    preset_context.application = reload_context.application

    asyncio.run(bot.preset_command(preset_update, preset_context))

    assert preset_update.message.replies[-1] == "프리셋이 변경되었습니다: focus"


def test_session_command_shows_current_default_session(make_update_context):
    update, context = make_update_context(text="/session", client=None)

    asyncio.run(bot.session_command(update, context))

    assert update.message.replies[-1] == "현재 세션: default"


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


def test_save_bot_state_writes_json_file(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_path = state_dir / "bot_state.json"
    monkeypatch.setattr(bot, "LOCAL_DATA_DIR", str(state_dir))
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.ensure_user_sessions(10)[bot.DEFAULT_SESSION_NAME] = ["User: hi", "AI: hello"]
    bot.user_selected_models[10] = "gpt-4o-mini"
    bot.user_selected_presets[10] = "coder"

    bot.save_bot_state()

    assert state_path.exists()
    payload = state_path.read_text(encoding="utf-8")
    assert '"version":1' in payload
    assert '"conversations":{"10":{"default":["User: hi","AI: hello"]}}' in payload
    assert '"selected_models":{"10":"gpt-4o-mini"}' in payload
    assert '"selected_presets":{"10":"coder"}' in payload


def test_load_bot_state_restores_saved_values(tmp_path, monkeypatch):
    state_dir = tmp_path / "state"
    state_dir.mkdir(parents=True, exist_ok=True)
    state_path = state_dir / "bot_state.json"
    state_path.write_text(
        '{"version":1,"conversations":{"123":{"default":["User: a","AI: b"]}},"active_sessions":{"123":"default"},'
        '"selected_models":{"123":"gpt-4o-mini"},"selected_presets":{"123":"ENGLISH"}}',
        encoding="utf-8",
    )
    monkeypatch.setattr(bot, "LOCAL_DATA_DIR", str(state_dir))
    monkeypatch.setattr(bot, "STATE_FILE_PATH", str(state_path))

    bot.load_bot_state()

    assert bot.get_session_history(123) == ["User: a", "AI: b"]
    assert bot.user_selected_models[123] == "gpt-4o-mini"
    assert bot.user_selected_presets[123] == "english"


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
