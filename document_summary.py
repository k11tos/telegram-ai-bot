from __future__ import annotations

import httpx

DEFAULT_DOCUMENT_SUMMARY_MODE = "summary"
DOCUMENT_SUMMARY_MODES = ("summary", "bullets", "action", "code")


class DocumentValidationError(Exception):
    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


def is_supported_document(file_name: str | None, supported_extensions: tuple[str, ...]) -> bool:
    if not isinstance(file_name, str):
        return False
    lowered = file_name.lower()
    return any(lowered.endswith(ext) for ext in supported_extensions)


def normalize_document_summary_mode(mode: str | None) -> str | None:
    if not isinstance(mode, str):
        return None
    normalized = mode.strip().lower()
    if normalized in DOCUMENT_SUMMARY_MODES:
        return normalized
    return None


def build_document_summary_prompt(
    file_name: str,
    content: str,
    summary_mode: str = DEFAULT_DOCUMENT_SUMMARY_MODE,
) -> str:
    normalized_mode = normalize_document_summary_mode(summary_mode) or DEFAULT_DOCUMENT_SUMMARY_MODE

    mode_instructions = {
        "summary": (
            "- 핵심 내용을 짧은 단락으로 요약\n"
            "- 전체 요약은 간결하고 명확하게 유지"
        ),
        "bullets": (
            "- 핵심 내용을 5~8개 bullet로 정리\n"
            "- 각 bullet은 한두 문장으로 짧게 유지"
        ),
        "action": (
            "- 실행해야 할 작업(Action Items) 중심으로 정리\n"
            "- 가능한 경우 담당자/기한/우선순위를 함께 표시\n"
            "- 결정사항과 후속 조치를 분리해서 제시"
        ),
        "code": (
            "- 기술/코드 관점에서 핵심 구조와 로직을 설명\n"
            "- 중요한 함수/클래스/설정 포인트를 bullet로 정리\n"
            "- 잠재적 리스크나 개선 포인트를 짧게 포함"
        ),
    }

    return (
        "다음 문서를 한국어로 요약해줘.\n"
        "요구사항:\n"
        f"- 요약 모드: {normalized_mode}\n"
        f"{mode_instructions[normalized_mode]}\n"
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
    summary_mode: str = DEFAULT_DOCUMENT_SUMMARY_MODE,
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
    prompt = build_document_summary_prompt(file_name, text_content, summary_mode)
    response = await client.post(
        chat_path,
        json={"prompt": prompt},
        headers={"X-Request-Id": request_id},
    )
    response.raise_for_status()
    return response.json()["response"]
