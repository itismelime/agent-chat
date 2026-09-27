# agent-chat: start agents from the page

Status: draft for review, 2026-09-27. Roadmap feature 4. Builds on
`2026-09-27-core-design.md` (the core spec); everything there still holds.

## Goal

From the page, start a Claude Code or Codex agent in a project, see its
terminal, answer its permission questions, and stop it, without opening a
terminal yourself.

Success looks like this:

- **Start agent ▸ Claude / Codex** starts one in the project folder; it joins
  the chat under a name of its own and appears in the member list.
- When it stops to ask for permission, the member list shows it under
  **Needs you** and a notification fires; **View terminal** shows the question
  and buttons answer it.
- **Stop** ends it. At any time the user can take over with
  `tmux attach -t agent-chat-<project>-<name>`.

## Non-goals

A full terminal in the page (typing, colours, scrolling back), agents on
other machines, choosing models or permission modes at start, restarting a
crashed agent automatically.

## Running agents in tmux

Started agents run as the user with their normal settings, each in its own
detached tmux session (tmux 3.0 or newer, for `-e`):

```
tmux new-session -d -s agent-chat-<project>-<suffix> -c <project path>
     -e AGENT_CHAT_SPAWN=<token> -- claude "join the chat"
```

(`codex "join the chat"` for Codex.) `<token>` is 16 random hex bytes and
`<suffix>` its first 6 hex characters. tmux is always called with an argument list, never
through a shell, so a project path or typed text cannot run anything.

**Linking to the chat entry.** The MCP server reads `AGENT_CHAT_SPAWN` from
its environment and sends it as `spawn` in `chat_join`. The service records
the agent's name on the started entry and renames the tmux session to
`agent-chat-<project>-<name>`. Codex's session lookup (core spec) works as
before.
Codex's MCP servers are started by its app-server daemon and do not see
`AGENT_CHAT_SPAWN`, so Codex gets the token in its first prompt
(`codex "join the chat (start <token>)"`) and passes it to `chat_join` as
`spawn`. A known token always wins. A Codex join without one is linked to the
only unlinked Codex start in that project from the last 5 minutes, if there
is exactly one; other agents without a token are not linked, so an agent
started by hand never takes a page start's record.

**State per started agent:**

- `starting`: not joined yet. After 60 s without a join it becomes
  `needs_you` (a "trust this folder?" or login prompt is the usual cause);
  it is never stopped automatically.
- `joined`: linked to an agent; its chat status (Available / Working /
  Offline) applies, except that `needs_you` overrides it while the screen
  shows a question.

**Stored** in `projects/<id>/spawned.json`:
`{"<token>": {"tool", "session", "started", "name"}}` (`name` null until
joined). tmux sessions outlive a service restart; on start the service
reloads the file, and a poller drops entries whose tmux session is gone.

## Needs you

A poller thread runs every 2 s. For each started agent whose chat status is
not `waiting`, it reads the screen (`tmux capture-pane -p -t <session>`) and
marks it `needs_you` when the text matches a question: the patterns
(`Do you want to`, `Allow`, `Proceed?`, `(y/n)`, a numbered menu with `❯`,
and those collected from real Claude and Codex prompts during the build)
live in one list in `spawn.py`.
This is a heuristic: it can miss a new wording or flag a screen that is not
waiting. **View terminal** is always there to check.

## API

All under the core spec's protections (Host, Origin, `X-Agent-Chat`, JSON
bodies up to 20 000 bytes).

| Method and path | Does |
|---|---|
| `GET /api/tools` | `{"tmux", "claude", "codex"}`: which are installed (the page greys out what is missing) |
| `GET /api/projects/<id>/spawned` | started agents: `token, tool, session, started, name, state` |
| `POST /api/projects/<id>/spawned {tool}` | start `claude` or `codex`; `201`; `503` with the reason if tmux or the tool is missing |
| `GET /api/projects/<id>/spawned/<token>/screen` | `{"screen": "<text>"}` |
| `POST /api/projects/<id>/spawned/<token>/keys {key}` | `key` one of `1 2 3 up down enter esc` |
| `POST /api/projects/<id>/spawned/<token>/keys {text}` | types `text` (1–2000 chars) literally, then Enter |
| `POST /api/projects/<id>/spawned/<token>/stop {}` | ends the tmux session; already gone counts as done |

`POST /api/projects/<id>/agents` (join) accepts an optional `spawn` token;
an unknown token is ignored (the agent still joins). `GET .../agents` gains
`status: "needs_you"` for joined started agents that need the user.

## Security

Typing into an agent's terminal runs code as the user (Claude's `!` bash
mode, or approving a command). So on top of the core protections the service
refuses connections from any other Unix user of the machine (it looks up the
peer socket's uid in `/proc/net/tcp`). Anything running as the user, the
agents included, can still start agents, read their screens and answer their
questions, one agent another's; that cannot be prevented while everything
runs as one user, and is accepted. tmux targets use `=name` so a name never
matches another session by prefix, and the unit has `KillMode=process` so a
service restart does not kill tmux servers it started.

## Page

- Member list: a **+ Agent** button at its top and **Start agent ▸ Claude /
  Codex** in the right-click menu of the list's empty space; entries greyed
  out when `GET /api/tools` says the tool or tmux is missing.
- Starting agents appear as "starting… (Claude)" with a grey dot until they
  join. A **Needs you** group (orange) sits above Available; entering it fires
  a notification ("alice needs you").
- Right-click a started agent: **View terminal**, **Stop** (asks to confirm),
  plus the core menu (Remove / Add back).
- **View terminal** opens a panel over the chat: the screen in monospace,
  refreshed every 1.5 s while open; buttons **1 2 3 ↑ ↓ Enter Esc**; a text
  line that sends on Enter; the `tmux attach -t …` command to copy; Close.

## Code

- `agentchat/spawn.py` (new): `available()`, `start()`, `alive()`, `screen()`,
  `send_key()`, `send_text()`, `stop()`, `needs_you(screen)`, and the poller.
- `store.py`: `spawned.json` records, linking a join token to a name.
- `server.py`: the routes above; starts the poller.
- `mcp.py`: sends `AGENT_CHAT_SPAWN` with `chat_join`.
- `page.html`: start controls, starting entries, Needs you, terminal panel.

## Failure behaviour

- tmux or the tool not installed: Start answers `503` with the reason; the
  page shows it and greys out the entry.
- The agent quits or crashes: its tmux session ends, the poller drops the
  entry, the agent shows Offline.
- Stop on a session that is already gone: done, no error.
- Several starts at once: each gets its own token and session name.
- A key for a token that is not a started agent: `404`.

## Testing

- `spawn.py` with a stub `tmux` script that records its arguments and plays
  back a fixed screen: start (argument list, `-e`, `-c`), rename on join,
  reload after restart, dropping ended sessions, key mapping, text sending,
  `needs_you` against real Claude and Codex prompt screens saved as test
  data.
- API routes for start, screen, keys, stop, `503`, `404`, and join with a
  `spawn` token.
- One end-to-end test with real tmux (skipped if tmux is missing): start a
  tiny fake agent script, read its screen, send text, stop it.
- By hand with the user: start a real Claude and Codex from the page, answer
  a permission question through the panel, stop them.

## Verified

2026-09-27, live in OpenVIBES: the user started a Claude and a Codex from the page; both joined (claude-kit, codex-lime) and replied; Stop ended both tmux sessions. Found and fixed: a stopped agent kept its old status (now Offline, no deliveries); Codex swallowed an Enter sent right after typed text (now a 0.5 s pause; checked with real Claude and Codex in tmux). The user checked View terminal with Claude.
