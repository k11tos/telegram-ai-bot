from __future__ import annotations

import shlex
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Callable

from telegram import Update
from telegram.ext import ContextTypes


@dataclass(frozen=True)
class SessionCommandDependencies:
    default_session_name: str
    user_active_sessions: dict[int, str]
    get_user_lock: Callable[[int], object]
    save_bot_state: Callable[[], None]
    increment_session_reset_token: Callable[[int, str], int]
    normalize_session_name: Callable[[str], str]
    get_active_session_name: Callable[[int], str]
    ensure_user_sessions: Callable[[int], dict[str, list[str]]]
    get_session_history: Callable[[int, str | None], list[str]]


def build_session_handlers(
    deps: SessionCommandDependencies,
) -> SimpleNamespace:
    async def session_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        requested_session = " ".join(context.args).strip() if context.args else ""

        if not requested_session:
            active_session = deps.get_active_session_name(user_id)
            per_session = deps.ensure_user_sessions(user_id)
            per_session.setdefault(
                active_session,
                deps.get_session_history(user_id, active_session),
            )
            session_names = sorted(per_session.keys())
            available_sessions_lines = "\n".join(f"- {name}" for name in session_names)
            if not available_sessions_lines:
                available_sessions_lines = "- (none)"

            await update.message.reply_text(
                "\n".join(
                    [
                        f"현재 세션: {active_session}",
                        f"전체 세션 수: {len(session_names)}",
                        "",
                        "보유한 세션:",
                        available_sessions_lines,
                    ]
                )
            )
            return

        next_session = deps.normalize_session_name(requested_session)
        lock = deps.get_user_lock(user_id)
        async with lock:
            deps.user_active_sessions[user_id] = next_session
            deps.get_session_history(user_id, next_session)
            deps.save_bot_state()
        await update.message.reply_text(f"세션 변경: {next_session}")

    async def sessions_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        active_session = deps.get_active_session_name(user_id)
        session_names = sorted(deps.ensure_user_sessions(user_id).keys())

        available_sessions_lines = "\n".join(f"- {name}" for name in session_names)
        if not available_sessions_lines:
            available_sessions_lines = "- (none)"

        await update.message.reply_text(
            "\n".join(
                [
                    f"현재 세션: {active_session}",
                    "",
                    "보유한 세션 목록:",
                    available_sessions_lines,
                ]
            )
        )

    async def session_rename_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = update.effective_user.id

        raw_text = (
            update.message.text
            if update.message and isinstance(update.message.text, str)
            else ""
        )
        args_text = raw_text.partition(" ")[2].strip()

        parsed_args: list[str] = []
        if args_text:
            try:
                parsed_args = shlex.split(args_text)
            except ValueError:
                parsed_args = []

        if len(parsed_args) >= 2:
            old_name, new_name = parsed_args[0], parsed_args[1]
        elif len(context.args) >= 2:
            old_name, new_name = context.args[0], context.args[1]
        else:
            await update.message.reply_text(
                "기존 세션 이름과 새 세션 이름을 모두 입력해주세요."
            )
            return

        old_session = deps.normalize_session_name(old_name)
        new_session = deps.normalize_session_name(new_name)

        if old_session == new_session:
            await update.message.reply_text(
                "변경 전/후 세션 이름이 같아요. 다른 이름을 입력해주세요."
            )
            return

        if old_session == deps.default_session_name:
            await update.message.reply_text("기본 세션 이름은 변경할 수 없어요.")
            return

        if new_session == deps.default_session_name:
            await update.message.reply_text("기본 세션 이름으로는 변경할 수 없어요.")
            return

        renamed = False
        duplicate_name = False
        active_session = deps.get_active_session_name(user_id)
        lock = deps.get_user_lock(user_id)
        async with lock:
            per_session = deps.ensure_user_sessions(user_id)
            if old_session not in per_session:
                pass
            elif new_session in per_session:
                duplicate_name = True
            else:
                per_session[new_session] = per_session.pop(old_session)
                if active_session == old_session:
                    deps.user_active_sessions[user_id] = new_session
                deps.save_bot_state()
                renamed = True

        if duplicate_name:
            await update.message.reply_text(
                f"이미 존재하는 세션 이름이에요: {new_session}"
            )
            return

        if not renamed:
            await update.message.reply_text(f"세션을 찾을 수 없어요: {old_session}")
            return

        await update.message.reply_text(
            f"세션 이름이 변경되었습니다: {old_session} → {new_session}"
        )

    async def session_delete_command(
        update: Update, context: ContextTypes.DEFAULT_TYPE
    ):
        user_id = update.effective_user.id
        requested_session = " ".join(context.args).strip() if context.args else ""

        if not requested_session:
            await update.message.reply_text("삭제할 세션 이름을 입력해주세요.")
            return

        target_session = deps.normalize_session_name(requested_session)
        active_session = deps.get_active_session_name(user_id)

        if target_session == active_session:
            await update.message.reply_text("현재 사용 중인 세션은 삭제할 수 없어요.")
            return

        if target_session == deps.default_session_name:
            await update.message.reply_text("기본 세션은 삭제할 수 없어요.")
            return

        deleted = False
        lock = deps.get_user_lock(user_id)
        async with lock:
            per_session = deps.ensure_user_sessions(user_id)
            if target_session in per_session:
                per_session.pop(target_session, None)
                deps.save_bot_state()
                deleted = True

        if not deleted:
            await update.message.reply_text(f"세션을 찾을 수 없어요: {target_session}")
            return

        await update.message.reply_text(f"세션이 삭제되었습니다: {target_session}")

    async def session_clear_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        user_id = update.effective_user.id
        requested_session = " ".join(context.args).strip() if context.args else ""

        if not requested_session:
            await update.message.reply_text("비울 세션 이름을 입력해주세요.")
            return

        target_session = deps.normalize_session_name(requested_session)

        cleared = False
        lock = deps.get_user_lock(user_id)
        async with lock:
            per_session = deps.ensure_user_sessions(user_id)
            if target_session in per_session:
                per_session[target_session] = []
                if target_session == deps.get_active_session_name(user_id):
                    deps.increment_session_reset_token(user_id, target_session)
                deps.save_bot_state()
                cleared = True

        if not cleared:
            await update.message.reply_text(f"세션을 찾을 수 없어요: {target_session}")
            return

        await update.message.reply_text(f"세션 기록을 비웠습니다: {target_session}")

    return SimpleNamespace(
        session_command=session_command,
        sessions_command=sessions_command,
        session_rename_command=session_rename_command,
        session_delete_command=session_delete_command,
        session_clear_command=session_clear_command,
    )
