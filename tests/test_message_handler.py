import asyncio
import json

import httpx

import bot


class FakeStreamResponse:
    def __init__(self, lines):
        self._lines = lines

    async def __aenter__(self):
        return self

    async def __aexit__(self, exc_type, exc, tb):
        return False

    def raise_for_status(self):
        return None

    async def aiter_lines(self):
        for line in self._lines:
            yield line


class FakePostResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        return None

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, stream_lines=None, stream_error=None, post_payload=None, post_error=None):
        self.stream_lines = stream_lines or []
        self.stream_error = stream_error
        self.post_payload = post_payload
        self.post_error = post_error
        self.stream_calls = []
        self.post_calls = []

    def stream(self, method, path, json=None, headers=None):
        self.stream_calls.append({"method": method, "path": path, "json": json, "headers": headers})
        if self.stream_error is not None:
            raise self.stream_error
        return FakeStreamResponse(self.stream_lines)

    async def post(self, path, json=None, headers=None):
        self.post_calls.append({"path": path, "json": json, "headers": headers})
        if self.post_error is not None:
            raise self.post_error
        return FakePostResponse(self.post_payload)


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
