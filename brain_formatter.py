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


def _get_change_type(change: Any) -> str:
    if not isinstance(change, dict):
        return ""

    change_type = change.get("type")
    if not isinstance(change_type, str):
        return ""

    return change_type.strip().lower()


def build_brain_change_lines(has_notable_changes: Any, changes: Any) -> list[str]:
    if has_notable_changes is not True or not isinstance(changes, list):
        return []

    lines: list[str] = []
    for change in changes:
        change_type = _get_change_type(change)
        if not change_type:
            continue

        if change_type == "restart_detected":
            service = change.get("service")
            if isinstance(service, str) and service.strip():
                lines.append(f"재시작 감지: {service.strip()}")
                continue
            lines.append("재시작 감지")
            continue

        if change_type == "service_state_change":
            service = change.get("service")
            from_state = change.get("from_state")
            to_state = change.get("to_state")
            if (
                isinstance(service, str)
                and service.strip()
                and isinstance(from_state, str)
                and from_state.strip()
                and isinstance(to_state, str)
                and to_state.strip()
            ):
                lines.append(f"상태 변경: {service.strip()} {from_state.strip()}→{to_state.strip()}")
                continue
            lines.append("상태 변경 감지")
            continue

        if change_type == "docker_summary_change":
            running = change.get("running")
            restarting = change.get("restarting")
            if isinstance(running, int) and isinstance(restarting, int):
                lines.append(f"도커 요약 변화: 실행 {running}, 재시작 {restarting}")
                continue
            lines.append("도커 요약 변화")
            continue

        if change_type == "metric_delta":
            metric = change.get("metric")
            if isinstance(metric, str) and metric.strip():
                lines.append(f"지표 변화: {metric.strip()}")
                continue
            lines.append("리소스/부하 변화")

    return lines



def build_brain_message(
    overall_status: str | None,
    message_lines: Any,
    has_notable_changes: Any = False,
    changes: Any = None,
) -> str:
    change_lines = build_brain_change_lines(has_notable_changes, changes)
    section_lines = "\n".join(f"- {line}" for line in normalize_brain_message_lines(message_lines))

    message_parts = ["📊 오늘 브리핑", "", "[서버]", section_lines, "", "[상태]", format_brain_overall_status(overall_status)]
    if change_lines:
        change_section = "\n".join(f"- {line}" for line in change_lines)
        message_parts.extend(["", "[변화 감지]", change_section])

    return "\n".join(message_parts)



def render_brain_payload(brain_payload: dict[str, Any] | None) -> str:
    payload = brain_payload if isinstance(brain_payload, dict) else {}
    return build_brain_message(
        payload.get("overall_status"),
        payload.get("message_lines"),
        payload.get("has_notable_changes"),
        payload.get("changes"),
    )
