from __future__ import annotations


def normalize_session_name(raw_name: str, default_session_name: str) -> str:
    normalized = raw_name.strip()
    if not normalized:
        return default_session_name
    return normalized[:32]


def get_active_session_name(
    user_id: int,
    user_active_sessions: dict[int, str],
    default_session_name: str,
) -> str:
    session_name = user_active_sessions.get(user_id)
    if not isinstance(session_name, str):
        return default_session_name
    return normalize_session_name(session_name, default_session_name)


def ensure_user_sessions(
    user_id: int,
    conversations: dict[int, dict[str, list[str]] | list[str]],
    default_session_name: str,
    max_history: int,
) -> dict[str, list[str]]:
    raw_per_session = conversations.get(user_id)
    if isinstance(raw_per_session, dict):
        per_session = raw_per_session
    elif isinstance(raw_per_session, list):
        per_session = {
            default_session_name: [
                line for line in raw_per_session if isinstance(line, str)
            ]
        }
        conversations[user_id] = per_session
    else:
        per_session = {}
        conversations[user_id] = per_session

    for session_name, history in list(per_session.items()):
        if not isinstance(session_name, str) or not isinstance(history, list):
            per_session.pop(session_name, None)
            continue

        normalized_name = normalize_session_name(session_name, default_session_name)
        cleaned_history = [line for line in history if isinstance(line, str)][
            -max_history:
        ]
        if normalized_name != session_name:
            per_session.pop(session_name, None)
        per_session[normalized_name] = cleaned_history

    return per_session


def get_session_history(
    user_id: int,
    conversations: dict[int, dict[str, list[str]] | list[str]],
    user_active_sessions: dict[int, str],
    default_session_name: str,
    max_history: int,
    session_name: str | None = None,
) -> list[str]:
    active_session = normalize_session_name(
        session_name
        or get_active_session_name(
            user_id,
            user_active_sessions,
            default_session_name,
        ),
        default_session_name,
    )
    per_session = ensure_user_sessions(
        user_id,
        conversations,
        default_session_name,
        max_history,
    )
    return per_session.setdefault(active_session, [])
