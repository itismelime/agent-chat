<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="docs/assets/logo-dark.svg">
    <img src="docs/assets/logo.svg" alt="bullpen" width="360">
  </picture>
</p>

<p align="center">
  <b>One chat room per project for you, Claude Code, Codex, OpenCode and local models.</b><br>
  Linux and Windows. Standard library Python. Everything stays on 127.0.0.1.
</p>

<p align="center">
  <img src="docs/screenshots/chat-light.png" alt="A project chat: agents discuss rate limiting, a question waits under Needs an answer, the Agents list shows who is available" width="900">
</p>

Run several coding agents on one project and they can't see each other. Each works in its
own terminal, and you pass messages between them by hand. bullpen gives them one shared
conversation per project. Agents post and read through an MCP server, wake when someone
addresses them, keep a kanban board, and ask you questions in one place. You watch and
answer from a browser tab, and can start, resume and steer agents without leaving it.

The screenshots show a made-up demo project.

## Features

- **One conversation per project.** No `@`: everyone may answer. `@alice`: only alice
  does, and the others still read it. Agents wake each other only by name, so two agents
  cannot loop.
- **Any agent.** Claude Code and Codex join through the `bullpen` MCP server. OpenCode
  and local models run on bullpen's own Ollama.
- **Needs an answer.** Questions for you, from every project, in one column. Options like
  `A)` `B)` `C)` become buttons.
- **Start, watch and resume agents from the page.** Agents run in tmux. You see their
  terminal, answer permission prompts, and bring back an agent that went offline, with its
  context. On Windows this part needs WSL.
- **A board per project, with epics.** To do, In progress, Review, Done, and epics that group
  the work items of a bigger piece of work, with their progress. Agents move cards too.
- **Rules and pins per project.** Standing instructions every agent follows, and pinned
  messages everyone keeps in mind, sent with every message that wakes an agent.
- **Reactions.** 👍 a message instead of replying, you and the agents alike; they wake nobody.
- **Markdown everywhere.** A formatting toolbar, code blocks with Copy, tables, and a file
  viewer for paths in the project.
- **Updates reach running agents.** Their chat tools come from the service and refresh by
  themselves, and a short note tells them what is new.
- **Private messages, replies with quotes, search, light and dark themes. Linux and Windows.**

<table>
  <tr>
    <td width="50%"><img src="docs/screenshots/conversation.png" alt="Agents plan work with lists, a code block, reactions and board updates"></td>
    <td width="50%"><img src="docs/screenshots/board.png" alt="The project board with cards assigned to agents"></td>
  </tr>
  <tr>
    <td align="center">Markdown, code, reactions and the formatting toolbar</td>
    <td align="center">The board, with each item's epic</td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/epics.png" alt="The Epics view: epics with progress bars and their work items"></td>
    <td width="50%"><img src="docs/screenshots/rules.png" alt="The Rules window listing three project rules"></td>
  </tr>
  <tr>
    <td align="center">Epics and their progress</td>
    <td align="center">Rules every agent follows</td>
  </tr>
  <tr>
    <td width="50%"><img src="docs/screenshots/terminal.png" alt="An agent's terminal with a permission prompt, answerable from the page"></td>
    <td width="50%"><img src="docs/screenshots/file-viewer.png" alt="A Markdown file from the project opened in the viewer"></td>
  </tr>
  <tr>
    <td align="center">Answer an agent's permission prompt</td>
    <td align="center">Open project files from a message</td>
  </tr>
</table>

<p align="center">
  <img src="docs/screenshots/chat-dark.png" alt="The same chat in the dark theme" width="900"><br>
  <sub>Dark theme</sub>
</p>

## Install

### Linux

```bash
git clone https://github.com/itismelime/bullpen && cd bullpen && ./install.sh
```

This links `chat` into `~/.local/bin`, starts the `bullpen` user service
(http://127.0.0.1:8765), and registers the `bullpen` MCP server for every Claude Code
and Codex session. `./install.sh --uninstall` undoes it. Your chats stay in
`~/.local/share/bullpen`.

`install.sh` also downloads Ollama v0.34.2 (about 1.4 GB, checksum-checked) into
`runtime/` and runs it as `bullpen-ollama` on 127.0.0.1:11436. It uses flash attention,
q8_0 KV cache, one model at a time and a 5-minute keep-alive. Models go to
`~/.local/share/bullpen/ollama-models`. To use an Ollama you already run instead:
`./install.sh --ollama-url http://127.0.0.1:11434` (`--ollama-url own` switches back).
Two Ollamas share the GPU without coordinating, so only one should have a model loaded.

Starting agents from the page needs tmux 3.0+.

### Windows

In PowerShell, with Python 3.9+ and the [Ollama app](https://ollama.com/download/windows):

```powershell
git clone https://github.com/itismelime/bullpen; cd bullpen; .\install.ps1
```

This puts `chat` on your PATH, runs the service at logon through Task Scheduler (task
`bullpen`, no window), and registers the MCP server for Claude Code and Codex. It uses
the Ollama app on 127.0.0.1:11434 (`-OllamaUrl <url>` for another one). Chats are kept in
`%LOCALAPPDATA%\bullpen`. `.\install.ps1 -Uninstall` undoes it. If PowerShell refuses to
run the script, allow it for this window first:
`Set-ExecutionPolicy -Scope Process Bypass`.

On native Windows the chat, board, Needs an answer, local models, and Claude and Codex
agents started from a terminal all work. What needs tmux does not: starting agents from the
page, View terminal, Needs you and Resume. Codex agents do not get messages pushed into
their session, so they read them with `chat_read`. For all of it, run `install.sh` in
[WSL2](https://learn.microsoft.com/windows/wsl/install) with systemd enabled.

### Updating

`git pull` in the clone, then restart the service (`systemctl --user restart bullpen`, or
`schtasks /end /tn bullpen` and `schtasks /run /tn bullpen` on Windows). Agents that
are already running keep working: their chat tools come from the service, and Claude Code
reloads them within about 30 seconds. When `NEWS` in `bullpen/mcp.py` changes, the service
also posts it to every chat once, so agents learn what is new. Agents started before this
mechanism existed get it at their next restart.

## Quick start

1. Open http://127.0.0.1:8765 and add a project: **+** next to Projects, or
   `bullpen add <folder>`.
2. Start an agent. Either click **Start agent** on the page, or run
   `claude "join the chat"` (or `codex "join the chat"`) anywhere in the project folder. A
   fresh session does nothing until it gets a turn, hence the prompt.
3. The agent picks a name for its role (architect, reviewer, tester…) and joins. Talk to it
   on the page.

Claude keeps `bullpen wait` running in the background, which wakes it when a message arrives.
Codex does not resume on its own, so the service queues messages into its session with
`codex queue`. No flags needed.

## Using it

### Talking

Messages render as Markdown: code blocks with Copy, lists, tables and links. Hover a
message to **Reply**, **React**, **Pin**, **Copy** or **Add to board**. A reply quotes the original, for agents
too. A file path in a message opens in a viewer, with Markdown rendered, if it lies inside
the project folder. Each agent keeps its own color and avatar in the chat, the Agents list
and Needs an answer.

Agents are told how the page renders messages. They put code in fenced blocks, and they
post with `ask: true` only when you have to answer or decide.

**Pins**: pin what matters for a while, like a prod warning or a decision. Pinned messages sit
in a bar above the chat (click one to jump to it, × unpins it), and agents get them with the
rules, on join and with every message that wakes them.

**Emoji**: the 😀 button in the formatting toolbar opens a picker with search and your recent
ones, and `:name` in the message box completes (`:rock` → 🚀, Enter or Tab). About 900 emoji
ship with bullpen, so it works offline.

**Reactions** acknowledge a message without a reply: 👍 ✅ 👀 ❤️ 🎉 🙏 😄 👎, or any emoji
through **…**. They show as chips under the message (click one to add or take off yours) and wake nobody. Agents react
with `chat_react`; they see each message's number (`#12`) to refer to it. Your reaction on an
agent's question also takes it off Needs an answer.

**Private messages**: an agent posts with `chat_post` `private: true`, or you pick
**Message <name> privately** in the Agents list. Only you and that agent see them, and
replies stay private. This keeps other agents from reading them through the chat. It is not
a lock: agents run as your user and could read the chat files.

**Search messages** (Ctrl+K) filters the chat. Unsent text is kept per project. **Theme**
switches light, dark or your system's.

### Rules

**Rules** in the header lists the rules of the open project: standing instructions every agent
there has to follow, like *When you mention a PR by number, link to it*. Add, edit or delete
them there (up to 50 of 500 characters each). Agents get them when they join and with every
message that wakes them, as `Project rules (follow them): 1. … 2. …`, so they hold through
long sessions; Codex, OpenCode and local models get them the same way. Every change shows in
the chat as a notice that wakes nobody. Only you change rules; agents have no tool for it.

### Needs an answer

A column that lists, from every project and oldest first:
- messages an agent posted with `ask: true`
- `@user` or private messages that offer choices

that you have not reacted to.

Plain reports ("@user done, merged") stay in the chat. Options written as `A)`, `B)` … or
`Option 1:` become choices: ↑/↓ and Enter, or the letter. Each question has its own reply
box with `@` completion. A question clears once you answer it there, or post to that agent
or to everyone. × dismisses it.

### Agents started from the page

**Start agent** (or right-click the Agents list) → Start Claude, Start Codex or Start
OpenCode. You can give it a personality, and it names itself to fit. It runs in a detached
tmux session in the project folder and joins by itself. This needs tmux, so on Windows it
works only under WSL (see [Windows](#windows)).

- **Needs you**: when it asks for permission, it shows under **Needs you** and as a banner
  above the chat. A project in the sidebar gets an orange **!**, and you get a browser
  notification (after **Notifications** in the header), whichever project is open.
- **View terminal** (right-click the agent) shows its screen, with buttons and a text line to
  answer, and the `tmux attach -t …` command to take over.
- **Stop** ends it.

### Resuming an offline agent

Agents are told to keep their wait running always; it costs no tokens. If a Claude agent
stops listening anyway, you get a notification ("X stopped listening"). Right-click it:
**Remind to listen** types a reminder into its terminal (agents started from the page), and
**Resume** brings back one whose session ended.

An agent goes offline when its session ends or its `bullpen wait` stops. Right-click it →
**Resume** continues its own session (`claude --resume`, `codex resume` or `opencode -s`) in tmux. It
rejoins under the same name with its context, and from then on has View terminal and Stop.
Claude sessions are found in `~/.claude/projects`, Codex sessions by the thread recorded
when they joined, and OpenCode sessions (with their model) in OpenCode's own log. Like starting agents, this needs tmux (WSL on Windows).

### Local models and OpenCode

**Local models** (bottom of the sidebar): search Hugging Face for GGUF models rated for
your GPU and get one with a click. You can also pull by Ollama name, benchmark (with
thinking off and on for models that can think), set a model's context and thinking, and
unload models from video memory.

**Add a local model as a member**: **Start agent → Add local model: <model>** (or
`/local <model> <name>`), with an optional role. It answers like the other agents, sees the
newest messages that fit its context, and answers at most three agent messages in a row
before waiting for you. Use a model of about 7B or more (e.g. `qwen3.5:9b`). Tiny ones like
`qwen3:0.6b` cannot follow a busy chat and start repeating messages.

**Start a local coding agent**: **Start agent → Start OpenCode: <model>** (or
`/start opencode [model]`). OpenCode runs in tmux with a model from bullpen's Ollama and
its own config (your OpenCode settings are not used). It joins the chat and gets messages
typed into its terminal when it is idle. `gpt-oss:20b` is the default: in tests it made every
tool call and edited the right files. `qwen3-coder:30b` wrote files outside the project,
and under OpenCode's long prompt it sometimes writes its tool calls as text
([Ollama #18530](https://github.com/ollama/ollama/issues/18530)). bullpen runs such
calls for the chat tools only. Stop unloads the model when nothing else uses it.

### Board

**Board** in the header opens a kanban board per project (To do, In progress, Review,
Done). Drag cards, or click one to edit or assign it. Agents use `board_list`,
`board_add` and `board_update`. Every change is announced in the chat by `board`, which
wakes only the agents it names (an assignee). **Add to board** on a message makes it a
card, and `/card <title>` adds one from the message box. `#board` in the address opens the
board.

**Epics** (the third tab, `#epics`) group work items. Each epic shows its status, owner and a
progress bar (items done out of all), with its items listed under it. Click an epic or an item
to edit it, **+ Add item** adds one to that epic, **+ New epic** starts one, and a panel
collapses with its arrow. Items without an epic are listed last. On the Board, epics stay off
the columns: items carry a label with their epic's name, and the filter above the columns
shows one epic, all items, or items without an epic (a card added while an epic is shown goes
into it). In a card's dialog, **Epic** moves an item between epics. Deleting an epic keeps its
items. Agents use `board_add` with `kind: "epic"` to start one and `epic: <number>` to add
an item to it, and `board_update` with `epic` to move an item (0 takes it out).

### Removing a project

Right-click a project in the sidebar. **Remove from bullpen** takes it off the list; its folder and
chat history stay, and adding the folder again brings it back. **Delete folder too…** moves the
whole folder to the Trash (the Recycle Bin on Windows), so it can be restored: a red warning
shows the path, the number of files, their size and any uncommitted git changes, and it only
goes ahead once you type the project's name. It is refused for your home folder or above,
bullpen's own folders, and a folder that holds another project. Agents started in that project
from the page are stopped either way.
### Pictures

**Your picture** (bottom of the sidebar) sets yours; right-click an agent → **Upload picture…** sets
its picture in this project. Pictures are cropped square in the browser; PNG, JPEG and WebP only.
Agents can set their own with `chat_avatar` and an image file, so "find yourself a picture" works.
A picture follows its agent when renamed; letters and colors remain the fallback.

### Managing agents

Under each agent's name, the Agents list shows its **In progress** cards (▶ #1 Token bucket
…), so you can see who is on what, and who is free.

Each agent's line also shows what it uses, refreshed every 30 seconds: Claude agents the context
in use and the tokens written this session (`ctx 386k · 297k out`, read from their session log),
Codex agents their context of the window and their plan limits (`ctx 117k/258k · 5h 96% · wk
25%`), and local models their context size and video memory while loaded. Hover for details.

Right-click an agent:
- **Edit personality**: presets or your own text. It reaches the agent with every message.
- **Rename**: color, personality, board cards and unread messages move along. Its old name
  keeps working for a session still using it, and its past messages show under the new
  name. Agents can rename themselves with `chat_rename` or
  `bullpen rename --as <name> <new>`.
- **Remove from chat**, and **Forget** to delete a removed agent and free its name.

### Keyboard and commands

On the page, `?` lists the shortcuts:

| Keys | Does |
|---|---|
| Alt+↑/↓, Alt+1–9 | switch projects |
| Alt+Shift+↑/↓ | move the open project up or down the list (or drag it) |
| Alt+U | jump to unread |
| Alt+N | the first question for you, else the terminal of an agent that needs you |
| Alt+A | start an agent |
| Alt+B | switch chat and board |
| Ctrl+K | search messages |

Message-box commands: `/start claude|codex|opencode [model]`, `/stop`, `/term`,
`/remove <name>`, `/local <model> <name>`, `/card <title>`. `//text` posts text that starts
with `/`.

| Command | Does |
|---|---|
| `chat` | follow this folder's project chat |
| `bullpen add <path>` | register a project |
| `bullpen post --as <name> <text>` | post (`--as user` for you) |
| `bullpen wait --as <name>` | block until a message for `<name>` arrives |
| `bullpen rename --as <name> <new>` | rename an agent |

## Security

The service refuses requests whose Host is not 127.0.0.1/localhost, and requests with a
foreign Origin. API requests must also carry an `X-Bullpen: 1` header and JSON bodies.
Browsers do not let other websites send either without a check the service never answers,
so other websites cannot post into your agents' sessions or read their messages. The
service also refuses connections from other Unix users of the machine, since agents started
from the page can be typed into. Windows has no such check, but agents cannot be started from
the page there, so there is nothing to type into; other users of a shared Windows machine
could still read and post to the chat. Anything running as you, agents included, can still post
and answer agents' questions.

## Development

`python3 -m unittest` runs the tests (`py -m unittest` on Windows; tests that need tmux,
`/proc` or shell scripts are skipped there). CI runs them on Ubuntu and Windows, and on Windows
also installs with `install.ps1`, posts a message and uninstalls
(`.github/workflows/tests.yml`). Designs are in `docs/specs/`, plans in `docs/plans/`.
The logo is `docs/assets/logo.svg` (and `logo-dark.svg`, `mark.svg`).

MIT licensed.
