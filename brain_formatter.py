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

    restart_services: list[str] = []
    service_transitions: list[str] = []
    has_service_state_fallback = False
    docker_summary_line: str | None = None
    metric_changes: list[str] = []
    has_metric_fallback = False

    for change in changes:
        change_type = _get_change_type(change)
        if not change_type:
            continue

        if change_type == "restart_detected":
            service = change.get("service")
            if isinstance(service, str) and service.strip():
                restart_services.append(service.strip())
            else:
                restart_services.append("")
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
                service_transitions.append(
                    f"{service.strip()} {from_state.strip()}→{to_state.strip()}"
                )
            else:
                has_service_state_fallback = True
            continue

        if change_type == "docker_summary_change" and docker_summary_line is None:
            running = change.get("running")
            restarting = change.get("restarting")
            if isinstance(running, int) and isinstance(restarting, int):
                docker_summary_line = f"도커 요약 변화: 실행 {running}, 재시작 {restarting}"
            else:
                docker_summary_line = "도커 요약 변화"
            continue

        if change_type == "metric_delta":
            metric = change.get("metric")
            if isinstance(metric, str) and metric.strip():
                metric_value = metric.strip()
                if metric_value not in metric_changes:
                    metric_changes.append(metric_value)
            else:
                has_metric_fallback = True

    lines: list[str] = []
    if restart_services:
        restart_targets = [service for service in restart_services if service]
        if restart_targets:
            lines.append(f"재시작 감지: {', '.join(restart_targets)}")
        else:
            lines.append("재시작 감지")

    if service_transitions or has_service_state_fallback:
        transition_snippet = ", ".join(service_transitions[:3])
        if service_transitions and len(service_transitions) > 3:
            transition_snippet = f"{transition_snippet} 외 {len(service_transitions) - 3}건"

        if transition_snippet and has_service_state_fallback:
            lines.append(f"상태 변경: {transition_snippet} (+추가 변경)")
        elif transition_snippet:
            lines.append(f"상태 변경: {transition_snippet}")
        else:
            lines.append("상태 변경 감지")

    if docker_summary_line:
        lines.append(docker_summary_line)

    if metric_changes or has_metric_fallback:
        metric_snippet = ", ".join(metric_changes[:2])
        if metric_changes and len(metric_changes) > 2:
            metric_snippet = f"{metric_snippet} 외 {len(metric_changes) - 2}건"

        if metric_snippet:
            lines.append(f"지표 변화: {metric_snippet}")
        else:
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
