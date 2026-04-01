from brain_formatter import build_brain_change_lines


def test_build_brain_change_lines_skips_non_notable_metric_decrease():
    lines = build_brain_change_lines(
        True,
        [
            {
                "kind": "metric_delta",
                "field": "memory_percent",
                "previous": 78.2,
                "current": 64.1,
                "notable": False,
            }
        ],
    )

    assert lines == []


def test_build_brain_change_lines_includes_notable_metric_increase():
    lines = build_brain_change_lines(
        True,
        [
            {
                "kind": "metric_delta",
                "field": "memory_percent",
                "previous": 64.1,
                "current": 78.2,
                "notable": True,
            }
        ],
    )

    assert lines == ["지표 변화: 메모리 사용률 64.1%→78.2%"]


def test_build_brain_change_lines_keeps_notable_restart_regression_and_docker_worsening():
    lines = build_brain_change_lines(
        True,
        [
            {
                "kind": "restart_detected",
                "field": "service_states.ai-gateway",
                "notable": True,
            },
            {
                "kind": "service_state_change",
                "field": "service_states.worker",
                "previous": "healthy",
                "current": "degraded",
                "notable": True,
            },
            {
                "kind": "docker_summary_change",
                "field": "docker_summary",
                "previous": {"running": 7, "stopped": 0},
                "current": {"running": 6, "stopped": 1},
                "notable": True,
            },
        ],
    )

    assert "재시작 감지: ai-gateway" in lines
    assert "상태 변경: worker healthy→degraded" in lines
    assert "도커 요약 변화: 실행 7→6, 중지 0→1" in lines
