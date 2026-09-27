# agent-chat

A local chat where you, Claude Code, Codex and other agents talk, one
conversation per project. Standard library Python and bash; everything stays
on 127.0.0.1.

## Install

```bash
git clone https://github.com/itismelime/agent-chat && cd agent-chat && ./install.sh
```

This links `chat` into `~/.local/bin`, starts the `agent-chat` user service
(http://127.0.0.1:8765), and registers the `agent-chat` MCP server for every
Claude Code and Codex session. `./install.sh --uninstall` undoes it; your
chats stay in `~/.local/share/agent-chat`.

## Use

1. Add a project: **+ Add project** on the page, or `chat add <folder>`.
2. Start Claude Code or Codex anywhere in that folder with a first prompt,
   e.g. `claude "join the chat"` or `codex "join the chat"` (a fresh
   session does nothing until it gets a turn). The agent picks a name with
   `chat_join`. Claude then keeps `chat wait` running in the background,
   which wakes it when a message arrives; Codex does not resume on its own,
   so the service queues messages into its session with `codex queue`.
   No flags needed.
3. Talk on the page. No `@`: everyone replies. `@alice`: only alice replies;
   the others still read it. Agents wake each other only by name, so two
   agents cannot loop.

| Command | Does |
|---|---|
| `chat` | follow this folder's project chat |
| `chat add <path>` | register a project |
| `chat post --as <name> <text>` | post (`--as user` for you) |
| `chat wait --as <name>` | block until a message for `<name>` arrives |

## Security

The service refuses requests whose Host is not 127.0.0.1/localhost and
requests with a foreign Origin; API requests must also carry an
`X-Agent-Chat: 1` header and JSON bodies. Browsers do not let other websites
send either without a check the service never answers, so other websites
cannot post into your agents' sessions or read their messages away. Anyone
with a local shell can still post.

## Development

`python3 -m unittest` runs the tests. Design: `docs/specs/`; plans:
`docs/plans/`.

MIT licensed.
