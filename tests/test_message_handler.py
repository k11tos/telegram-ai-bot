import asyncio
import json

import httpx
import pytest

import bot


class FakeStreamResponse:
    def __init__(self, lines, status_error=None):
        self._lines = lines
        self._status_error = status_error

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakePostResponse:
    def __init__(self, payload=None, status_error=None, json_error=None):
        self._payload = payload
        self._status_error = status_error
        self._json_error = json_error

    def raise_for_status(self):
        if self._status_error is not None:
            raise self._status_error
        return None

    def json(self):
        if self._json_error is not None:
            raise self._json_error
        return self._payload


class FakeClient:
    def __init__(
        self,
        stream_lines=None,
        stream_error=None,
        stream_status_error=None,
        post_payload=None,
        post_error=None,
        post_status_error=None,
        post_json_error=None,
    ):
        self.stream_lines = stream_lines or []
        self.stream_error = stream_error
        self.stream_status_error = stream_status_error
        self.post_payload = post_payload
        self.post_error = post_error
        self.post_status_error = post_status_error
        self.post_json_error = post_json_error
        self.stream_calls = []
        self.post_calls = []

    def stream(self, method, path, json=None, headers=None):
        self.stream_calls.append({"method": method, "path": path, "json": json, "headers": headers})
        if self.stream_error is not None:
            raise self.stream_error
        return FakeStreamResponse(self.stream_lines, status_error=self.stream_status_error)

    async def post(self, path, json=None, headers=None):
        self.post_calls.append({"path": path, "json": json, "headers": headers})
        if self.post_error is not None:
            raise self.post_error
        return FakePostResponse(
            payload=self.post_payload,
            status_error=self.post_status_error,
            json_error=self.post_json_error,
        )


def test_first_message_initializes_history_and_calls_gateway(make_update_context):
    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': '안녕하세요'})}",
            "data: [DONE]",
        ]
    )
    update, context = make_update_context(text="처음 질문", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert bot.conversations[123] == ["User: 처음 질문", "AI: 안녕하세요"]
    assert update.message.replies[0] == "생각 중…"
    assert update.message.waiting_message.edits[-1] == "안녕하세요"
    assert len(client.stream_calls) == 1
    assert client.stream_calls[0]["path"] == bot.AI_GATEWAY_STREAM_PATH
    assert client.stream_calls[0]["json"] == {"prompt": "User: 처음 질문\nAI:"}
    assert client.post_calls == []


def test_existing_conversation_trims_and_preserves_latest_history(make_update_context):
    user_id = 77
    old_history = [f"Turn {i}" for i in range(bot.MAX_HISTORY)]
    bot.conversations[user_id] = old_history[:]

    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': '새 답변'})}",
            "data: [DONE]",
        ]
    )
    update, context = make_update_context(user_id=user_id, text="새 질문", client=client)

    asyncio.run(bot.handle_message(update, context))

    expected_prompt_history = (old_history + ["User: 새 질문"])[-bot.MAX_HISTORY :]
    assert client.stream_calls[0]["json"] == {"prompt": "\n".join(expected_prompt_history) + "\nAI:"}

    expected_saved = (old_history + ["User: 새 질문", "AI: 새 답변"])[-bot.MAX_HISTORY :]
    assert bot.conversations[user_id] == expected_saved


def test_stream_failure_falls_back_to_chat_and_appends_reply(make_update_context):
    request = httpx.Request("POST", "http://test/chat")
    stream_error = httpx.RequestError("stream failed", request=request)
    client = FakeClient(
        stream_error=stream_error,
        post_payload={"response": "폴백 응답"},
    )
    update, context = make_update_context(text="질문", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert len(client.post_calls) == 1
    assert client.post_calls[0]["path"] == bot.AI_GATEWAY_CHAT_PATH
    assert bot.conversations[123] == ["User: 질문", "AI: 폴백 응답"]
    assert update.message.waiting_message.edits[-1] == "폴백 응답"


def test_backend_request_error_is_handled_gracefully(make_update_context):
    request = httpx.Request("POST", "http://test/chat")
    stream_error = httpx.RequestError("stream failed", request=request)
    post_error = httpx.RequestError("post failed", request=request)
    client = FakeClient(stream_error=stream_error, post_error=post_error)
    update, context = make_update_context(text="실패 테스트", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert bot.conversations[123] == []
    assert update.message.waiting_message.edits[-1] == "죄송합니다. AI 서버와의 연결에 실패했습니다. 잠시 후 다시 시도해주세요."
    assert bot.user_in_flight_requests[123] is False


@pytest.mark.parametrize(
    ("history_size", "expected_oldest_index"),
    [
        (bot.MAX_HISTORY - 1, 0),
        (bot.MAX_HISTORY, 1),
        (bot.MAX_HISTORY + 1, 2),
    ],
)
def test_prompt_history_boundaries_keep_expected_recent_lines(
    make_update_context, history_size, expected_oldest_index
):
    user_id = 91
    base_history = [f"H{i}" for i in range(history_size)]
    bot.conversations[user_id] = base_history[:]
    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': '경계 응답'})}",
            "data: [DONE]",
        ]
    )

    update, context = make_update_context(user_id=user_id, text="경계 질문", client=client)
    asyncio.run(bot.handle_message(update, context))

    expected_prompt_lines = (base_history + ["User: 경계 질문"])[-bot.MAX_HISTORY :]
    assert expected_prompt_lines[0] == f"H{expected_oldest_index}"
    assert client.stream_calls[0]["json"]["prompt"] == "\n".join(expected_prompt_lines) + "\nAI:"


def test_prompt_includes_exact_history_order_and_role_prefixes(make_update_context):
    user_id = 1001
    bot.conversations[user_id] = ["User: 첫 질문", "AI: 첫 답변", "User: 둘째 질문", "AI: 둘째 답변"]
    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': '셋째 답변'})}",
            "data: [DONE]",
        ]
    )

    update, context = make_update_context(user_id=user_id, text="셋째 질문", client=client)
    asyncio.run(bot.handle_message(update, context))

    assert client.stream_calls[0]["json"] == {
        "prompt": "User: 첫 질문\nAI: 첫 답변\nUser: 둘째 질문\nAI: 둘째 답변\nUser: 셋째 질문\nAI:"
    }


def test_multi_user_history_is_isolated(make_update_context):
    user_a = 11
    user_b = 22
    client_a = FakeClient(stream_lines=[f"data: {json.dumps({'response': 'A응답'})}", "data: [DONE]"])
    client_b = FakeClient(stream_lines=[f"data: {json.dumps({'response': 'B응답'})}", "data: [DONE]"])

    update_a, context_a = make_update_context(user_id=user_a, text="A질문", client=client_a)
    update_b, context_b = make_update_context(user_id=user_b, text="B질문", client=client_b)

    asyncio.run(bot.handle_message(update_a, context_a))
    asyncio.run(bot.handle_message(update_b, context_b))

    assert bot.conversations[user_a] == ["User: A질문", "AI: A응답"]
    assert bot.conversations[user_b] == ["User: B질문", "AI: B응답"]
    assert client_a.stream_calls[0]["json"]["prompt"] == "User: A질문\nAI:"
    assert client_b.stream_calls[0]["json"]["prompt"] == "User: B질문\nAI:"


def test_reset_clears_prior_conversation_for_next_prompt(make_update_context):
    user_id = 333
    initial_client = FakeClient(
        stream_lines=[f"data: {json.dumps({'response': '이전 응답'})}", "data: [DONE]"]
    )
    first_update, first_context = make_update_context(user_id=user_id, text="이전 질문", client=initial_client)
    asyncio.run(bot.handle_message(first_update, first_context))

    reset_update, reset_context = make_update_context(user_id=user_id, text="/reset", client=None)
    asyncio.run(bot.reset(reset_update, reset_context))

    next_client = FakeClient(stream_lines=[f"data: {json.dumps({'response': '새 응답'})}", "data: [DONE]"])
    next_update, next_context = make_update_context(user_id=user_id, text="새 질문", client=next_client)
    asyncio.run(bot.handle_message(next_update, next_context))

    assert next_client.stream_calls[0]["json"]["prompt"] == "User: 새 질문\nAI:"
    assert bot.conversations[user_id] == ["User: 새 질문", "AI: 새 응답"]


@pytest.mark.parametrize(
    ("post_kwargs", "expected_message"),
    [
        ({"post_error": httpx.ReadTimeout("read timeout")}, "응답이 오래 걸리고 있어요. 잠시 후 다시 시도해주세요."),
        ({"post_json_error": ValueError("bad json")}, "죄송합니다. AI 응답을 처리하는 중 오류가 발생했습니다."),
        ({"post_payload": {}}, "죄송합니다. AI 응답을 처리하는 중 오류가 발생했습니다."),
    ],
)
def test_fallback_resilience_errors_are_user_friendly(make_update_context, post_kwargs, expected_message):
    request = httpx.Request("POST", "http://test/gateway")
    client = FakeClient(stream_error=httpx.RequestError("stream failed", request=request), **post_kwargs)
    update, context = make_update_context(text="복원력 테스트", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert update.message.waiting_message.edits[-1] == expected_message
    assert bot.conversations[123] == []


def test_non_200_fallback_response_is_handled(make_update_context):
    request = httpx.Request("POST", "http://test/chat")
    response = httpx.Response(503, request=request)
    client = FakeClient(
        stream_error=httpx.RequestError("stream failed", request=request),
        post_status_error=httpx.HTTPStatusError("service unavailable", request=request, response=response),
    )
    update, context = make_update_context(text="상태 코드 테스트", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert update.message.waiting_message.edits[-1] == "죄송합니다. AI 서버에서 오류가 발생했습니다. 잠시 후 다시 시도해주세요."
    assert bot.conversations[123] == []


def test_empty_user_input_still_constructs_deterministic_prompt(make_update_context):
    client = FakeClient(stream_lines=[f"data: {json.dumps({'response': '빈 입력 응답'})}", "data: [DONE]"])
    update, context = make_update_context(text="", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert client.stream_calls[0]["json"] == {"prompt": "User: \nAI:"}
    assert bot.conversations[123] == ["User: ", "AI: 빈 입력 응답"]


def test_selected_model_is_included_in_gateway_payloads(make_update_context):
    user_id = 222
    bot.user_selected_models[user_id] = "gpt-4o-mini"
    request = httpx.Request("POST", "http://test/chat")
    client = FakeClient(
        stream_error=httpx.RequestError("stream failed", request=request),
        post_payload={"response": "모델 응답"},
    )
    update, context = make_update_context(user_id=user_id, text="모델 질문", client=client)

    asyncio.run(bot.handle_message(update, context))

    expected_payload = {"prompt": "User: 모델 질문\nAI:", "model": "gpt-4o-mini"}
    assert client.stream_calls[0]["json"] == expected_payload
    assert client.post_calls[0]["json"] == expected_payload


def test_empty_selected_model_falls_back_to_default_gateway_behavior(make_update_context):
    user_id = 223
    bot.user_selected_models[user_id] = ""
    client = FakeClient(stream_lines=[f"data: {json.dumps({'response': '기본 모델 응답'})}", "data: [DONE]"])
    update, context = make_update_context(user_id=user_id, text="기본 질문", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert client.stream_calls[0]["json"] == {"prompt": "User: 기본 질문\nAI:"}


def test_whitespace_selected_model_falls_back_to_default_gateway_behavior(make_update_context):
    user_id = 224
    bot.user_selected_models[user_id] = "   "
    client = FakeClient(stream_lines=[f"data: {json.dumps({'response': '공백 모델 응답'})}", "data: [DONE]"])
    update, context = make_update_context(user_id=user_id, text="공백 질문", client=client)

    asyncio.run(bot.handle_message(update, context))

    assert client.stream_calls[0]["json"] == {"prompt": "User: 공백 질문\nAI:"}


def test_preset_constants_are_defined_centrally():
    assert bot.SUPPORTED_PRESETS == ("normal", "coder", "english", "quant")
    assert bot.DEFAULT_PRESET == "normal"
    assert set(bot.PRESET_PROMPT_PREFIXES.keys()) == set(bot.SUPPORTED_PRESETS)


def test_preset_prefix_string_values_are_fixed():
    assert bot.PRESET_PROMPT_PREFIXES["coder"] == "Preset: coder. Focus on practical coding help."
    assert bot.PRESET_PROMPT_PREFIXES["english"] == "Preset: english. Reply in English unless asked otherwise."
    assert bot.PRESET_PROMPT_PREFIXES["quant"] == "Preset: quant. Prefer quantitative reasoning and clear assumptions."


def test_build_prompt_with_preset_starts_with_coder_prefix():
    prompt = bot.build_prompt_with_preset(["User: hi"], "coder")

    assert prompt.startswith(f"{bot.PRESET_PROMPT_PREFIXES['coder']}\n\n")


def test_build_prompt_with_preset_starts_with_english_prefix():
    prompt = bot.build_prompt_with_preset(["User: hi"], "english")

    assert prompt.startswith(f"{bot.PRESET_PROMPT_PREFIXES['english']}\n\n")


def test_build_prompt_with_preset_starts_with_quant_prefix():
    prompt = bot.build_prompt_with_preset(["User: hi"], "quant")

    assert prompt.startswith(f"{bot.PRESET_PROMPT_PREFIXES['quant']}\n\n")


def test_build_prompt_with_normal_preset_keeps_existing_prompt_format():
    prompt = bot.build_prompt_with_preset(["User: hi"], "normal")

    assert prompt == "User: hi\nAI:"


def test_handle_message_uses_active_preset_prefix_for_non_default_preset(make_update_context):
    user_id = 808
    bot.user_selected_presets[user_id] = "coder"
    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': '코드 중심 응답'})}",
            "data: [DONE]",
        ]
    )

    update, context = make_update_context(user_id=user_id, text="리팩토링 해줘", client=client)
    asyncio.run(bot.handle_message(update, context))

    expected_prompt = (
        f"{bot.PRESET_PROMPT_PREFIXES['coder']}\n\n"
        "User: 리팩토링 해줘\nAI:"
    )
    assert client.stream_calls[0]["json"] == {"prompt": expected_prompt}


def test_invalid_preset_falls_back_to_normal_without_prefix(make_update_context):
    user_id = 909
    bot.user_selected_presets[user_id] = "unsupported"
    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': '기본 응답'})}",
            "data: [DONE]",
        ]
    )

    update, context = make_update_context(user_id=user_id, text="기본 동작", client=client)
    asyncio.run(bot.handle_message(update, context))

    assert bot.resolve_active_preset(user_id) == "normal"
    assert client.stream_calls[0]["json"] == {"prompt": "User: 기본 동작\nAI:"}


def test_preset_is_normalized_before_resolution():
    user_id = 910
    bot.user_selected_presets[user_id] = " Coder "

    assert bot.resolve_active_preset(user_id) == "coder"


def test_setting_english_preset_via_command_applies_to_followup_message(make_update_context):
    user_id = 913
    preset_update, preset_context = make_update_context(
        user_id=user_id,
        text="/preset english",
        client=None,
        args=["english"],
    )

    asyncio.run(bot.preset_command(preset_update, preset_context))

    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': 'Follow-up English reply'})}",
            "data: [DONE]",
        ]
    )
    message_update, message_context = make_update_context(
        user_id=user_id,
        text="Please summarize",
        client=client,
    )

    asyncio.run(bot.handle_message(message_update, message_context))

    expected_prompt = (
        f"{bot.PRESET_PROMPT_PREFIXES['english']}\n\n"
        "User: Please summarize\nAI:"
    )
    assert bot.user_selected_presets[user_id] == "english"
    assert client.stream_calls[0]["json"] == {"prompt": expected_prompt}


def test_english_preset_prefix_is_applied_to_prompt(make_update_context):
    user_id = 911
    bot.user_selected_presets[user_id] = "english"
    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': 'English reply'})}",
            "data: [DONE]",
        ]
    )

    update, context = make_update_context(user_id=user_id, text="Please answer", client=client)
    asyncio.run(bot.handle_message(update, context))

    expected_prompt = (
        f"{bot.PRESET_PROMPT_PREFIXES['english']}\n\n"
        "User: Please answer\nAI:"
    )
    assert client.stream_calls[0]["json"] == {"prompt": expected_prompt}


def test_quant_preset_prefix_is_applied_to_prompt(make_update_context):
    user_id = 912
    bot.user_selected_presets[user_id] = "quant"
    client = FakeClient(
        stream_lines=[
            f"data: {json.dumps({'response': 'Quant reply'})}",
            "data: [DONE]",
        ]
    )

    update, context = make_update_context(user_id=user_id, text="분석해줘", client=client)
    asyncio.run(bot.handle_message(update, context))

    expected_prompt = (
        f"{bot.PRESET_PROMPT_PREFIXES['quant']}\n\n"
        "User: 분석해줘\nAI:"
    )
    assert client.stream_calls[0]["json"] == {"prompt": expected_prompt}
