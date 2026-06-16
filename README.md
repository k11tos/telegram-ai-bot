# telegram-ai-bot

Telegram bot that acts as a mobile UI for AI Gateway-backed chat and Obsidian wiki jobs.

## Obsidian wiki mode

The Telegram bot never reads the Obsidian vault. It only creates jobs through `ai-gateway` and displays results produced by the external `obsidian-mobile-worker`.

For `/wiki` commands, vault access and LLM/opencode work happen outside this bot on the MacBook worker. Do not configure a vault path or opencode command here.

### Environment

```env
OBSIDIAN_TELEGRAM_INTERNAL_TOKEN=
OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS=0
OBSIDIAN_NOTIFICATION_POLL_SECONDS=5
```

- `OBSIDIAN_TELEGRAM_INTERNAL_TOKEN` is sent to ai-gateway as `Authorization: Bearer <token>` for Obsidian job APIs.
- `OBSIDIAN_WIKI_AUTO_RESULT_TIMEOUT_SECONDS` optionally enables short polling for `/wiki ask` results. `0` disables auto-result polling.
- `OBSIDIAN_NOTIFICATION_POLL_SECONDS` controls how often the bot polls ai-gateway for completed Obsidian jobs to auto-deliver. Default: `5`.

The bot auto-delivers completed Obsidian job results by polling ai-gateway notification endpoints. It never reads the Obsidian vault and never runs opencode.

### Commands

```text
/wiki ask <question>
/wiki ingest
/wiki capture <text>
/wiki draft <topic>
/wiki status [job_id]
/wiki result <job_id>
```
