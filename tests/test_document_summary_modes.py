from document_summary import build_document_summary_prompt


def test_build_document_summary_prompt_defaults_to_summary_mode():
    prompt = build_document_summary_prompt("a.txt", "내용")

    assert "- 요약 모드: summary" in prompt
    assert "핵심 내용을 짧은 단락" in prompt


def test_build_document_summary_prompt_varies_by_mode():
    bullets_prompt = build_document_summary_prompt("a.txt", "내용", "bullets")
    action_prompt = build_document_summary_prompt("a.txt", "내용", "action")
    code_prompt = build_document_summary_prompt("a.txt", "내용", "code")

    assert "- 요약 모드: bullets" in bullets_prompt
    assert "5~8개 bullet" in bullets_prompt

    assert "- 요약 모드: action" in action_prompt
    assert "실행해야 할 작업(Action Items)" in action_prompt

    assert "- 요약 모드: code" in code_prompt
    assert "기술/코드 관점" in code_prompt
