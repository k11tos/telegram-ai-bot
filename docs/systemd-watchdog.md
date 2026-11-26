# Opt-in systemd polling watchdog runbook (Step 4)

Tracking: [home_service #22](https://github.com/k11tos/home_service/issues/22),
[Step 4 scope](https://github.com/k11tos/home_service/issues/22#issuecomment-7191479779).
Step 3 is merged in [PR #139](https://github.com/k11tos/telegram-ai-bot/pull/139),
main commit `5f51b4b141e4e4f8fec3f30c416499175b85eda4`.
This document and `systemd/60-polling-watchdog.conf.example` do not activate anything.
There is no new production daemon, timer, cron job or restart state file.

The operator must confirm deployment of Step 3 or a reviewed descendant with
**WatchdogUSec=0** before activation. Permission to prepare this PR is not evidence
that deployment occurred. A successful deploy smoke check proves only is-active,
not polling, notifier delivery or the running code's identity. No Pi access,
production settings, live Telegram calls or production fault injection were used
in preparing this change. Step 5 remains responsible for integration acceptance
and operator-confirmed live results; keep the tracking issue open.

## 1. Read-only preflight on the Pi

Run commands locally in a trusted shell; do not use shell tracing. Set the actual
unit name and reviewed checkout path (the example path must be edited):

```sh
UNIT=telegram-bot.service
APP_DIR=/absolute/path/to/telegram-ai-bot
systemctl --version
systemctl show "$UNIT" --no-pager \
  -p Id -p LoadState -p ActiveState -p SubState -p Type -p MainPID \
  -p NotifyAccess -p WatchdogUSec -p Restart -p RestartUSec \
  -p StartLimitIntervalUSec -p StartLimitBurst -p NRestarts -p Result \
  -p WatchdogTimestampMonotonic 2>/dev/null || printf '%s\n' 'Unit inspection failed'
git -C "$APP_DIR" rev-parse --verify HEAD 2>/dev/null || printf '%s\n' 'Checkout inspection failed'
git -C "$APP_DIR" merge-base --is-ancestor \
  5f51b4b141e4e4f8fec3f30c416499175b85eda4 HEAD 2>/dev/null
# Exit 0 means the checkout includes Step 3; any other result requires review.
```

Record only these allowlisted properties, systemd version and commit SHA. Missing
unit, missing properties, insufficient permissions or failed ancestry checks are
**unknown / stop**, not permission to enable or repeatedly restart. Confirm the
unit name matches the operator's inventory. Do not paste `systemctl cat`, full
`show`, `status`, raw journals, process command lines, environment dumps or secret
files into issues, CI or LLM prompts. Do not read additional privileged secret files.

Confirm the deployment/restart record identifies this checkout and that the unit's
existing launch arrangement executes Python as the **main process**. Do this from
trusted configuration ownership/deployment records; do not publish ExecStart/env
values. `git HEAD` alone does not identify a running process. A shell wrapper must
already use `exec`; a supervising shell or multi-process launcher needs a separate
review. `NotifyAccess=main` will reject notifications from a child process. Preserve
Type and ExecStart. The example supports existing `simple`/`exec` arrangements;
stop for other types pending review. In particular the bot sends no `READY=1`, so
switching to `Type=notify` is unsupported. Do not add manual NOTIFY_SOCKET,
WATCHDOG_PID or WATCHDOG_USEC environment values; systemd supplies them.

Before proceeding, record operator confirmation that the notifier-capable build
was deployed/restarted with watchdog disabled and is operating normally. Inspect
Python 3.11+, pinned PTB 22.8 and dependency installation using the established
deployment procedure. Review any newer application changes. Review the existing
restart and rate-limit policy, including later drop-ins, before staging a change.

## 2. Choose a budget and stage the candidate

Candidate values are **not approved production settings**:

| Setting | Candidate | Operator review |
| --- | --- | --- |
| WatchdogSec | 180s | Startup, first poll, scheduling and notification budget |
| NotifyAccess | main | Python must be the unit's main process |
| Restart | on-failure | Keep an explicitly justified compatible policy such as always |
| RestartSec | 30s | Recovery delay, not a guarantee of availability |
| StartLimitIntervalSec / StartLimitBurst | 15min / 3 | Includes manual starts as well as automatic restarts |

Current integration: 10s long poll, 5s connect/read/write, 1s pool; an overall
56s attempt cap plus 5s progress jitter. PTB retries transport timeouts immediately,
other Telegram errors with backoff up to 30s, and RetryAfter with the server delay
plus 0.5s. Notification cadence is inherited WATCHDOG_USEC / 3: 60s for this
candidate, comfortably below half the interval. Startup/bootstrap/gateway preset
loading and the first completed poll must fit the initial watchdog window with
margin. A stalled poll may take its deadline plus a notification tick plus systemd's
remaining interval before recovery; WatchdogSec is not an end-to-end recovery SLA.
Measure startup and jitter in staging before choosing final values. Shortening
180s merely to speed up a bot test can cause false recovery.

Idle successful empty polls are healthy. Continuing completed network/API errors
with advancing retry/backoff are degraded and continue notifications even when
the last success is old. RetryAfter may be long; an old success alone must not
trigger recovery. The overall cap's **unknown** case can reflect a slow external
peer and latches notifications off until restart. A loop delay of at least half
the interval also latches off. Rate limits bound recovery but may leave the bot
stopped; investigate instead of automatically resetting them.

Stage on the operator machine/Pi without modifying systemd:

```sh
STAGE=$(mktemp -d)
chmod 700 "$STAGE"
cp "$APP_DIR/systemd/60-polling-watchdog.conf.example" "$STAGE/60-polling-watchdog.conf"
vi "$STAGE/60-polling-watchdog.conf"
# Keep a compatible existing Restart policy by omitting Restart= here, if justified.
# Do not add Type, ExecStart, Environment or EnvironmentFile overrides.
cat > "$STAGE/offline-watchdog.service" <<'UNIT_EOF'
[Unit]
Description=Synthetic offline watchdog configuration check
[Service]
Type=simple
ExecStart=/bin/true
UNIT_EOF
mkdir "$STAGE/offline-watchdog.service.d"
cp "$STAGE/60-polling-watchdog.conf" "$STAGE/offline-watchdog.service.d/"
systemd-analyze verify "$STAGE/offline-watchdog.service"
```

Use the actual supported Type in this **synthetic** unit if it is `exec`. Verify
on the target systemd version; older versions may reject directives. This parses
only synthetic data and does not start a service or prove notification delivery.
Run offline repository tests with `python -m pytest -q tests/test_systemd_watchdog_config.py`.
If systemd-analyze is absent, or a bare fixture fails because the sandbox denies
SO_PASSCRED, parsing tests skip explicitly; static checks remain.
A manager/bus is not required for those tests. The test verifier uses user scope
with a private XDG_RUNTIME_DIR to avoid creating host /run/systemd; the directives
are shared, but target system-scope validation is still an operator requirement.

## 3. Manual activation, only after explicit operator approval

Do not execute these commands as part of ordinary GitHub Actions/deploy. Existing
`scripts/deploy.sh` and `.github/workflows/deploy.yml` continue managing deployment
and `10-deploy-runtime-env.conf` without installing this example. Once manually
enabled, later ordinary deploy restarts inherit the watchdog; disable it first
before deploying an older or non-notifying build.

Keep the preflight values as the rollback baseline. These commands intentionally
**refuse to overwrite** an existing watchdog drop-in. If one exists, stop and
review it; keep a private backup of just that file before any separately approved
replacement. Do not replace/delete the unit, its entire `.d` directory or
`10-deploy-runtime-env.conf`.

```sh
DROP_DIR="/etc/systemd/system/$UNIT.d"
DROP_FILE="$DROP_DIR/60-polling-watchdog.conf"
sudo test ! -e "$DROP_FILE" && sudo test ! -L "$DROP_FILE" || exit 1
sudo install -d -m 755 "$DROP_DIR"
sudo install -m 644 "$STAGE/60-polling-watchdog.conf" "$DROP_FILE"
sudo systemctl daemon-reload
systemctl show "$UNIT" --no-pager \
  -p LoadState -p Type -p NotifyAccess -p WatchdogUSec -p Restart \
  -p RestartUSec -p StartLimitIntervalUSec -p StartLimitBurst 2>/dev/null
# STOP before restart if effective values differ from the reviewed candidate,
# or Type changed. Later drop-ins may override this file. Use rollback below.
# With the unedited candidate expect 3min / 180000000 usec, main,
# on-failure, 30s, 15min and 3 (systemctl may format durations as time units).
sudo systemctl restart "$UNIT" 2>/dev/null || printf '%s\n' 'Restart failed; inspect allowlisted state'
```

`daemon-reload` rereads manager configuration globally; only this unit is restarted.
Never restart Docker, other units or the host. Do not use an automatic reset loop.
Observe for multiple watchdog windows, including a naturally idle period:

```sh
systemctl show "$UNIT" --no-pager \
  -p ActiveState -p SubState -p MainPID -p NRestarts -p Result \
  -p WatchdogUSec -p WatchdogTimestampMonotonic 2>/dev/null
```

Repeat manually after at least two configured watchdog intervals. Accept only if
active/running, timestamp advances, and NRestarts/MainPID remain stable. Timestamp
advancement verifies receipt of notifications, not successful AI replies. Operator
functional acceptance uses normal approved bot operation; no extra getUpdates
consumer or getMe recovery probe. Do not inject network/process faults on production.
If watchdog expires, unknown/stalled events persist or restarts increase, roll back
and investigate sanitized state. A repeated external failure within the 56s cap
should not cause a restart storm; prove that with mocks/staging, not a live outage.

## 4. Rollback and start-limit-hit

For the first installation above, rollback removes **only the new file**:

```sh
sudo rm -- "$DROP_FILE"
sudo systemctl daemon-reload
systemctl show "$UNIT" --no-pager \
  -p Type -p WatchdogUSec -p NotifyAccess -p Restart -p RestartUSec \
  -p StartLimitIntervalUSec -p StartLimitBurst 2>/dev/null
# Verify baseline restored (normally WatchdogUSec=0) BEFORE restarting old code.
sudo systemctl restart "$UNIT" 2>/dev/null || printf '%s\n' 'Restart failed; check start limit'
```

If an operator later replaces an existing opt-in file, restore **only its private
backup** to the same DROP_FILE instead of removing it, then daemon-reload, inspect
and restart only this unit. A prior enabled backup is not a rollback to disabled;
verify compatibility before reverting application code. If another drop-in enables
a watchdog, removal alone is insufficient: stop and resolve it through its owner.
The runtime metadata drop-in and environment file must remain intact.

For `Result=start-limit-hit`, fix the cause and confirm the rollback/effective
configuration first. Starts, including manual starts/deploy restarts, count toward
the limit; it is not simply a restart counter. The operator may wait for the window
to elapse and explicitly start, or, after diagnosis and explicit approval, use:

```sh
sudo systemctl reset-failed "$UNIT"
sudo systemctl start "$UNIT" 2>/dev/null || printf '%s\n' 'Start failed; stop and investigate'
```

Never place reset-failed/start in a loop or CI recovery job.

## 5. Disposable recovery exercise (separate staging machine only)

This exercises the manager's transport/expiry/restart policy with **no bot code or
credentials**. It does not prove PTB behavior; Step 3's fake clocks/requests cover
idle polling, advancing external errors, long retry waits, hung attempts and loop
stalls. Run only on an approved disposable systemd host with /usr/bin/python3. Do not run on the production Pi. Reserve the name below first;
abort if it is already loaded. No enable/boot persistence is used.

```sh
TEST_UNIT=telegram-watchdog-disposable.service
systemctl show "$TEST_UNIT" -p LoadState --value 2>/dev/null
# Continue only if output is not-found and no file/drop-in with this name exists.
LAB=$(mktemp -d /tmp/telegram-watchdog-lab.XXXXXX)
chmod 755 "$LAB"
cat > "$LAB/probe.py" <<'PY_EOF'
import os
import socket
import sys
import time

mode = sys.argv[1]
if mode == 'exit':
    raise SystemExit(1)
address = os.environ['NOTIFY_SOCKET']
if address.startswith('@'):
    address = '\0' + address[1:]
cadence = int(os.environ['WATCHDOG_USEC']) / 3_000_000
with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
    while True:
        sock.sendto(b'WATCHDOG=1', address)
        if mode == 'stall':
            time.sleep(60)  # Stop notifications after the first one.
        else:
            time.sleep(cadence)
PY_EOF
cat > "$LAB/$TEST_UNIT" <<UNIT_EOF
[Unit]
Description=Disposable synthetic watchdog exercise
StartLimitIntervalSec=2min
StartLimitBurst=3
[Service]
Type=simple
ExecStart=/usr/bin/python3 $LAB/probe.py healthy
WatchdogSec=6s
NotifyAccess=main
Restart=on-failure
RestartSec=2s
# Keep synthetic crash artifacts out of core storage.
LimitCORE=0
UNIT_EOF
systemd-analyze verify "$LAB/$TEST_UNIT"
sudo test ! -e "/run/systemd/system/$TEST_UNIT" || exit 1
sudo install -m 644 "$LAB/$TEST_UNIT" "/run/systemd/system/$TEST_UNIT"
sudo systemctl daemon-reload
sudo systemctl start "$TEST_UNIT"
systemctl show "$TEST_UNIT" --no-pager \
  -p ActiveState -p SubState -p MainPID -p NRestarts -p Result \
  -p WatchdogTimestampMonotonic -p ExecMainStartTimestampMonotonic \
  -p ExecMainExitTimestampMonotonic -p RestartUSec
```

Observe healthy mode for >12s: advancing timestamp, stable PID/restart count.
Then stop **only TEST_UNIT**, use `vi "$LAB/$TEST_UNIT"` to change `healthy` to
`stall`, reinstall that same runtime file, daemon-reload and start it. Observe for
about 40s: last notification expires after ~6s; restart occurs after at least the
2s delay; after three starts within 2min it stops with start-limit-hit. Poll the
allowlisted properties manually to record exit/start monotonic times and restart
counts; do not assume wall-clock exactness. Never automatically reset the limit.

After diagnosing the expected limit, stop TEST_UNIT, change mode to `exit`, reinstall
and reload. Explicitly reset-failed TEST_UNIT once and start: process exit should
recover with the 2s delay and hit the same start limit. For the **disabled** case, stop TEST_UNIT, set `WatchdogSec=0` and replace
ExecStart with `/usr/bin/sleep infinity` (the probe requires watchdog environment).
Reinstall, reload, reset-failed once after review and start. Verify WatchdogUSec=0 and stable activity
for >12s without notifications. This disabled fixture tests manager policy only;
ordinary bot operation without watchdog env is covered by the application tests.
Short lab intervals apply only to this trivial fixture, never to the real bot.

Cleanup even after a failed exercise:

```sh
sudo systemctl stop "$TEST_UNIT"
sudo rm -- "/run/systemd/system/$TEST_UNIT"
sudo systemctl daemon-reload
sudo systemctl reset-failed "$TEST_UNIT"
rm -r -- "$LAB"
rm -r -- "$STAGE"  # Only when the activation/rollback review is finished.
```

## Acceptance record and limits

Record commit, systemd version, operator-approved effective values, disabled-build
deployment evidence, test commands, expected/observed outcomes and rollback result.
Separate offline parser/static tests, simulated application tests, disposable manager
exercises and live manual acceptance. Do not report unexecuted steps as passed.
Step 4 supplies the procedure; Step 5 collects cross-repo and live acceptance.

A responsive poller does not guarantee handler/AI/downstream responses. getMe proves
API/auth access only. Telegram alerts can be silent during Telegram/network/Pi
failure; no alternate alert channel is added. No automatic issue closure or merge.

References: [systemd.service](https://www.freedesktop.org/software/systemd/man/latest/systemd.service.html),
[systemd.unit start limits](https://www.freedesktop.org/software/systemd/man/latest/systemd.unit.html#StartLimitIntervalSec=interval),
[sd_notify](https://www.freedesktop.org/software/systemd/man/latest/sd_notify.html).
