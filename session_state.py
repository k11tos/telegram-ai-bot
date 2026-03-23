from __future__ import annotations


def normalize_session_name(raw_name: str) -> str:
    import bot as bot_module

    normalized = raw_name.strip()
    if not normalized:
        return bot_module.DEFAULT_SESSION_NAME
    return normalized[:32]


def get_active_session_name(user_id: int) -> str:
    import bot as bot_module

    session_name = bot_module.user_active_sessions.get(user_id)
    if not isinstance(session_name, str):
        return bot_module.DEFAULT_SESSION_NAME
    return normalize_session_name(session_name)


def ensure_user_sessions(user_id: int) -> dict[str, list[str]]:
    import bot as bot_module

    raw_per_session = bot_module.conversations.get(user_id)
    if isinstance(raw_per_session, dict):
        per_session = raw_per_session
    elif isinstance(raw_per_session, list):
        per_session = {
            bot_module.DEFAULT_SESSION_NAME: [
                line for line in raw_per_session if isinstance(line, str)
            ]
        }
        bot_module.conversations[user_id] = per_session
    else:
        per_session = {}
        bot_module.conversations[user_id] = per_session

    for session_name, history in list(per_session.items()):
        if not isinstance(session_name, str) or not isinstance(history, list):
            per_session.pop(session_name, None)
            continue

        normalized_name = normalize_session_name(session_name)
        cleaned_history = [line for line in history if isinstance(line, str)][
            -bot_module.MAX_HISTORY :
        ]
        if normalized_name != session_name:
            per_session.pop(session_name, None)
        per_session[normalized_name] = cleaned_history

    return per_session


def get_session_history(user_id: int, session_name: str | None = None) -> list[str]:
    active_session = normalize_session_name(
        session_name or get_active_session_name(user_id)
    )
    per_session = ensure_user_sessions(user_id)
    return per_session.setdefault(active_session, [])
