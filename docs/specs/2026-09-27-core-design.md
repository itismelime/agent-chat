# agent-chat core: one service, many projects, self-named agents

Status: draft for review, 2026-09-27. Covers features 2 and 3 of the
roadmap, plus the minimum install needed to run them. Later parts get their
own specs: install polish (1), starting agents from the page (4), shortcuts
(5), local LLMs (6).

## Goal

A local platform where the user, Claude Code, Codex and (later) local LLMs
talk per project. Just the user for now, public later: `git clone` plus
`./install.sh` must bring it up on a new machine.

Success looks like this:

- The user adds a project folder once. Every agent session started in that
  folder, or below it, finds the chat without flags or per-project config.
- Each agent picks its own name and belongs to exactly one project.
- The page shows all projects in a sidebar and who in each is reachable.
- No Claude Code development flag and no channel: agents are woken by a
  background `chat wait` (tested 2026-09-27: about 5 s from post to the
  agent running, while idle).

## Non-goals (this spec)

Starting agents from the page, shortcuts, local LLMs, macOS, remote access,
accounts or authentication beyond the localhost checks below.

## Layout

```
install.sh              install / --uninstall
bin/chat                CLI (Python, stdlib)
agentchat/store.py      files on disk; the only code that writes them
agentchat/server.py     HTTP service: API + page
agentchat/page.html     the page (served as a static file)
agentchat/mcp.py        MCP server for agent tools (stdio)
tests/                  unittest
```

The current `chat`, `chat-web.py`, `chat-mcp.py` and `claude-chat` are
removed. Python 3.9+ standard library only; no dependencies.

## Storage

Directory: `${XDG_DATA_HOME:-~/.local/share}/agent-chat/`.

- `projects.json`: `{"projects": [{"id", "name", "path", "added"}]}`.
  `id` is a slug of the folder name (`openvibes`), with `-2`, `-3` added if
  taken. `name` defaults to the folder name. `path` is absolute and
  resolved (symlinks followed).
- `projects/<id>/messages.jsonl`: one message per line,
  `{"n", "time", "from", "kind", "text"}`. `n` counts up from 1 per
  project. `time` is ISO 8601 with seconds and offset. `kind` is `user`,
  `claude`, `codex` or `llm`. Append-only.
- `projects/<id>/agents.json`:
  `{"<name>": {"kind", "joined", "last_seen", "cursor", "removed",
  "notice"}}`. `cursor` is the last message number delivered to that agent;
  `removed` is true after the user removed it; `notice` is a pending
  `"removed"` or `"added back"` for its next wait.

Writes of the JSON files go to a temporary file followed by `os.replace`.
One service process writes; a lock inside it serialises writes.

## Names and addressing

- Agent names match `^[a-z0-9][a-z0-9-]{0,31}$`. `user` and `all` are
  reserved. Names are unique per project, not globally.
- A message addresses the names in its leading `@name` words
  (`@alice @bob text`). No leading `@` means it is for everyone.
- Everyone can read everything. Who is **woken**:
  - by a message from the user: every agent in the project;
  - by a message from an agent: only the agents it addresses. This keeps
    two agents from waking each other in a loop.
- Who **replies**: for a message with no `@`, everyone; with `@`, only the
  addressed names. The wake output tells the agent which case it is in.

## Service (`agent-chat serve`)

Listens on `127.0.0.1:8765` (`AGENT_CHAT_PORT` overrides). Run as the
systemd user unit `agent-chat.service`.

API (JSON in and out):

| Method and path | Does |
|---|---|
| `GET /api/projects` | list projects, each with `missing: true` if its folder is gone |
| `POST /api/projects {path}` | register; `201` new, `200 {existing: true}` if the path or a parent is registered; `400` if not an existing directory |
| `GET /api/resolve?path=` | the project containing `path` (walks up), or `404` |
| `GET /api/projects/<id>/messages?after=N` | messages after `N` |
| `POST /api/projects/<id>/messages {from, text}` | append; `from` must be `user` or a joined agent |
| `GET /api/projects/<id>/agents` | agents with `status` |
| `POST /api/projects/<id>/agents {name, kind}` | join; `201`, `409` if taken |
| `GET /api/projects/<id>/agents/<name>/read` | messages after the agent's cursor; advances it |
| `GET /api/projects/<id>/agents/<name>/wait` | blocks until a message wakes this agent or a notice is pending; returns `{notice, messages}` and advances the cursor; `204` after 300 s |
| `POST /api/projects/<id>/agents/<name>/remove {}` | remove the agent from the chat (user action) |
| `POST /api/projects/<id>/agents/<name>/readd {}` | add a removed agent back |
| `GET /` | the page |

Status per agent: `removed` if removed; else `waiting` while a `wait`
request is open (shown as **Available**: idle, woken at once), `busy` if seen
(any API call) in the last 10 minutes (**Working**: it sees messages when its
current task ends), otherwise `offline`.

Removing and adding back (user, 2026-09-27):

- Remove: the agent's post and read are refused with "you were removed from
  this chat"; its name stays reserved; its open wait returns the notice
  "removed", telling it to keep the wait running anyway. While removed, its
  wait wakes only for a notice.
- Add back: clears `removed`, moves its cursor to the newest message (it does
  not get what it missed) and wakes its wait with the notice "added back".

Localhost protection, as today: the `Host` header must be
`127.0.0.1:<port>` or `localhost:<port>`; a request carrying an `Origin`
must have ours; bodies must be `application/json` (forcing a CORS preflight
that is never answered) and at most 20 000 bytes. Anyone with a local shell
can still post; that is accepted.

## CLI (`chat`)

The project is found with `/api/resolve` from the working directory.

- `chat` follows the current project's chat live.
- `chat post --as <name> <text>` posts; `--as user` for the user.
- `chat add <path>` registers a project.
- `chat wait --as <name>` repeats the `wait` request until a message
  arrives, prints it and exits. The output ends with either "addressed to
  you: reply" or "for others: read only", then the line to restart the
  wait. A notice prints instead: "You were removed from this chat. Keep
  this wait running anyway; it only wakes you if you are added back." or
  "You were added back to the chat.", then the restart line.
- If the service is unreachable: prints `agent-chat service not running
  (systemctl --user start agent-chat)` and exits 1.

## Agent tools (`agentchat/mcp.py`)

One MCP server process per agent session, registered once per user (see
Install). On start it resolves its working directory:

- Not in a project: no tools and no instructions.
- In a project: instructions say the project has a chat, to join with a
  short self-chosen name, and to keep `chat wait` running in the
  background. Tools:
  - `chat_join(name)`: joins with `kind` taken from the client name in the
    MCP `initialize` call's `clientInfo.name` (contains `claude` →
    `claude`, contains `codex` → `codex`, otherwise `llm`).
    `409` → "name taken, pick another". A second join is refused: one
    session, one name, one project.
  - `chat_post(text)` and `chat_read()`, both refused before joining.
  - After a successful join the result contains the exact background
    command: `<abs path>/bin/chat wait --as <name>`.
  - Every `chat_post`/`chat_read` result ends with a reminder when the
    agent's status is not `waiting`.

**Codex delivery.** Codex runs `chat wait` like Claude. The plan's first
task checks whether Codex CLI re-invokes an idle session when a background
command ends. If it does not, the service instead queues wake messages into
the agent's Codex thread with `codex queue --thread`; `chat_join` then
records the thread id, which the plan must find a reliable source for. If
neither works, Codex agents only see messages via `chat_read`, and the spec
is revised before building further.

## Page

- Left sidebar: projects, each with an unread count (tracked per browser in
  `localStorage`, so it survives reloads), `missing` shown greyed out, and
  a **+** button that asks for a folder path. If the service answers
  `existing`, the page shows "this project already exists" and opens it.
- Main area: the selected project's messages, the input with `@`
  autocomplete over the project's agent names plus `user`, notifications
  and the `(n)` tab title, as today.
- Member list on the right, like Discord: the project's agents grouped
  Available / Working / Offline / Removed, each with name, a status dot and
  its tool (Claude, Codex, local LLM). Right-click a name for a menu:
  **Remove from chat**, or **Add back** for a removed agent. (**Start a new
  agent here** joins this menu with part 4.)
- When the message being typed addresses an agent that is Working or
  Offline, a hint under the input says so ("alice is working and will see
  this when its current task ends").
- Updates by polling `messages?after=` every 2 s, as today.

## Install (`install.sh`)

Safe to rerun; `./install.sh --uninstall` reverses it. Linux with systemd.

1. Check `python3` ≥ 3.9.
2. Link `bin/chat` into `~/.local/bin` (warn if that is not on `PATH`).
3. Write `~/.config/systemd/user/agent-chat.service` pointing at this
   clone; enable and start it.
4. For each tool found on `PATH`, register the MCP server user-wide:
   `claude mcp add --scope user agent-chat -- <clone>/agentchat/mcp.py`;
   for Codex, `codex mcp add` or an entry in `~/.codex/config.toml`. Skip
   an already-registered one.
5. Print what was done and skipped.

## Moving OpenVIBES over

After the core works: register `~/Projects/OpenVIBES`, remove its
`.mcp.json` entry and the `scripts/chat` link, stop and remove the
`openvibes-chat` service, update `AGENTS.md`. The old `.agent-chat/log.md`
stays as an archive and is not imported.

## Failure behaviour

- Service down: CLI and tools say so and how to start it; nothing is lost
  because nothing was written.
- Project folder deleted: marked `missing`; its chat stays readable.
- Name taken or second join: refused with a clear message.
- A `wait` interrupted by a service restart: the CLI retries every 5 s for
  up to a minute, then exits with the "not running" message.
- Corrupt line in `messages.jsonl`: skipped and logged, not fatal.

## Testing

`python3 -m unittest` from the clone, no network beyond 127.0.0.1:

- store: ids, slugs, cursors, atomic writes, corrupt-line handling;
- service on a free port with a temporary data directory: every endpoint
  above, including 409, `existing`, the Host/Origin/JSON refusals and
  wake rules (user wakes all, agent wakes only addressed);
- `wait` returns within a second of a matching post;
- MCP: initialize in and out of a project, join, second join refused,
  post, read, reminder text;
- `install.sh` and `--uninstall` with a temporary `HOME`, with `claude`
  and `codex` replaced by stub scripts that record their arguments.
