from __future__ import annotations

import httpx


class DocumentValidationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def is_supported_document(file_name: str | None, supported_extensions: tuple[str, ...]) -> bool:
    if not isinstance(file_name, str):
        return False
    lowered = file_name.lower()
    return any(lowered.endswith(ext) for ext in supported_extensions)


def build_document_summary_prompt(file_name: str, content: str) -> str:
    return (
        "다음 문서를 한국어로 간결하게 요약해줘.\n"
        "요구사항:\n"
        "- 핵심 내용을 3~5개 bullet로 정리\n"
        "- 전체 요약은 짧고 명확하게 유지\n"
        f"- 파일명: {file_name}\n\n"
        f"문서 원문:\n{content}"
    )


async def summarize_document_text(
    *,
    document,
    telegram_bot,
    client: httpx.AsyncClient,
    request_id: str,
    chat_path: str,
    max_document_bytes: int,
    max_document_prompt_chars: int,
) -> str:
    file_name = document.file_name or "unknown"
    telegram_file = await telegram_bot.get_file(document.file_id)
    file_bytes = bytes(await telegram_file.download_as_bytearray())

    if len(file_bytes) > max_document_bytes:
        raise DocumentValidationError(
            f"파일이 너무 큽니다. 최대 {max_document_bytes}바이트까지 처리할 수 있어요."
        )

    try:
        text_content = file_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise DocumentValidationError(
            "UTF-8 텍스트 파일만 처리할 수 있어요. 인코딩을 확인한 뒤 다시 업로드해주세요."
        ) from error

    text_content = text_content[:max_document_prompt_chars]
    prompt = build_document_summary_prompt(file_name, text_content)
    response = await client.post(
        chat_path,
        json={"prompt": prompt},
        headers={"X-Request-Id": request_id},
    )
    response.raise_for_status()
    return response.json()["response"]
