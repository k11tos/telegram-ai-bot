import bot


def test_split_short_message_returns_single_chunk():
    text = "짧은 메시지"

    assert bot.split_telegram_text(text, limit=4000) == [text]


def test_split_exactly_limit_returns_single_chunk():
    text = "a" * 4000

    assert bot.split_telegram_text(text, limit=4000) == [text]


def test_split_large_paragraph_preserves_content_and_limit():
    text = ("문단 하나입니다. " * 500) + "\n\n" + ("다음 문단입니다. " * 500)

    chunks = bot.split_telegram_text(text, limit=4000)

    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks) == text


def test_split_code_block_text_preserves_content_and_limit():
    code_line = "print('hello world')\n"
    text = "```python\n" + (code_line * 600) + "```"

    chunks = bot.split_telegram_text(text, limit=4000)

    assert len(chunks) > 1
    assert all(len(chunk) <= 4000 for chunk in chunks)
    assert "".join(chunks) == text


def test_split_leading_delimiters_does_not_create_empty_chunks():
    text = "\n\n" + (" 앞부분 공백 포함 텍스트" * 260)

    chunks = bot.split_telegram_text(text, limit=120)

    assert len(chunks) > 1
    assert all(chunks)
    assert all(len(chunk) <= 120 for chunk in chunks)
    assert "".join(chunks) == text
