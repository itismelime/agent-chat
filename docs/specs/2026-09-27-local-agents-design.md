# agent-chat: local agents (OpenCode)

Status: draft for review, 2026-09-27. Roadmap feature 6, part 3 of 3. Builds
on the spawn spec (tmux, View terminal, Needs you, Stop), the local-models
spec (agent-chat's Ollama, Models panel) and the spike
`2026-09-27-local-agents-spike.md`.

## Goal

From the page, start an OpenCode agent that runs a local model from
agent-chat's Ollama, joins the chat, reads and changes files in the project,
and is woken by chat messages like Claude and Codex.

## Non-goals

Other local agent programs than OpenCode; applying the Models panel's
per-model context and thinking settings to OpenCode (see Limits); OpenCode's
headless mode; several models per agent.

## Starting

- The start menu gets **Start OpenCode: <model>**, one entry per installed
  model whose `/api/show` capabilities include `tools`. `qwen3-coder:30b`
  (or, if missing, the largest tool-capable model that fits the GPU) is
  listed first. Entries are greyed out, with the reason, when `opencode` or
  tmux is not installed or the Ollama in use does not answer; with no
  tool-capable model, one greyed entry says "No model that can call tools;
  get one in Models".
- `/start opencode <model>` does the same from the message box.
- The service starts, through the spawn machinery (tool `opencode`):

  ```
  tmux new-session -d -s agent-chat-<project>-<suffix> -c <project path>
       -e AGENT_CHAT_SPAWN=<token> -e XDG_CONFIG_HOME=<data>/opencode-config
       -- opencode -m ac/<model> --prompt "join the chat"
  ```

  (the flag for a first prompt is confirmed during the build; if OpenCode's
  TUI has none, the service types `join the chat` once the TUI is idle).
  The record stores the model.

## OpenCode's config

Before every start the service writes
`<data>/opencode-config/opencode/opencode.json`:

```json
{
  "$schema": "https://opencode.ai/config.json",
  "provider": {"ac": {"npm": "@ai-sdk/openai-compatible", "name": "agent-chat Ollama",
            "options": {"baseURL": "<Ollama in use>/v1"},
            "models": {"<model>": {"name": "<model>", "tools": true}, "…": {}}}},
  "mcp": {"agent-chat": {"type": "local", "enabled": true,
          "command": ["<clone>/bin/chat", "mcp"],
          "environment": {"AGENT_CHAT_PORT": "<port>"}}},
  "tools": {"skill": false}
}
```

with every installed tool-capable model. `XDG_CONFIG_HOME` makes OpenCode use
only this config: the user's own OpenCode settings and MCP servers are not
loaded (fewer tools, fewer chances for a local model to go wrong), and are
never changed. `"skill": false` keeps OpenCode from listing the machine's
skills (1 877 here) in its prompt, which in the spike left a 9B model
believing it had no tools.

## Joining and waking

- Kind `opencode` (added to the kinds); the MCP server maps a client name
  containing `opencode` to it. Shown as "OpenCode · <model>".
- OpenCode starts the MCP server itself, so it sees `AGENT_CHAT_SPAWN` and
  the join links by token. `chat_join` does not offer the `spawn` argument to
  kind `opencode` (it confused gpt-oss in the spike). Join instructions for
  this kind: "Chat messages for you are typed into this session as they
  arrive; reply with chat_post." No `chat wait`.
- **Waking.** For each message that wakes an `opencode` agent (core rules),
  the service keeps it as pending for that agent. The spawn poller (every
  2 s) types pending messages into the agent's tmux session when its screen
  is idle, not working and not asking the user:
  `[chat] <from>: <text> (<label>) Reply with chat_post.` on one line
  (newlines shown as ` / `, cut at 2000 characters with
  `(… cut; chat_read has the whole message)`), with the paste pause before
  Enter. Several pending messages are typed one after another, oldest first,
  each once the screen is idle again. The cursor moves past a message once
  it is typed.
- Idle, working and question screens are recognised by patterns taken from
  real OpenCode 1.18 screens saved in `tests/data/screens/` (collected during
  the build, as for Claude and Codex).

## Status

- `needs_you` as for other started agents (OpenCode's permission screens
  added to the question patterns).
- `busy` (Working) while the screen shows OpenCode working or messages are
  pending; `waiting` (Available) when idle; `offline` once the session ended.

## Stop

Stop ends the tmux session as before, then unloads the agent's model
(`keep_alive: 0`) unless another active local member or OpenCode agent in any
project uses the same model.

## Limits

- OpenCode reaches Ollama through its OpenAI-compatible endpoint, which
  ignores per-request options, so the Models panel's context and thinking
  settings do not apply; OpenCode agents get Ollama's default context
  (32 768 for agent-chat's own Ollama) and the model's default thinking.
- Devstral Small 2 is listed (Ollama reports tool support) but ignored the
  chat tools in the spike.
- A model larger than video memory loads partly into RAM and is slower
  (qwen3-coder:30b: 13.1 of 20.6 GB on the GPU in the spike).

## Failure behaviour

- OpenCode, tmux or Ollama missing: the start entries are greyed with the
  reason; `/start opencode` answers the same reason.
- An agent that does not join within 60 s: Needs you (spawn spec).
- Typing into a session that has ended: the message stays unread, the
  poller drops the record, the agent shows Offline.
- A model deleted while an agent uses it: OpenCode shows the error on its
  screen (View terminal); nothing else is done.

## Testing

- The generated config (provider URL from the setting, the model list, the
  MCP command and port, `skill` off), written before each start.
- The start command: arguments, `-e` values, the model.
- The tool-capable model list from `/api/show` (fake Ollama), default first.
- Join for kind `opencode`: no `spawn` in the tool schema, the instructions.
- Waking: pending messages typed only on an idle screen (real screens as
  test data), one line, cut, oldest first, cursor moved; nothing typed while
  working or asking.
- Stop unloads the model only when no other active member uses it.
- Firefox: the menu entries and the member line.
- Live with the user: a qwen3-coder:30b agent in a scratch project does a
  small task from the chat, one permission question answered through the
  terminal panel, then Stop.

## Verified

2026-09-27, live in OpenVIBES: the user started OpenCode with `qwen3-coder:30b` from the page; it joined, was given a task in the chat (create `hello.txt` containing `hi`), did it, and replied. The user then used Remove rather than Stop, which leaves the tmux session and its model running (Remove only leaves the chat); Stop is the way to end it.
