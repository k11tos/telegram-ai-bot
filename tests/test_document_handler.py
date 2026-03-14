import asyncio
from types import SimpleNamespace

import httpx

import bot
from tests.test_message_handler import FakeClient


class FakeTelegramFile:
    def __init__(self, content: bytes):
        self._content = content

    async def download_as_bytearray(self):
        return bytearray(self._content)


class FakeBotAPI:
    def __init__(self, file_obj=None, file_error=None):
        self.file_obj = file_obj
        self.file_error = file_error

    async def get_file(self, file_id):
        if self.file_error is not None:
            raise self.file_error
        return self.file_obj


def make_document_update_context(make_update_context, *, file_name, file_size, file_id="f1", content=b"", client=None):
    update, context = make_update_context(text=None, client=client)
    update.message.document = SimpleNamespace(file_name=file_name, file_size=file_size, file_id=file_id)
    context.bot = FakeBotAPI(file_obj=FakeTelegramFile(content))
    return update, context


def test_handle_document_rejects_unsupported_extension(make_update_context):
    update, context = make_document_update_context(
        make_update_context,
        file_name="paper.pdf",
        file_size=100,
        content=b"hello",
        client=FakeClient(post_payload={"response": "unused"}),
    )

    asyncio.run(bot.handle_document(update, context))

    assert update.message.replies[-1] == "지원하지 않는 파일 형식입니다. .txt 또는 .md 파일만 업로드해주세요."


def test_handle_document_rejects_large_file_by_metadata(make_update_context):
    update, context = make_document_update_context(
        make_update_context,
        file_name="note.txt",
        file_size=bot.MAX_DOCUMENT_BYTES + 1,
        content=b"x",
        client=FakeClient(post_payload={"response": "unused"}),
    )

    asyncio.run(bot.handle_document(update, context))

    assert update.message.replies[-1] == f"파일이 너무 큽니다. 최대 {bot.MAX_DOCUMENT_BYTES}바이트까지 처리할 수 있어요."


def test_handle_document_rejects_invalid_utf8(make_update_context):
    update, context = make_document_update_context(
        make_update_context,
        file_name="note.md",
        file_size=10,
        content=b"\xff\xfe\x00",
        client=FakeClient(post_payload={"response": "unused"}),
    )

    asyncio.run(bot.handle_document(update, context))

    assert update.message.waiting_message.edits[-1] == "UTF-8 텍스트 파일만 처리할 수 있어요. 인코딩을 확인한 뒤 다시 업로드해주세요."


def test_handle_document_summarizes_supported_file(make_update_context):
    client = FakeClient(post_payload={"response": "- 핵심 요약"})
    update, context = make_document_update_context(
        make_update_context,
        file_name="readme.md",
        file_size=30,
        content="테스트 문서 내용".encode("utf-8"),
        client=client,
    )

    asyncio.run(bot.handle_document(update, context))

    assert update.message.replies[0] == "파일을 읽고 요약 중…"
    assert update.message.waiting_message.edits[-1] == "- 핵심 요약"
    assert len(client.post_calls) == 1
    assert client.post_calls[0]["path"] == bot.AI_GATEWAY_CHAT_PATH
    assert "한국어로 간결하게 요약" in client.post_calls[0]["json"]["prompt"]


def test_handle_document_cleanly_handles_gateway_error(make_update_context):
    request = httpx.Request("POST", "http://test/chat")
    client = FakeClient(post_error=httpx.RequestError("network", request=request))
    update, context = make_document_update_context(
        make_update_context,
        file_name="doc.txt",
        file_size=20,
        content=b"hello",
        client=client,
    )

    asyncio.run(bot.handle_document(update, context))

    assert update.message.waiting_message.edits[-1] == "문서 요약 중 오류가 발생했어요. 잠시 후 다시 시도해주세요."


class DelayedTelegramFile(FakeTelegramFile):
    def __init__(self, gate, content: bytes):
        super().__init__(content)
        self._gate = gate

    async def download_as_bytearray(self):
        await self._gate.wait()
        return await super().download_as_bytearray()


def test_handle_document_truncates_prompt_content(make_update_context):
    long_text = "가" * (bot.MAX_DOCUMENT_PROMPT_CHARS + 50)
    client = FakeClient(post_payload={"response": "요약"})
    update, context = make_document_update_context(
        make_update_context,
        file_name="long.txt",
        file_size=len(long_text.encode("utf-8")),
        content=long_text.encode("utf-8"),
        client=client,
    )

    asyncio.run(bot.handle_document(update, context))

    prompt = client.post_calls[0]["json"]["prompt"]
    assert ("가" * bot.MAX_DOCUMENT_PROMPT_CHARS) in prompt
    assert ("가" * (bot.MAX_DOCUMENT_PROMPT_CHARS + 1)) not in prompt


def test_handle_document_rejects_inflight_requests_same_user(make_update_context):
    async def scenario():
        gate = asyncio.Event()
        slow_content = b"hello"
        first_update, first_context = make_update_context(user_id=123, text=None, client=FakeClient(post_payload={"response": "ok"}))
        first_update.message.document = SimpleNamespace(file_name="a.txt", file_size=5, file_id="f1")
        first_context.bot = FakeBotAPI(file_obj=DelayedTelegramFile(gate, slow_content))

        first_task = asyncio.create_task(bot.handle_document(first_update, first_context))

        # Ensure first request entered in-flight state.
        while not bot.user_in_flight_requests.get(123, False):
            await asyncio.sleep(0)

        second_update, second_context = make_document_update_context(
            make_update_context,
            file_name="b.txt",
            file_size=5,
            content=b"world",
            client=FakeClient(post_payload={"response": "ok2"}),
        )
        await bot.handle_document(second_update, second_context)

        assert second_update.message.replies[-1] == "이전 요청을 처리 중입니다. 잠시 후 다시 보내주세요."

        gate.set()
        await first_task

    asyncio.run(scenario())
