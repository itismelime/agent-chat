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

`install.sh` also downloads Ollama v0.34.2 (about 1.4 GB, checksum-checked)
into `runtime/` and runs it as `agent-chat-ollama` on 127.0.0.1:11436, tuned
like local-ai-chat (flash attention, q8_0 KV cache, one model at a time, 5
minute keep-alive). Models go to `~/.local/share/agent-chat/ollama-models`.
To use an Ollama you already run instead: `./install.sh --ollama-url
http://127.0.0.1:11434` (`--ollama-url own` switches back). Two Ollamas
share the GPU without coordinating, so only one should have a model loaded.

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
4. Or start an agent from the page: **+ Agent** above the member list (or
   right-click it) → Start Claude / Start Codex. It runs in a detached tmux
   session in the project folder and joins by itself. When it asks for
   permission it shows under **Needs you**; right-click → **View terminal**
   shows its screen, with buttons and a text line to answer, and the
   `tmux attach -t …` command to take over. **Stop** ends it. Needs tmux 3.0+.
5. **Models** (bottom of the sidebar): search Hugging Face for GGUF models
   rated for your GPU and get one with a click, pull by Ollama name,
   benchmark (with thinking off and on for models that can think), set a
   model's context and thinking, and unload models from video memory.
6. Add a local model as a member: **+ Agent → Add local model: <model>** (or
   `/local <model> <name>`), with an optional role. It answers like the
   other agents (no `@` or `@name` for it), sees the newest messages that
   fit its context, thinks only if you set that in Models, and answers at
   most three agent messages in a row before waiting for you. Use a model of
   about 7B or more (e.g. `qwen3.5:9b`): tiny ones like `qwen3:0.6b` cannot
   follow a busy chat and start repeating messages.
7. Start a local coding agent: **+ Agent → Start OpenCode: <model>** (or
   `/start opencode [model]`). OpenCode runs in tmux with a local model from
   agent-chat's Ollama and its own config (your OpenCode settings are not
   used), joins the chat, and gets chat messages typed into its terminal when
   it is idle. `gpt-oss:20b` is the default: in tests it made every tool call
   and edited the right files; `qwen3-coder:30b` wrote files outside the
   project and, under OpenCode's long prompt, sometimes writes its tool calls
   as text (Ollama #18530), which agent-chat runs for the chat tools only. Stop unloads its model
   when nothing else uses it.
8. **Board** (header): a kanban board per project (To do, In progress,
   Review, Done). Drag cards, click to edit or assign. Agents use
   `board_list`, `board_add` and `board_update`; every change is announced in
   the chat by `board`, which wakes only the agents it names (an assignee).
   Hover a message → **Add to board** makes it a card; `/card <title>` adds
   one from the message box. `#board` in the address opens the board.
9. Right-click an agent → **Edit personality** (presets or your own text);
   you are also asked when starting one. It reaches the agent with every
   message. **Forget** deletes a removed agent and frees its name. Messages
   render as Markdown (code blocks with Copy, lists, tables, links); each
   agent keeps its own color. Hover a message to Reply (your message then
   quotes it, for agents too), Copy or Add to board. A file path in a
   message opens in a viewer, Markdown rendered, if it lies inside the
   project folder.
10. **Needs an answer**: a column listing every agent message that starts
   with `@user`, and every private one, in all projects, oldest first. Each
   has its own reply box (with `@` completion). Options written as `A)`,
   `B)` … or `Option 1:` become choices: ↑/↓ and Enter, or the letter. A
   question clears once you answer it there, or post to that agent or to
   everyone; × dismisses it.
11. **Renaming**: an agent calls `chat_rename`, runs `chat rename --as <name> <new>`, or you pick
   **Rename** in the Agents list. Color, personality, board cards and unread messages move along;
   its old name keeps working for a session still using it, and its past messages show under the
   new name, with the same name, color and avatar in the chat, the Agents list and Needs an answer.
12. **Private messages**: an agent posts with `chat_post` `private: true`,
   or you pick **Message <name> privately** in the Agents list. Only you and
   that agent see them, and replies stay private. This keeps other agents
   from reading them through the chat; it is not a lock, since agents run
   as your user and could read the chat files.
   **Search messages** (Ctrl+K) filters the chat; unsent text is kept per
   project; **Theme** switches light, dark or your system's. An agent that
   asks for permission shows as a banner above the chat.

On the page, `?` (or the **?** button) lists the keyboard shortcuts (Alt+↑/↓
and Alt+1–9 switch projects, Alt+U jumps to unread, Alt+N goes to the first question for you, else the
terminal of an agent that needs you, Alt+A starts one, Alt+B switches chat and board) and the message-box commands
(`/start claude|codex`, `/stop`, `/term`, `/remove <name>`, `/card <title>`; `//text` posts
text starting with `/`).

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
cannot post into your agents' sessions or read their messages away. The
service also refuses connections from other Unix users of the machine, since
agents started from the page can be typed into. Anything running as you,
agents included, can still post and answer agents' questions.

## Development

`python3 -m unittest` runs the tests. Design: `docs/specs/`; plans:
`docs/plans/`.

MIT licensed.
