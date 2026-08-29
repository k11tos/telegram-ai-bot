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
