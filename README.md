# agent-chat

A shared chat for you, Claude Code and Codex working in the same project.
Standard library only (bash, Python 3); everything stays on 127.0.0.1.

| File | What it does |
|---|---|
| `chat` | Post (`chat <name> <message>`) or follow (`chat`) the log. Messages not from Codex and not addressed to `@claude…`/`@user` are also queued into the newest Codex CLI session (`codex queue`). |
| `chat-web.py` | Chat page on http://127.0.0.1:8765 (`AGENT_CHAT_PORT` to change): `@` autocomplete, desktop notifications, `(n)` unread count in the tab title. |
| `chat-mcp.py` | Claude Code channel: wakes a Claude session per message and gives it a `chat_post` tool. |
| `claude-chat` | Starts Claude Code with the channel loaded: `claude-chat alice` posts as `claude-alice`. |

## Set up a project

1. `mkdir .agent-chat` in the project root and git-ignore it. The log is
   `.agent-chat/log.md`; every tool finds it by walking up from its working
   directory (or uses `$AGENT_CHAT_LOG`).
2. Add the channel to the project's `.mcp.json`:
   ```json
   {"mcpServers": {"agent-chat": {"command": "/path/to/agent-chat/chat-mcp.py"}}}
   ```
3. Put `chat` and `claude-chat` on your `PATH`, e.g.
   `ln -s "$PWD/chat" "$PWD/claude-chat" ~/.local/bin/`.
4. Run the page from the project root: `cd <project> && ./chat-web.py`, or as
   a systemd user service with `WorkingDirectory=<project>`.

Claude Code only loads a custom channel with
`--dangerously-load-development-channels` (research preview; there is no
setting for it), which is what `claude-chat` passes.

## Conventions

- Names: `user`, `codex`, `claude` or `claude-<name>` per Claude session.
- No `@`: everyone replies. `@claude-alice` / `@codex`: only that one replies.
- Claude sessions wake each other only when addressed by full name, so two
  sessions cannot loop.

## Security

The page refuses requests whose Host is not 127.0.0.1/localhost and posts
whose Origin differs or whose body is not JSON, so other websites cannot post
into your agents' sessions. Anyone with a local shell can still write the log.

MIT licensed.
