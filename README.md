# telegram-ai-bot

Telegram bot that acts as a mobile UI for AI Gateway-backed chat and Obsidian wiki jobs.

## Obsidian wiki mode

The Telegram bot never reads the Obsidian vault. It only creates jobs through `ai-gateway` and displays results produced by the external `obsidian-mobile-worker`.

For `/wiki` commands, vault access and LLM/opencode work happen outside this bot on the MacBook worker. Do not configure a vault path or opencode command here.

### Environment

```env
OBSIDIAN_TELEGRAM_INTERNAL_TOKEN=
OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS=0
OBSIDIAN_WIKI_SEND_ACCEPTED_MESSAGE=false
OBSIDIAN_NOTIFICATION_POLL_SECONDS=5
```

- `OBSIDIAN_TELEGRAM_INTERNAL_TOKEN` is sent to ai-gateway as `Authorization: Bearer <token>` for Obsidian job APIs.
- `OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS` optionally enables short polling for `/wiki ask` results. `0` disables auto-result polling.
- `OBSIDIAN_WIKI_SEND_ACCEPTED_MESSAGE` restores immediate `/wiki` job accepted messages when set to `true`. Default: `false`.
- `OBSIDIAN_NOTIFICATION_POLL_SECONDS` controls how often the bot polls ai-gateway for completed Obsidian jobs to auto-deliver. Default: `5`.

The bot auto-delivers completed Obsidian job results by polling ai-gateway notification endpoints. It never reads the Obsidian vault and never runs opencode.

### Suppressing `/wiki` accepted messages

By default the Telegram bot does not send an immediate “job accepted” message for `/wiki ask`, `/wiki ingest`, or `/wiki draft`. Final results are still sent when the worker completes the job.

To restore the old behavior:

```bash
OBSIDIAN_WIKI_SEND_ACCEPTED_MESSAGE=true
```

### Commands

```text
/wiki ask <question>
/wiki ingest  # Process source notes already written directly in Obsidian
/wiki update <instruction>
/wiki lint [instruction]
/wiki refactor --preview <instruction>
/wiki status [job_id]
/wiki result <job_id>
/wiki draft <topic>  # Optional: create a standalone, unsaved draft
```

`draft` remains available as a secondary workflow because it creates a standalone draft without first running a question. Prefer `ask` followed by `save` when a useful answer should become a note.

### Exact job payloads

The bot forwards commands to `POST /obsidian/jobs` without reading the vault or implementing worker behavior. The common envelope is:

```json
{
  "command": "<command>",
  "payload": {},
  "telegram_chat_id": 123,
  "telegram_message_id": 456,
  "requested_by": 789
}
```

Command payloads are exactly:

| Telegram command | `command` | `payload` |
| --- | --- | --- |
| `/wiki ask <question>` | `ask` | `{"question":"<question>"}` |
| `/wiki save <ask_job_id>` | `save` | `{"source_job_id":"<ask_job_id>"}` |
| `/wiki ingest` | `ingest` | `{}` |
| `/wiki update <instruction>` | `update` | `{"instruction":"<instruction>"}` |
| `/wiki lint` | `lint` | `{}` |
| `/wiki lint <instruction>` | `lint` | `{"instruction":"<instruction>"}` |
| `/wiki refactor --preview <instruction>` | `refactor` | `{"mode":"preview","instruction":"<instruction>"}` |
| `/wiki draft <topic>` | `draft` | `{"topic":"<topic>"}` |

`/wiki status` reads queue/worker status. `/wiki status <job_id>` and `/wiki result <job_id>` both read that job's result. Completed results are delivered back to the originating Telegram chat; successful `ask` results include the exact `/wiki save <ask_job_id>` follow-up.

New notes and edits are authored directly in the Obsidian app. The legacy `/wiki capture` command is unsupported and creates no job; it only directs users to Obsidian. Apply-mode refactors are also unsupported and create no job.

Refactors are preview-only: `/wiki refactor` requires the literal `--preview` flag and a non-empty instruction. `lint` and refactor preview are read-only worker operations. The bot submits requests and displays results; it does not inspect or mutate the vault, run OpenCode, calculate diffs, or decide which vault files a write command may change.

## Opt-in polling watchdog (systemd)

Related: [home_service issue #22, Step 3](https://github.com/k11tos/home_service/issues/22#issuecomment-7191477867).
Prerequisites merged: home_service [#37](https://github.com/k11tos/home_service/pull/37)
and [#38](https://github.com/k11tos/home_service/pull/38).

The watchdog implementation requires Python 3.11+ (matching PR CI).
Ordinary CLI runs and deployments leave the watchdog disabled. This change installs
no systemd configuration and sends no readiness notification. A valid inherited
`NOTIFY_SOCKET` plus positive `WATCHDOG_USEC`, and matching `WATCHDOG_PID` if set,
opt into `WATCHDOG=1`. Filesystem and Linux abstract sockets are supported.
Do not set these variables manually for ordinary use. Step 4 supplies the separate
reviewed opt-in unit/runbook; it must preserve existing Type/ExecStart/runtime metadata.

The existing PTB 22.8 updater calls a public `ExtBot.get_updates` override. It delegates
to PTB unchanged, observing the actual result/error (including empty successful polls).
It does not consume updates separately or patch PTB internals. Existing `run_polling`,
post-init HTTP client/Obsidian startup and post-shutdown cleanup remain in place.
Outside a valid watchdog environment the original bot builder is used.

The small monotonic state machine reports initializing, healthy, degraded, stalled,
unknown or stopping. No signals are sent until a poll completes successfully or
with a recognized retryable Telegram error. Completed transport timeouts retry
immediately; other Telegram errors allow PTB's maximum 30s backoff; `RetryAfter`
allows the server wait plus PTB's 0.5s slack. A successful poll allows 5s scheduling
slack before the next attempt. An old success alone never causes recovery during
continuing external errors. The notifier runs on the application loop every one
third of the systemd interval, with nonblocking Unix datagrams and no worker thread.
Cancellation/invalid token/local unexpected errors stop notifications. Task creation
is lazy and idempotent; bot shutdown cancels and awaits it, and cleanup polls cannot
reactivate stopped notifications.

### Configuration and classification limits

The integrated path retains PTB's default 10s long poll, zero poll interval and
HTTPXRequest defaults (5s connect/read/write and 1s pool). An additional overall
attempt cap of long-poll timeout + 46s (56s normally) bounds slow trickle responses
and local parsing. The progress deadline includes another 5s jitter allowance.
Hitting the overall cap is ambiguous, classified **unknown**, and latches signals
off until process restart even if PTB subsequently retries. Ordinary HTTPX/PTB
`TimedOut` errors remain degraded and continue signaling. An external peer that
exceeds the overall cap can therefore cause bounded recovery; it is not proof of a
local deadlock. Similarly, no new attempt after a known wait is **stalled**, but
this public extension cannot observe the updater's actual sleep or queue internals.
A loop wakeup delayed by at least half the watchdog interval latches signals off,
so a resumed loop cannot simply revive an expired watchdog.

Before activation, verify the deployed PTB version/default timeout/retry behavior,
startup (including gateway preset loading and Telegram bootstrap), actual systemd
version, restart policy and start limits. The watchdog interval must comfortably
exceed startup, the 56s attempt cap plus 5s jitter, notifier cadence and scheduling
jitter; notifier cadence alone being less than half the interval is insufficient.
Very small intervals are unsupported operationally even when syntactically valid.
Changed polling intervals/custom transport timeouts require reviewing this budget.
Long server-requested waits are conservatively treated as known degraded waits;
their completion cannot be proven until the next poll begins.

This covers polling and loop progress only. A live async handler, AI gateway or
other downstream request can still fail or hang independently. Telegram/Pi/network
outages can also prevent alerts. No end-to-end reply or alert-path guarantee is made.
No Pi/systemd activation, restart, real API test or fault injection is part of this PR.

Console logging now uses an allowlist of literal event names and fixed severity:
arguments, IDs, URLs, free-form messages and tracebacks are omitted for application
and library logs. Unknown events become `runtime_event`; this deliberately reduces
diagnostic detail. Top-level runtime failures use a fixed event and exit 1.
Deployment smoke failures omit raw journals. This is an output-format boundary for
standard logging handlers; code must not add direct prints of sensitive data or
new handlers bypassing the formatter.

Offline validation: `python -m pytest -q`, `bash -n scripts/deploy.sh`, and
`python -m py_compile bot.py polling_watchdog.py safe_logging.py`. Tests use fake
clocks, sockets and requests, including real PTB response/error parsing with an
in-memory request. They do not contact a daemon, Telegram or an AI gateway.
Rollback: revert the Step 3 application commit while watchdog remains disabled.
If a future operator has enabled WatchdogSec, disable the separate opt-in drop-in
first using the Step 4 rollback runbook before reverting to a non-notifying build.

Public extension/source references checked against the installed pinned PTB 22.8:
[ExtBot](https://docs.python-telegram-bot.org/en/v22.8/telegram.ext.extbot.html),
[ApplicationBuilder.bot](https://docs.python-telegram-bot.org/en/v22.8/telegram.ext.applicationbuilder.html#telegram.ext.ApplicationBuilder.bot),
[Bot.get_updates](https://docs.python-telegram-bot.org/en/v22.8/telegram.bot.html#telegram.Bot.get_updates),
and [22.8 retry-loop source](https://github.com/python-telegram-bot/python-telegram-bot/blob/v22.8/telegram/ext/_utils/networkloop.py).
No private PTB member is read or modified by the integration.
