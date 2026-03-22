from typing import Any

BRAIN_STATUS_LABELS = {
    "ok": "✅ 안정",
    "partial": "⚠️ 일부 정보 누락",
    "warning": "🚨 점검 필요",
}
BRAIN_STATUS_FALLBACK = "⚠️ 일부 정보 확인 불가"
BRAIN_MESSAGE_LINES_FALLBACK = ["브리핑 세부 정보가 아직 없어요."]


def format_brain_overall_status(overall_status: str | None) -> str:
    normalized_status = overall_status.strip().lower() if isinstance(overall_status, str) else ""
    return BRAIN_STATUS_LABELS.get(normalized_status, BRAIN_STATUS_FALLBACK)



def normalize_brain_message_lines(message_lines: Any) -> list[str]:
    if not isinstance(message_lines, list):
        return BRAIN_MESSAGE_LINES_FALLBACK.copy()

    normalized_lines = [line.strip() for line in message_lines if isinstance(line, str) and line.strip()]
    if normalized_lines:
        return normalized_lines

    return BRAIN_MESSAGE_LINES_FALLBACK.copy()



def build_brain_message(overall_status: str | None, message_lines: Any) -> str:
    section_lines = "\n".join(f"- {line}" for line in normalize_brain_message_lines(message_lines))

    return "\n".join(
        [
            "📊 오늘 브리핑",
            "",
            "[서버]",
            section_lines,
            "",
            "[상태]",
            format_brain_overall_status(overall_status),
        ]
    )



def render_brain_payload(brain_payload: dict[str, Any] | None) -> str:
    payload = brain_payload if isinstance(brain_payload, dict) else {}
    return build_brain_message(payload.get("overall_status"), payload.get("message_lines"))
