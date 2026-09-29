# Spike: local coding agents through OpenCode (2026-09-27)

Throwaway test for part 3 (local agents). OpenCode 1.18.29, `opencode run
--auto`, isolated config (`XDG_CONFIG_HOME`/`XDG_DATA_HOME` in a scratch
folder) with an OpenAI-compatible provider on bullpen's Ollama
(`http://127.0.0.1:11436/v1`) and the bullpen MCP server pointed at a test
service. Task: read `notes.txt` (secret word), run `date +%Y`, join the chat
with `chat_join` and post both with `chat_post`. RTX 5070 Ti 16 GB, 31 GB RAM.

| Model | Result | Time (load included) | Video memory |
|---|---|---|---|
| qwen3.5:9b | all three steps | 16 s | fits |
| qwen3-coder:30b (30B-A3B, Q4) | all three steps | 60 s | 13.1 of 20.6 GB, rest in RAM |
| gpt-oss:20b | all three steps; filled `spawn` with a made-up value | 62 s | 12.6 of 14.1 GB |
| devstral-small-2:24b (Q4) | read and shell only; never used the MCP tools (twice) | 61–65 s | 11.9 of 18.5 GB |

Findings for the design:

- **OpenCode's `skill` tool must be off.** With the 1 877 skills in
  `~/.claude/skills` and `~/.agents/skills`, OpenCode's prompt was 16 K
  tokens and qwen3.5:9b said it had no tools (168 s, nothing done). With
  `"tools": {"skill": false}` the same model did the task in 16 s.
- **`opencode run` waits for stdin** when it is not a terminal; give it
  `< /dev/null` (the TUI in tmux is not affected).
- The join instructions for kind `llm` currently say to run `bullpen wait`; for
  agents started from the page the service can type messages into the tmux
  session instead, so those instructions must differ.
- The optional `spawn` argument of `chat_join` confuses some models; hide it
  from agents that get their token another way.
- Qwen tool calls worked through the OpenAI-compatible provider here
  (contrary to earlier notes about OpenCode mangling them).
