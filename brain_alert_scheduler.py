import asyncio
import logging
from datetime import datetime
from typing import Awaitable, Callable


DEFAULT_BRAIN_ALERT_POLL_INTERVAL_SECONDS = 60
DEFAULT_BRAIN_ALERT_SCHEDULE_HOUR_LOCAL = 9


class BrainAlertScheduler:
    def __init__(
        self,
        user_brain_alert_modes: dict[int, str],
        last_sent_windows: dict[int, str],
        send_alert_for_user: Callable[[int, str], Awaitable[bool]],
        logger: logging.Logger,
        poll_interval_seconds: float = DEFAULT_BRAIN_ALERT_POLL_INTERVAL_SECONDS,
        schedule_hour_local: int = DEFAULT_BRAIN_ALERT_SCHEDULE_HOUR_LOCAL,
        now_func: Callable[[], datetime] | None = None,
    ) -> None:
        self._user_brain_alert_modes = user_brain_alert_modes
        self._last_sent_windows = last_sent_windows
        self._send_alert_for_user = send_alert_for_user
        self._logger = logger
        self._poll_interval_seconds = max(1.0, float(poll_interval_seconds))
        self._schedule_hour_local = max(0, min(23, int(schedule_hour_local)))
        self._now_func = now_func or (lambda: datetime.now().astimezone())

        self._stop_event = asyncio.Event()
        self._task: asyncio.Task | None = None

    @property
    def task(self) -> asyncio.Task | None:
        return self._task

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return

        self._stop_event = asyncio.Event()
        self._task = asyncio.create_task(self._run_loop())

    async def stop(self) -> None:
        self._stop_event.set()
        if self._task is None:
            return

        self._task.cancel()
        try:
            await self._task
        except asyncio.CancelledError:
            pass

    async def run_once(self) -> None:
        now = self._now_func()
        if now.hour != self._schedule_hour_local:
            return

        window_key = now.date().isoformat()
        for user_id, mode in list(self._user_brain_alert_modes.items()):
            if mode not in {"notable", "all"}:
                continue

            if self._last_sent_windows.get(user_id) == window_key:
                continue

            try:
                sent = await self._send_alert_for_user(user_id, mode)
            except Exception as error:  # pragma: no cover - safety guard
                self._logger.warning(
                    "brain_alert_schedule_send_failed user_id=%s mode=%s error=%s",
                    user_id,
                    mode,
                    error,
                )
                continue

            if sent:
                self._last_sent_windows[user_id] = window_key

    async def _run_loop(self) -> None:
        self._logger.info(
            "brain_alert_schedule_loop_started poll_interval=%s schedule_hour_local=%s",
            self._poll_interval_seconds,
            self._schedule_hour_local,
        )
        try:
            while not self._stop_event.is_set():
                await self.run_once()
                try:
                    await asyncio.wait_for(
                        self._stop_event.wait(), timeout=self._poll_interval_seconds
                    )
                except asyncio.TimeoutError:
                    continue
        except asyncio.CancelledError:
            raise
        finally:
            self._logger.info("brain_alert_schedule_loop_stopped")
