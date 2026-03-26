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


DEFAULT_DOCUMENT_SUMMARY_MODE = "summary"
SUPPORTED_DOCUMENT_SUMMARY_MODES = ("summary", "bullets", "action", "code")
DOCUMENT_SUMMARY_MODE_INSTRUCTIONS = {
    "summary": [
        "- 전체 맥락을 짧고 명확한 문단 1~2개로 설명",
        "- 중요한 배경/결론을 빠뜨리지 말 것",
    ],
    "bullets": [
        "- 핵심 내용을 5개 내외 bullet로 정리",
        "- 각 bullet은 한 줄 위주로 간결하게 작성",
    ],
    "action": [
        "- 실행이 필요한 항목(Action items)만 bullet로 정리",
        "- 각 항목에 담당/기한이 있으면 함께 표기, 없으면 '미정'으로 표시",
    ],
    "code": [
        "- 코드/설정/명령어 관련 내용 중심으로 요약",
        "- 필요 시 짧은 코드 블록이나 명령어 예시를 포함",
    ],
}


def normalize_document_summary_mode(mode: str | None) -> str:
    if not isinstance(mode, str):
        return DEFAULT_DOCUMENT_SUMMARY_MODE
    normalized = mode.strip().lower()
    if normalized in SUPPORTED_DOCUMENT_SUMMARY_MODES:
        return normalized
    return DEFAULT_DOCUMENT_SUMMARY_MODE


def build_document_summary_prompt(file_name: str, content: str, mode: str | None = None) -> str:
    normalized_mode = normalize_document_summary_mode(mode)
    instructions = DOCUMENT_SUMMARY_MODE_INSTRUCTIONS[normalized_mode]
    requirements = "\n".join(instructions)
    return (
        "다음 문서를 한국어로 요약해줘.\n"
        f"요약 모드: {normalized_mode}\n"
        "요구사항:\n"
        f"{requirements}\n"
        "- 모드 지시사항을 우선으로 따를 것\n"
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
    mode: str | None = None,
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
    prompt = build_document_summary_prompt(file_name, text_content, mode=mode)
    response = await client.post(
        chat_path,
        json={"prompt": prompt},
        headers={"X-Request-Id": request_id},
    )
    response.raise_for_status()
    return response.json()["response"]
