# telegram-ai-bot

Telegram bot that acts as a mobile UI for AI Gateway-backed chat and an opencode-backed Telegram wiki work command channel.

## Telegram wiki command channel

The `/wiki` command is a Telegram channel for submitting wiki work jobs and receiving completed results. It is not an Obsidian memo input UI: source memo writing and editing happen in Obsidian.

The Telegram bot never reads the Obsidian vault and never runs the wiki LLM or `opencode`. It only creates jobs through `ai-gateway` and displays results produced by the external opencode-backed worker. For `/wiki` commands, vault access and LLM/opencode work happen outside this bot on the MacBook worker. Do not configure a vault path or opencode command here.

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

By default the Telegram bot no longer sends an immediate “job accepted” message for `/wiki` work commands. Final results are still sent when the worker completes the job.

To restore the old behavior:

```bash
OBSIDIAN_WIKI_SEND_ACCEPTED_MESSAGE=true
```

### Commands

```text
/wiki ask <question>
/wiki ingest
/wiki status [job_id]
/wiki result <job_id>
```

Keep using Obsidian for source memo writing and editing. Use Telegram `/wiki` to submit work requests and receive worker output.

Planned or newly supported worker commands:

```text
/wiki update <request>
/wiki save <request>
/wiki lint [target]
/wiki refactor --preview <request>
```

`/wiki update`, `/wiki save`, `/wiki lint`, and `/wiki refactor --preview` are passed through to the worker as jobs; the bot does not perform vault reads, edits, linting, refactors, or LLM execution itself.
