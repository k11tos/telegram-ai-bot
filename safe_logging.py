"""Fail-closed logging boundary: retain only known, literal event names."""
import logging


class SafeFormatter(logging.Formatter):
    def format(self, record):
        # Never interpolate args or render traceback/exception/stack contents.
        event = "runtime_event"
        if record.name in ("bot", "__main__", "polling_watchdog"):
            if isinstance(record.msg, str):
                candidate = record.msg.split(" ", 1)[0]
                if candidate in SAFE_EVENTS:
                    event = candidate
        return f"{logging.getLevelName(record.levelno) if record.levelno in (10, 20, 30, 40, 50) else 'LOG'} {event}"


# Literal application events only; all parameters (IDs, errors, URLs) are omitted.
SAFE_EVENTS = frozenset({
    "watchdog_initializing", "watchdog_healthy", "watchdog_degraded",
    "watchdog_stalled", "watchdog_unknown", "watchdog_stopping",
    'bot_startup_or_runtime_failed',
    'document_request_rejected_inflight',
    'document_summary_edit_failed',
    'document_summary_http_status_error',
    'document_summary_request_error',
    'http_client_timeout_config',
    'invalid_allowed_telegram_user_id',
    'invalid_obsidian_notification_poll_seconds',
    'invalid_obsidian_wiki_auto_result_timeout_seconds',
    'obsidian_ask_auto_result_poll_failed',
    'obsidian_ask_auto_result_poll_timeout',
    'obsidian_job_create_failed',
    'obsidian_job_result_failed',
    'obsidian_mark_notified_failed',
    'obsidian_notification_missing_chat_id',
    'obsidian_notification_poll_failed',
    'obsidian_notification_poll_missing_http_client',
    'obsidian_notification_poll_started',
    'obsidian_notification_poll_stopped',
    'obsidian_notification_render_failed',
    'obsidian_notification_send_failed',
    'obsidian_notification_unexpected_payload',
    'obsidian_status_failed',
    'preset_load_failed',
    'wiki_permission_denied',
})


def configure_safe_logging():
    root = logging.getLogger()
    if not root.handlers:
        root.addHandler(logging.StreamHandler())
    for handler in root.handlers:
        handler.setFormatter(SafeFormatter())
