import asyncio

from commands.sessions import SessionCommandDependencies, build_session_handlers
from session_state import (
    ensure_user_sessions,
    get_active_session_name,
    get_session_history,
    normalize_session_name,
)


def test_session_state_helpers_mutate_explicit_live_state_not_module_globals():
    conversations = {7: ["User: hi", "AI: hello", 123]}
    active_sessions = {7: " work "}

    history = get_session_history(
        7,
        conversations,
        active_sessions,
        default_session_name="default",
        max_history=10,
    )

    assert history == []
    assert conversations == {7: {"default": ["User: hi", "AI: hello"], "work": []}}
    assert get_active_session_name(7, active_sessions, "default") == "work"
    assert normalize_session_name("  ", "default") == "default"


def test_built_session_handlers_use_supplied_runtime_dependencies(make_update_context):
    live_conversations = {}
    live_active_sessions = {}
    lock_map = {}
    save_calls = []
    reset_calls = []

    def get_lock(user_id: int):
        return lock_map.setdefault(user_id, asyncio.Lock())

    def save_state():
        save_calls.append("saved")

    def increment_reset_token(user_id: int, session_name: str) -> int:
        reset_calls.append((user_id, session_name))
        return len(reset_calls)

    def normalize(name: str) -> str:
        return normalize_session_name(name, "default")

    def get_active(user_id: int) -> str:
        return get_active_session_name(user_id, live_active_sessions, "default")

    def ensure_sessions(user_id: int) -> dict[str, list[str]]:
        return ensure_user_sessions(user_id, live_conversations, "default", 10)

    def get_history(user_id: int, session_name: str | None = None) -> list[str]:
        return get_session_history(
            user_id,
            live_conversations,
            live_active_sessions,
            "default",
            10,
            session_name,
        )

    handlers = build_session_handlers(
        SessionCommandDependencies(
            default_session_name="default",
            user_active_sessions=live_active_sessions,
            get_user_lock=get_lock,
            save_bot_state=save_state,
            increment_session_reset_token=increment_reset_token,
            normalize_session_name=normalize,
            get_active_session_name=get_active,
            ensure_user_sessions=ensure_sessions,
            get_session_history=get_history,
        )
    )

    update, context = make_update_context(
        user_id=88,
        text="/session work",
        client=None,
        args=["work"],
    )

    asyncio.run(handlers.session_command(update, context))

    assert live_active_sessions[88] == "work"
    assert live_conversations == {88: {"work": []}}
    assert save_calls == ["saved"]
    assert reset_calls == []
    assert update.message.replies[-1] == "세션 변경: work"
