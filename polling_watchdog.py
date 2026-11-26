"""Opt-in polling liveness for PTB 22.8; no readiness or host management."""
import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import math
import os
import socket
import time

from telegram.error import InvalidToken, RetryAfter, TelegramError, TimedOut
from telegram.ext import ExtBot


@dataclass(frozen=True)
class NotifyConfig:
    address: str
    interval: float

    @classmethod
    def from_env(cls, env=None, pid=None):
        env = os.environ if env is None else env
        pid = os.getpid() if pid is None else pid
        address = env.get("NOTIFY_SOCKET", "")
        try:
            usec = int(env.get("WATCHDOG_USEC", "0"))
            expected = int(env.get("WATCHDOG_PID", str(pid)))
            interval = usec / 1_000_000
        except (ValueError, OverflowError, TypeError):
            return None
        if (usec <= 0 or not math.isfinite(interval) or expected != pid
                or len(address) < 2 or address[0] not in ("/", "@")
                or "\0" in address or len(os.fsencode(address)) > 107):
            return None
        return cls("\0" + address[1:] if address[0] == "@" else address, interval)

    @property
    def cadence(self):
        return self.interval / 3


def notify(config, socket_factory=socket.socket):
    """Nonblocking and fail closed; never log socket paths or OS exceptions."""
    try:
        with socket_factory(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
            sock.setblocking(False)
            return sock.sendto(b"WATCHDOG=1", config.address) == 10
    except (OSError, ValueError):
        return False


class PollProgress:
    """Finite local deadlines, not freshness of the last successful API response."""
    def __init__(self, clock=time.monotonic, jitter=5.0):
        self.clock = clock
        self.jitter = jitter
        self.phase = "initializing"
        self.deadline = None
        self.completed = False
        self.degraded = False
        self.stopping = False

    def begin(self, budget):
        if not self.stopping:
            self.phase = "polling"
            self.deadline = self.clock() + budget + self.jitter

    def finish(self, wait=0.0, degraded=False):
        if not self.stopping:
            self.completed = True
            self.degraded = degraded
            self.phase = "degraded" if degraded else "healthy"
            self.deadline = self.clock() + wait + self.jitter

    def stop(self, phase="stopping"):
        self.stopping = True
        self.phase = phase

    def status(self):
        if self.stopping:
            return self.phase
        if self.deadline is not None and self.clock() > self.deadline:
            return "stalled"
        if not self.completed:
            return "initializing"
        return "degraded" if self.degraded else "healthy"

    def can_notify(self):
        return self.status() in ("healthy", "degraded")


class PollWatchdog:
    def __init__(self, config, progress=None, sender=notify):
        self.config = config
        self.progress = progress if progress is not None else PollProgress()
        self.sender = sender
        self.task = None
        self.last_tick = None
        self.loop_stalled = False
        self.last_status = None

    def tick(self):
        now = self.progress.clock()
        # A late event-loop wakeup must not revive an already expired watchdog.
        timely = self.last_tick is None or now - self.last_tick < self.config.interval / 2
        self.last_tick = now
        if not timely:
            self.loop_stalled = True
        status = "stalled" if self.loop_stalled else self.progress.status()
        if status != self.last_status:
            event = {
                "initializing": "watchdog_initializing",
                "healthy": "watchdog_healthy",
                "degraded": "watchdog_degraded",
                "stalled": "watchdog_stalled",
                "unknown": "watchdog_unknown",
                "stopping": "watchdog_stopping",
            }[status]
            logging.getLogger(__name__).info(event)
            self.last_status = status
        if not self.loop_stalled and self.progress.can_notify():
            return self.sender(self.config)
        return False

    def start(self):
        if self.task is None and not self.progress.stopping:
            self.last_tick = self.progress.clock()
            self.task = asyncio.create_task(self.run(), name="polling-watchdog")

    async def run(self):
        while not self.progress.stopping:
            await asyncio.sleep(self.config.cadence)
            self.tick()

    async def close(self):
        self.progress.stop()
        task, self.task = self.task, None
        if task is not None:
            task.cancel()
            try:
                await task
            except asyncio.CancelledError:
                pass


ATTEMPT_OVERHEAD = 46.0


class ProgressBot(ExtBot):
    """Observe the existing updater through public get_updates/shutdown methods.

    Only constructed for a valid watchdog environment. PTB transport defaults and
    run_polling's default 10-second long poll are preserved. A generous overall
    attempt cap additionally bounds slow trickle responses and parsing.
    """
    __slots__ = ("watchdog",)

    def __init__(self, token, watchdog, **kwargs):
        super().__init__(token, **kwargs)
        object.__setattr__(self, "watchdog", watchdog)

    async def get_updates(self, *args, **kwargs):
        timeout = kwargs.get("timeout", args[2] if len(args) > 2 else None)
        seconds = timeout.total_seconds() if isinstance(timeout, timedelta) else timeout or 0
        # Current integration leaves HTTPXRequest defaults intact: connect/read/
        # write 5s, pool 1s; 30s additional room for scheduling and processing.
        budget = float(seconds) + ATTEMPT_OVERHEAD
        progress = self.watchdog.progress
        self.watchdog.start()
        progress.begin(budget)
        try:
            async with asyncio.timeout(budget):
                result = await super().get_updates(*args, **kwargs)
        except asyncio.CancelledError:
            progress.stop()  # Updater stop, including its final timeout=0 cleanup.
            raise
        except InvalidToken:
            progress.stop()
            raise
        except RetryAfter as exc:
            delay = exc.retry_after
            delay = delay.total_seconds() if isinstance(delay, timedelta) else delay
            progress.finish(float(delay) + 0.5, degraded=True)
            raise
        except TimeoutError:
            # Overall cap exceeded: cannot establish transport vs local stall.
            # PTB can retry, but notifications stay latched off until restart.
            progress.stop("unknown")
            raise TimedOut() from None
        except TimedOut:
            progress.finish(degraded=True)  # PTB retries transport timeouts immediately.
            raise
        except TelegramError:
            # PTB's exponential retry is capped at 30s, including after 429.
            progress.finish(30.0, degraded=True)
            raise
        except Exception:
            progress.stop()  # Unknown parser/local failure, no fabricated success.
            raise
        else:
            progress.finish()
            return result

    async def shutdown(self):
        await self.watchdog.close()
        await super().shutdown()
