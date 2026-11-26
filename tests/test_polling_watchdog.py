import asyncio
from datetime import timedelta
import io
import logging
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from telegram.error import InvalidToken, NetworkError, RetryAfter, TimedOut
from telegram.ext import ExtBot
from telegram.request import BaseRequest

from polling_watchdog import NotifyConfig, PollProgress, PollWatchdog, ProgressBot, notify
from safe_logging import SafeFormatter


class Clock:
    value = 0.0

    def __call__(self):
        return self.value


def rig():
    clock = Clock()
    sent = []
    progress = PollProgress(clock)
    controller = PollWatchdog(NotifyConfig('/tmp/fake-notify', 180), progress,
                              lambda config: sent.append('WATCHDOG=1') or True)
    return clock, sent, progress, controller


def test_state_machine_idle_updates_outage_hung_and_shutdown():
    clock, sent, state, wd = rig()
    assert state.status() == 'initializing'
    assert not wd.tick()
    state.begin(56)
    assert not wd.tick()  # no completed poll yet
    state.finish()  # empty update tuple is real progress
    assert wd.tick() and sent
    for _ in range(100):
        state.begin(56)
        clock.value += 20
        state.finish(30, degraded=True)
        assert state.status() == 'degraded'
        assert wd.tick()
        clock.value += 30
    state.begin(56)
    clock.value += 62
    assert state.status() == 'stalled'
    assert not wd.tick()
    state.stop()
    assert state.status() == 'stopping'
    assert not wd.tick()


def test_no_next_attempt_and_loop_stall_fail_closed():
    clock, sent, state, wd = rig()
    state.finish()
    assert wd.tick()
    clock.value = 6
    assert not wd.tick()  # no next attempt despite live event loop
    state.begin(56)
    state.finish(600, degraded=True)
    clock.value = 100
    assert not wd.tick()  # delayed event-loop wakeup
    clock.value = 101
    assert not wd.tick()  # cannot revive after a scheduling stall


@pytest.mark.parametrize('extra', [
    {}, {'WATCHDOG_USEC': '0'}, {'WATCHDOG_USEC': '-1'},
    {'WATCHDOG_USEC': 'nan'}, {'WATCHDOG_USEC': 'x'},
    {'WATCHDOG_USEC': '9' * 400}, {'WATCHDOG_PID': '8'},
    {'WATCHDOG_PID': 'x'}, {'NOTIFY_SOCKET': 'relative'},
    {'NOTIFY_SOCKET': '/'}, {'NOTIFY_SOCKET': '@'},
    {'NOTIFY_SOCKET': '/tmp/\0bad'}, {'NOTIFY_SOCKET': '/' + 'x' * 108},
])
def test_invalid_or_disabled_environment(extra):
    env = {'WATCHDOG_USEC': '180000000', 'NOTIFY_SOCKET': '/tmp/fake'}
    if not extra:
        env = {}
    else:
        env.update(extra)
    assert NotifyConfig.from_env(env, pid=7) is None


def test_env_cadence_abstract_and_socket_transport():
    config = NotifyConfig.from_env({'WATCHDOG_USEC': '180000000',
                                  'NOTIFY_SOCKET': '@fake', 'WATCHDOG_PID': '7'}, pid=7)
    assert config.address == '\0fake'
    assert config.cadence <= config.interval / 2
    calls = []

    class Socket:
        def __enter__(self):
            return self

        def __exit__(self, *args):
            calls.append('closed')

        def setblocking(self, value):
            assert value is False

        def sendto(self, data, address):
            calls.append((data, address))
            return len(data)

    assert notify(config, lambda *args: Socket())
    assert calls == [(b'WATCHDOG=1', '\0fake'), 'closed']

    def broken(*args):
        raise OSError('synthetic private path')

    assert not notify(config, broken)


@pytest.mark.asyncio
@pytest.mark.parametrize('outcome,expected,wait', [
    [(), 'healthy', 0], [(SimpleNamespace(),), 'healthy', 0],
    [TimedOut(), 'degraded', 0], [NetworkError('synthetic'), 'degraded', 30],
    [RetryAfter(120), 'degraded', 120.5],
    [RetryAfter(timedelta(seconds=120)), 'degraded', 120.5],
    [InvalidToken(), 'stopping', 0], [RuntimeError('synthetic'), 'stopping', 0],
])
async def test_public_extension_delegates_results_and_errors(monkeypatch, outcome, expected, wait):
    clock, sent, state, wd = rig()
    callback = AsyncMock(side_effect=outcome) if isinstance(outcome, Exception) else AsyncMock(return_value=outcome)
    monkeypatch.setattr(ExtBot, 'get_updates', callback)
    b = ProgressBot('123:synthetic', wd)
    if isinstance(outcome, Exception):
        with pytest.raises(type(outcome)):
            await b.get_updates(timeout=timedelta(seconds=10), offset=1)
    else:
        assert await b.get_updates(timeout=timedelta(seconds=10), offset=1) == outcome
    callback.assert_awaited_once_with(timeout=timedelta(seconds=10), offset=1)
    assert state.status() == expected
    if expected != 'stopping':
        assert state.deadline == wait + 5
    await b.shutdown()
    assert wd.task is None and not wd.tick()


@pytest.mark.asyncio
async def test_in_flight_cancellation_shutdown_and_no_duplicate_task(monkeypatch):
    clock, sent, state, wd = rig()
    started = asyncio.Event()

    async def hanging(*args, **kwargs):
        started.set()
        await asyncio.Future()

    monkeypatch.setattr(ExtBot, 'get_updates', hanging)
    b = ProgressBot('123:synthetic', wd)
    task = asyncio.create_task(b.get_updates(timeout=10))
    await started.wait()
    existing = wd.task
    wd.start()
    assert wd.task is existing
    assert state.status() == 'initializing' and not wd.tick()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await b.shutdown()
    await b.shutdown()
    assert wd.task is None and state.stopping
    wd.start()
    assert wd.task is None


class FakeRequest(BaseRequest):
    """Exercise real PTB get_updates parsing/error conversion, without a network."""
    read_timeout = 5

    def __init__(self):
        self.reply = (200, b'{"ok":true,"result":[]}')
        self.calls = 0

    async def initialize(self):
        pass

    async def shutdown(self):
        pass

    async def do_request(self, **kwargs):
        self.calls += 1
        return self.reply


@pytest.mark.asyncio
async def test_real_ptb_empty_response_and_http429_conversion():
    clock, sent, state, wd = rig()
    request = FakeRequest()
    b = ProgressBot('123:synthetic', wd, get_updates_request=request)
    assert await b.get_updates(timeout=10) == ()
    assert wd.tick()
    request.reply = (429, b'{"ok":false,"error_code":429,"description":"synthetic","parameters":{"retry_after":90}}')
    with pytest.raises(RetryAfter):
        await b.get_updates(timeout=10)
    assert state.deadline == 95.5 and state.status() == 'degraded'
    assert wd.tick() and request.calls == 2
    await b.shutdown()


def test_safe_logging_omits_arguments_urls_and_tracebacks():
    output = io.StringIO()
    handler = logging.StreamHandler(output)
    handler.setFormatter(SafeFormatter())
    log = logging.Logger('bot')
    log.addHandler(handler)
    try:
        raise ValueError('synthetic-secret')
    except ValueError:
        log.exception('wiki_permission_denied user_id=%s chat_id=%s', 'synthetic-user', 'synthetic-chat')
    log.warning('https://synthetic.invalid/private?secret=synthetic-secret')
    assert output.getvalue() == 'ERROR wiki_permission_denied\nWARNING runtime_event\n'


@pytest.mark.asyncio
async def test_overall_hang_cap_is_unknown_and_never_external_progress(monkeypatch):
    from contextlib import asynccontextmanager
    import polling_watchdog

    @asynccontextmanager
    async def deadline(budget):
        assert budget == 56
        yield
        raise TimeoutError()

    monkeypatch.setattr(polling_watchdog.asyncio, 'timeout', deadline)
    monkeypatch.setattr(ExtBot, 'get_updates', AsyncMock(return_value=()))
    clock, sent, state, wd = rig()
    b = ProgressBot('123:synthetic', wd)
    with pytest.raises(TimedOut):
        await b.get_updates(timeout=10)
    assert state.status() == 'unknown' and not wd.tick()
    state.begin(56)
    state.finish()
    assert not wd.tick()  # latch survives another attempt
    await b.shutdown()


@pytest.mark.asyncio
async def test_monitor_coroutine_uses_same_loop_and_closes(monkeypatch):
    import polling_watchdog
    clock, sent, state, wd = rig()
    state.finish(1000, degraded=True)
    sleeps = []

    async def next_tick(delay):
        sleeps.append(delay)
        clock.value += delay
        if len(sleeps) == 3:
            clock.value += 180  # simulate a frozen application loop
        if len(sleeps) == 4:
            state.stop()

    monkeypatch.setattr(polling_watchdog.asyncio, 'sleep', next_tick)
    await wd.run()
    assert sleeps == [60, 60, 60, 60]
    assert len(sent) == 2


def test_main_opt_in_builder_keeps_hooks_and_run_polling(monkeypatch, tmp_path):
    import bot
    from telegram.ext import Application
    built = []
    monkeypatch.setattr(bot, 'BOT_TOKEN', '123:synthetic')
    monkeypatch.setattr(bot, 'AI_GATEWAY_BASE_URL', 'http://synthetic.invalid')
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(bot, 'load_bot_state', lambda: None)
    monkeypatch.setenv('NOTIFY_SOCKET', '@synthetic')
    monkeypatch.setenv('WATCHDOG_USEC', '180000000')
    monkeypatch.delenv('WATCHDOG_PID', raising=False)
    monkeypatch.setattr(Application, 'run_polling', lambda self: built.append(self))
    bot.main()
    app = built[0]
    assert isinstance(app.bot, ProgressBot)
    assert app.post_init is bot.init_http_client
    assert app.post_shutdown is bot.close_http_client
    assert app.bot.watchdog.task is None  # no heartbeat during initialization
    asyncio.run(app.bot.shutdown())


def test_deploy_failure_never_reads_raw_journal():
    from pathlib import Path
    script = Path('scripts/deploy.sh').read_text()
    assert 'journalctl' not in script
    assert 'raw journal output omitted' in script
