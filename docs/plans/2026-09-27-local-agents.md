# Local agents (OpenCode) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Start OpenCode agents with a local model from the page; they join the chat, are woken by messages typed into their terminal, show Needs you / Working / Available, and Stop unloads their model when nothing else uses it.

**Architecture:** `agentchat/opencode.py` lists tool-capable models and writes OpenCode's own config. `spawn.py` learns tool `opencode` (command, `XDG_CONFIG_HOME`), OpenCode's question and working screens, and the typing of pending messages in the poller; the store gets kind `opencode` and a `type_in` hook; `mcp.py` maps the `opencode` client and gives it the right join text and schema. The server adds `/api/opencode/models` and passes the model to Start; the page adds the menu entries.

**Tech Stack:** Python ≥ 3.9 stdlib, tmux, OpenCode 1.18, vanilla JS.

**Spec:** `docs/specs/2026-09-27-local-agents-design.md` (spike: `docs/specs/2026-09-27-local-agents-spike.md`)

## Global Constraints

- Facts from the build's probes (OpenCode 1.18.29/1.18.32): the TUI takes `--prompt <text>`; OpenCode's MCP client reports `clientInfo.name` `"opencode"`; working screens show `esc interrupt`; permission screens show `Permission required` and `enter confirm`; idle screens show neither.
- Start command: `opencode -m ac/<model> --prompt "join the chat"` with `-e XDG_CONFIG_HOME=<data>/opencode-config` and `-e AGENT_CHAT_SPAWN=<token>`.
- Config at `<data>/opencode-config/opencode/opencode.json`: provider `ac` (`@ai-sdk/openai-compatible`, `<ollama url>/v1`), all tool-capable models, MCP `agent-chat` (`[<clone>/bin/chat, "mcp"]`, env `AGENT_CHAT_PORT`), `"tools": {"skill": false}`.
- Default model `qwen3-coder:30b`, else the largest tool-capable one with `size × 1.2 ≤ GPU × 0.9`, else the largest.
- Typed line: `[chat] <from>: <text> (<label>) Reply with chat_post.`; newlines → ` / `; at most 2000 characters, cut with `(… cut; chat_read has the whole message)`; one pending message per agent per poll, oldest first, only on an idle screen.
- Files under 500 lines. Commit only after checking the full suite printed `OK`.

## Review Focus

- A message typed into an OpenCode terminal must never be typed twice, even across polls (test in Task 4).
- Nothing is typed while OpenCode shows a permission question (test in Task 4).
- Stop must not unload a model that a local member or another OpenCode agent still uses (test in Task 4).
- A Claude agent started from the page must keep working exactly as before (Task 1 keeps its tests green).
- The generated config must not include the user's own OpenCode config or MCP servers (test in Task 2).

---

### Task 1: OpenCode in the tmux layer

**Files:**
- Modify: `agentchat/spawn.py`, `tests/test_spawn.py`, `tests/test_server.py`
- Create: `tests/data/screens/question-opencode-permission.txt`, `idle-opencode-idle.txt`, `working-opencode-working.txt` (copies)

**Interfaces:**
- Produces: `spawn.TOOLS = ("claude", "codex", "opencode")`; `spawn.start(tool, path, session, token, model=None, config_home=None)`; `spawn.WORKING`, `spawn.working(text) -> bool`; `spawn.format_message(m, name) -> str`; `needs_you` also matches `Permission required` / `enter confirm`.

- [ ] **Step 1: Copy the screens**

```bash
O=/tmp/claude-1000/-home-lime-Projects-OpenVIBES/05ff64d3-8d30-489a-adb2-a2b6d1caf4e5/scratchpad/prompts/out
cp $O/opencode-permission.txt tests/data/screens/question-opencode-permission.txt
cp $O/opencode-idle.txt tests/data/screens/idle-opencode-idle.txt
cp $O/opencode-working.txt tests/data/screens/working-opencode-working.txt
```

- [ ] **Step 2: Write the failing tests**

In `tests/test_spawn.py`:

1. In `TmuxTest.test_available`, change the expected dict to `{"tmux": True, "claude": True, "codex": True, "opencode": False}`.
2. In `NeedsYouTest.test_real_screens`, replace `self.assertEqual(len(files), 6)` with `self.assertEqual(len(files), 9)` and the loop body with:

```python
            self.assertEqual(spawn.needs_you(f.read_text()), f.name.startswith("question-"), f.name)
            self.assertEqual(spawn.working(f.read_text()), f.name.startswith("working-"), f.name)
```

3. Add to `TmuxTest`:

```python
    def test_start_opencode(self):
        (self.d / "opencode").write_text("#!/bin/sh\nexit 0\n")
        (self.d / "opencode").chmod(0o755)
        spawn.start("opencode", "/p", "agent-chat-p-abc123", "tok", model="qwen3-coder:30b",
                    config_home="/data/opencode-config")
        self.assertEqual(calls(self.d), [
            "new-session -d -s agent-chat-p-abc123 -c /p -e AGENT_CHAT_SPAWN=tok "
            "-e XDG_CONFIG_HOME=/data/opencode-config -- opencode -m ac/qwen3-coder:30b "
            "--prompt join the chat"])
        with self.assertRaises(StoreError) as e:
            spawn.start("opencode", "/p", "s", "t")
        self.assertEqual(e.exception.code, 400)

    def test_format_message(self):
        m = {"from": "user", "text": "@kit look\nat this", "time": "2026-09-27T20:00:00+02:00"}
        self.assertEqual(spawn.format_message(m, "kit"),
                         "[chat] user: @kit look / at this (addressed to you: reply) Reply with chat_post.")
        long = spawn.format_message(dict(m, text="x" * 5000), "kit")
        self.assertLessEqual(len(long), spawn.MAX_TEXT)
        self.assertIn("(… cut; chat_read has the whole message)", long)
```

4. In `tests/test_server.py` `test_spawn_routes`, change the expected tools dict to `{"tmux": True, "claude": True, "codex": True, "opencode": False}`.

- [ ] **Step 3: Run them to verify they fail**

Run: `python3 -m unittest tests.test_spawn tests.test_server 2>&1 | tail -3`
Expected: failures (dict mismatch, 9 screens, `working` missing).

- [ ] **Step 4: Implement**

In `agentchat/spawn.py`:

1. `TOOLS = ("claude", "codex")` → `TOOLS = ("claude", "codex", "opencode")`.
2. Replace the `QUESTION = re.compile(...)` statement with:

```python
QUESTION = re.compile(r"enter to confirm|enter confirm|enter continue|esc to cancel|do you want to|"
                      r"would you like to|permission required|^\s*[❯›]\s*\d+\.", re.I | re.M)
# OpenCode's footer while it works (tests/data/screens/working-*)
WORKING = re.compile(r"esc interrupt", re.I)
```

3. Replace `def start(tool, path, session, token):` through the `new-session` call with:

```python
def start(tool, path, session, token, model=None, config_home=None):
    """Start the tool with the prompt "join the chat" in a detached tmux session."""
    if tool not in TOOLS:
        raise StoreError(400, "tool must be one of: " + ", ".join(TOOLS))
    if tool == "opencode" and not model:
        raise StoreError(400, "OpenCode needs a model")
    missing = [t for t in ("tmux", tool) if shutil.which(t) is None]
    if missing:
        raise StoreError(503, "%s is not installed" % " and ".join(missing))
    env = ["-e", "AGENT_CHAT_SPAWN=" + token]
    if tool == "opencode":
        env += ["-e", "XDG_CONFIG_HOME=" + config_home] if config_home else []
        command = ["opencode", "-m", "ac/" + model, "--prompt", PROMPT]
    else:
        # Codex's MCP servers are started by its app-server daemon and do not see
        # AGENT_CHAT_SPAWN, so Codex gets the token in its prompt for chat_join.
        command = [tool, PROMPT if tool == "claude" else "%s (start %s)" % (PROMPT, token)]
    r = tmux("new-session", "-d", "-s", session, "-c", path, *env, "--", *command)
```

(the `if r.returncode:` lines after it stay).

4. After `def needs_you(text):` … add:

```python
def working(text):
    """Whether the bottom of the screen shows OpenCode working."""
    return bool(WORKING.search("\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])))


def format_message(m, name):
    """One line to type into an OpenCode agent's terminal."""
    from .client import label
    tail = " (%s) Reply with chat_post." % label(m, name)
    text = m["text"].replace("\r", "").replace("\n", " / ")
    line = "[chat] %s: %s%s" % (m["from"], text, tail)
    if len(line) > MAX_TEXT:
        cut = " (… cut; chat_read has the whole message)"
        room = MAX_TEXT - len("[chat] %s: " % m["from"]) - len(cut) - len(tail)
        line = "[chat] %s: %s%s%s" % (m["from"], text[:room], cut, tail)
    return line
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest 2>&1 | tail -1` → `OK`.

- [ ] **Step 6: Commit**

```bash
git add agentchat/spawn.py tests/test_spawn.py tests/test_server.py tests/data/screens
git commit -m "tmux layer: OpenCode start command, its question and working screens"
```

---

### Task 2: OpenCode's config and models

**Files:**
- Create: `agentchat/opencode.py`, `tests/test_opencode.py`

**Interfaces:**
- Produces: `opencode.DEFAULT = "qwen3-coder:30b"`; `opencode.tool_models(ollama, gpu_total) -> [name]` (tool-capable, default first); `opencode.config_home(root) -> Path` (`<root>/opencode-config`); `opencode.write_config(root, ollama_url, models, port) -> Path`.

- [ ] **Step 1: Write the failing tests**

`tests/test_opencode.py`:

```python
import json
import tempfile
import unittest
from pathlib import Path

from agentchat import opencode
from agentchat.ollama import Ollama
from tests.fake_ollama import GIB, FakeOllama

CLONE = Path(__file__).resolve().parent.parent


class OpenCodeTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.ollama = Ollama(self.fake.url)

    def test_tool_models_default_first(self):
        self.fake.add("small:9b", size=6 * GIB, capabilities=["completion", "tools"])
        self.fake.add("talker:1b", size=GIB, capabilities=["completion"])
        self.fake.add("qwen3-coder:30b", size=18 * GIB, capabilities=["completion", "tools"])
        self.assertEqual(opencode.tool_models(self.ollama, 16 * GIB), ["qwen3-coder:30b", "small:9b"])

    def test_without_the_default_the_largest_that_fits(self):
        self.fake.add("small:9b", size=6 * GIB, capabilities=["tools"])
        self.fake.add("mid:20b", size=11 * GIB, capabilities=["tools"])
        self.fake.add("huge:70b", size=40 * GIB, capabilities=["tools"])
        self.assertEqual(opencode.tool_models(self.ollama, 16 * GIB)[0], "mid:20b")
        self.assertEqual(opencode.tool_models(self.ollama, 0)[0], "huge:70b")

    def test_write_config(self):
        root = Path(tempfile.mkdtemp())
        path = opencode.write_config(root, "http://127.0.0.1:11436", ["a:1", "b:2"], 8765)
        self.assertEqual(path, root / "opencode-config" / "opencode" / "opencode.json")
        c = json.loads(path.read_text())
        self.assertEqual(c["provider"]["ac"]["options"]["baseURL"], "http://127.0.0.1:11436/v1")
        self.assertEqual(c["provider"]["ac"]["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(sorted(c["provider"]["ac"]["models"]), ["a:1", "b:2"])
        self.assertTrue(all(m["tools"] for m in c["provider"]["ac"]["models"].values()))
        self.assertEqual(c["mcp"]["agent-chat"]["command"], [str(CLONE / "bin" / "chat"), "mcp"])
        self.assertEqual(c["mcp"]["agent-chat"]["environment"], {"AGENT_CHAT_PORT": "8765"})
        self.assertEqual(c["tools"], {"skill": False})
        self.assertEqual(list(c["mcp"]), ["agent-chat"])  # nothing of the user's own config
        self.assertEqual(sorted(c["provider"]), ["ac"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_opencode 2>&1 | tail -3`
Expected: `ImportError: cannot import name 'opencode'`.

- [ ] **Step 3: Implement**

`agentchat/opencode.py`:

```python
"""OpenCode agents: which local models they can use, and OpenCode's own config.

The service starts OpenCode with XDG_CONFIG_HOME pointing at this config, so
the user's own OpenCode settings and MCP servers are neither used nor changed.
"""
import json
from pathlib import Path

from . import rating
from .ollama import OllamaError
from .store import write_json

DEFAULT = "qwen3-coder:30b"
CLONE = Path(__file__).resolve().parent.parent


def tool_models(ollama, gpu_total):
    """Installed models that can call tools: the default first, else the
    largest that fits the GPU, else the largest; then by size."""
    found = []
    for m in ollama.tags():
        try:
            if "tools" in (ollama.show(m["name"]).get("capabilities") or []):
                found.append((m["name"], m.get("size", 0)))
        except OllamaError:
            continue
    fits = lambda size: gpu_total and size * rating.MARGIN <= gpu_total * rating.LIMIT
    found.sort(key=lambda x: (x[0] != DEFAULT, not fits(x[1]), -x[1]))
    return [name for name, _ in found]


def config_home(root):
    return Path(root) / "opencode-config"


def write_config(root, ollama_url, models, port):
    """OpenCode's config: agent-chat's Ollama, the chat tools, no skill tool
    (OpenCode would list every installed skill in its prompt)."""
    path = config_home(root) / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"ac": {"npm": "@ai-sdk/openai-compatible", "name": "agent-chat Ollama",
                            "options": {"baseURL": ollama_url.rstrip("/") + "/v1"},
                            "models": {m: {"name": m, "tools": True} for m in models}}},
        "mcp": {"agent-chat": {"type": "local", "enabled": True,
                               "command": [str(CLONE / "bin" / "chat"), "mcp"],
                               "environment": {"AGENT_CHAT_PORT": str(port)}}},
        "tools": {"skill": False}})
    return path
```

(`json` is not needed; remove the import if a linter complains.)

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_opencode -v 2>&1 | tail -3` → `OK`; full suite `OK`.

- [ ] **Step 5: Commit**

```bash
git add agentchat/opencode.py tests/test_opencode.py
git commit -m "OpenCode: tool-capable local models and its own config"
```

---

### Task 3: Kind opencode in the store and the agent tools

**Files:**
- Modify: `agentchat/store.py`, `agentchat/mcp.py`, `tests/test_store.py`, `tests/test_mcp.py`

**Interfaces:**
- Produces: kind `opencode` in `store.KINDS`; `Store.type_in` hook (`type_in(pid, name, m)`, default None) called for woken agents of kind `opencode`; `status()` for kind `opencode`: `needs_you` if flagged, else `busy` if `store.local[(pid, name)]["busy"]`, else `waiting`; `mcp.kind_of("opencode") == "opencode"`; `Session.tools()` offers `spawn` in `chat_join` only to kind `codex`; opencode join text "Chat messages for you are typed into this session as they arrive; reply with chat_post."; no reminder for kind `opencode`.

- [ ] **Step 1: Write the failing tests**

In `tests/test_store.py`, before `def test_remove_and_readd(self):`:

```python
    def test_opencode_agents_are_typed_to(self):
        typed = []
        self.store.type_in = lambda pid, name, m: typed.append((name, m["text"]))
        self.store.add_spawned(self.pid, "t", "opencode", "s")
        self.store.join(self.pid, "kit", "opencode", spawn="t")
        self.store.post(self.pid, "user", "hello")
        self.store.post(self.pid, "kit", "@kit me")
        self.assertEqual(typed, [("kit", "hello")])
        row = lambda: next(a for a in self.store.status(self.pid) if a["name"] == "kit")
        self.assertEqual(row()["status"], "waiting")
        self.store.set_local(self.pid, "kit", busy=True)
        self.assertEqual(row()["status"], "busy")
        self.store.set_needs(self.pid, "t", True)
        self.assertEqual(row()["status"], "needs_you")

```

In `tests/test_mcp.py`, before `def test_name_taken(self):`:

```python
    def test_opencode_session(self):
        s = Session(Client(self.port), str(self.dir), spawn_token="tok")
        self.store.add_spawned("proj", "tok", "opencode", "agent-chat-test-no-such-session")
        init = rpc(s, "initialize", {"clientInfo": {"name": "opencode", "version": "1.18.32"}})
        self.assertEqual(s.kind, "opencode")
        self.assertNotIn("chat wait", init["result"]["instructions"])
        join = next(t for t in rpc(s, "tools/list")["result"]["tools"] if t["name"] == "chat_join")
        self.assertNotIn("spawn", join["inputSchema"]["properties"])
        text, err = tool(s, "chat_join", {"name": "kit"})
        self.assertFalse(err, text)
        self.assertIn("typed into this session", text)
        self.assertEqual(self.store.agents("proj")["kit"]["kind"], "opencode")
        self.assertNotIn("Reminder", tool(s, "chat_post", {"text": "hi"})[0])
        codex, _ = self.codex(None)
        cjoin = next(t for t in rpc(codex, "tools/list")["result"]["tools"] if t["name"] == "chat_join")
        self.assertIn("spawn", cjoin["inputSchema"]["properties"])
```

Also in `test_kind_of`, add `"opencode"` to the inputs and `"opencode"` to the expected list:
`[kind_of(n) for n in ("claude-code", "codex-mcp-client", "opencode", "x", None)]` → `["claude", "codex", "opencode", "llm", "llm"]`.

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_store tests.test_mcp 2>&1 | tail -3` → failures.

- [ ] **Step 3: Implement**

`agentchat/store.py`:

1. `KINDS = {"claude", "codex", "llm"}` → `KINDS = {"claude", "codex", "llm", "opencode"}`, and in `join` the message `"kind must be one of: claude, codex, llm"` → `"kind must be one of: claude, codex, llm, opencode"`.
2. After `        self.talk = None` in `__init__` add `        self.type_in = None  # type_in(pid, name, message): OpenCode agents (spawn.Spawner)`.
3. In `post`, replace

```python
                elif a.get("model") and self.talk:
                    self.talk(pid, name, m)
```

with

```python
                elif a.get("model") and self.talk:
                    self.talk(pid, name, m)
                elif a["kind"] == "opencode" and self.type_in:
                    self.type_in(pid, name, m)
```

4. In `status()`, replace

```python
                elif token and (pid, token) in self.needs:
                    status = "needs_you"
```

with

```python
                elif token and (pid, token) in self.needs:
                    status = "needs_you"
                elif a["kind"] == "opencode":
                    status = "busy" if self.local.get((pid, name), {}).get("busy") else "waiting"
```

`agentchat/mcp.py`:

1. Replace `kind_of`:

```python
def kind_of(client_name):
    n = (client_name or "").lower()
    for kind in ("opencode", "claude", "codex"):
        if kind in n:
            return kind
    return "llm"
```

2. Replace `    def tools(self):\n        return TOOLS if self.project else []` with:

```python
    def tools(self):
        if not self.project:
            return []
        if self.kind == "codex":
            return TOOLS
        # only Codex needs the spawn argument; it confuses other models
        join = dict(TOOLS[0], inputSchema={"type": "object", "required": ["name"],
                                           "properties": {"name": {"type": "string"}}})
        return [join] + TOOLS[1:]
```

3. In `instructions()`, change `if self.kind == "codex":` (the one choosing the Codex text) to `if self.kind in ("codex", "opencode"):` and its text's `"for you are then delivered into this session as they arrive"` stays; for OpenCode it reads correctly as well.
4. In `reminder()`, change `if self.kind == "codex":` to `if self.kind in ("codex", "opencode"):`.
5. In `call` (`chat_join`), replace

```python
                if self.kind != "codex":
                    how = ("Run this as a background command now, and again each time it "
                           "exits:\n%s" % self.wait_command())
```

with

```python
                if self.kind == "opencode":
                    how = ("Chat messages for you are typed into this session as they arrive; "
                           "reply with chat_post.")
                elif self.kind != "codex":
                    how = ("Run this as a background command now, and again each time it "
                           "exits:\n%s" % self.wait_command())
```

- [ ] **Step 4: Run all tests** → `OK`.

- [ ] **Step 5: Commit**

```bash
git add agentchat/store.py agentchat/mcp.py tests/test_store.py tests/test_mcp.py
git commit -m "Kind opencode: typed-to agents in the store, right join text and schema"
```

---

### Task 4: Spawner and routes

**Files:**
- Modify: `agentchat/spawn.py`, `agentchat/ollama.py`, `agentchat/server.py`, `tests/test_spawn.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: Tasks 1–3; `models.Models`; `opencode.tool_models`, `write_config`, `config_home`.
- Produces: `Spawner(store, models=None, port=8765)`; `Spawner.start(pid, tool, model=None)` (records `model`); `Spawner.__call__(pid, name, m)` = `store.type_in`; the poller types one pending message per idle OpenCode agent per poll and sets `store.local` busy; `Spawner.stop` unloads the model unless still used; `Ollama.unload(model)`; `GET /api/opencode/models -> {"models": [...], "default": name|null}`; `POST /api/projects/<id>/spawned {tool, model?}`.

- [ ] **Step 1: Write the failing tests**

Add to `SpawnerTest` in `tests/test_spawn.py` (its `setUp` already puts stub `tmux`, `claude`, `codex` on PATH); add `from tests.fake_ollama import GIB, FakeOllama` and `from agentchat import models as models_mod` to the imports:

```python
    def opencode_setup(self):
        (self.d / "opencode").write_text("#!/bin/sh\nexit 0\n")
        (self.d / "opencode").chmod(0o755)
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.fake.add("coder:30b", size=18 * GIB, capabilities=["tools"])
        self.sp = spawn.Spawner(self.store, models_mod.Models(root=self.store.root,
                                                              ollama_url=self.fake.url), port=8765)
        r = self.sp.start("proj", "opencode", model="coder:30b")
        self.store.join("proj", "kit", "opencode", spawn=r["token"])
        self.store.type_in = self.sp
        return r

    def typed(self):
        return [c for c in calls(self.d) if c.startswith("send-keys") and " -l -- " in c]

    def test_opencode_start_writes_the_config(self):
        r = self.opencode_setup()
        self.assertEqual(r["model"], "coder:30b")
        cfg = self.store.root / "opencode-config" / "opencode" / "opencode.json"
        self.assertIn("coder:30b", cfg.read_text())
        self.assertIn("XDG_CONFIG_HOME=%s" % (self.store.root / "opencode-config"),
                      [c for c in calls(self.d) if c.startswith("new-session")][0])

    def test_types_pending_messages_only_when_idle(self):
        self.opencode_setup()
        (self.d / "screen").write_text((SCREENS / "working-opencode-working.txt").read_text())
        self.store.post("proj", "user", "first")
        self.store.post("proj", "user", "second")
        self.sp.poll()
        self.assertEqual(self.typed(), [])
        self.assertEqual(next(a for a in self.store.status("proj") if a["name"] == "kit")["status"], "busy")
        (self.d / "screen").write_text((SCREENS / "question-opencode-permission.txt").read_text())
        self.sp.poll()
        self.assertEqual(self.typed(), [])
        (self.d / "screen").write_text((SCREENS / "idle-opencode-idle.txt").read_text())
        self.sp.poll()
        self.sp.poll()
        self.sp.poll()
        typed = self.typed()
        self.assertEqual(len(typed), 2)  # each once, oldest first
        self.assertIn("[chat] user: first", typed[0])
        self.assertIn("[chat] user: second", typed[1])
        self.assertEqual(self.store.agents("proj")["kit"]["cursor"], self.store.messages("proj")[-1]["n"])
        self.assertEqual(next(a for a in self.store.status("proj") if a["name"] == "kit")["status"], "waiting")

    def test_stop_unloads_only_an_unused_model(self):
        r = self.opencode_setup()
        self.fake.loaded.add("coder:30b")
        self.store.add_local("proj", "talker", "coder:30b")
        self.sp.stop("proj", r["token"])
        self.assertIn("coder:30b", self.fake.loaded)  # the local member still uses it
        self.store.remove("proj", "talker")
        r2 = self.sp.start("proj", "opencode", model="coder:30b")
        self.sp.stop("proj", r2["token"])
        self.assertNotIn("coder:30b", self.fake.loaded)
```

Add to `ModelRoutesTest` in `tests/test_server.py`:

```python
    def test_opencode_routes(self):
        d = stub_tools(self)
        (d / "opencode").write_text("#!/bin/sh\nexit 0\n")
        (d / "opencode").chmod(0o755)
        self.fake.add("coder:30b", size=18 * GIB, capabilities=["tools"])
        body = self.c.call("GET", "/api/opencode/models")[1]
        self.assertEqual((body["models"], body["default"]), (["coder:30b"], "coder:30b"))
        pdir = Path(tempfile.mkdtemp())
        pid = self.c.call("POST", "/api/projects", {"path": str(pdir)})[1]["project"]["id"]
        for data, code in (({"tool": "opencode"}, 400), ({"tool": "opencode", "model": "qwen3.5:9b"}, 400)):
            with self.assertRaises(ApiError) as e:
                self.c.call("POST", "/api/projects/%s/spawned" % pid, data)
            self.assertEqual(e.exception.code, code)
        status, r = self.c.call("POST", "/api/projects/%s/spawned" % pid,
                                {"tool": "opencode", "model": "coder:30b"})
        self.assertEqual((status, r["spawned"]["model"]), (201, "coder:30b"))
```

(`qwen3.5:9b` in the fake has no `tools` capability, so it is refused.)

- [ ] **Step 2: Run them to verify they fail** → errors (`Spawner()` has no `models`, route 404).

- [ ] **Step 3: Implement**

1. `agentchat/ollama.py`, add after `unload_all`:

```python
    def unload(self, model):
        self.json("POST", "/api/generate", {"model": model, "keep_alive": 0}, timeout=120)
```

2. `agentchat/spawn.py`, replace the `Spawner` class's `__init__` and `start` with:

```python
    def __init__(self, store, models=None, port=8765):
        self.store, self.models, self.port = store, models, port
        self.lock = threading.Lock()  # a rename on join vs the poller's liveness check
        self.pending = {}  # (pid, name) -> messages to type into an OpenCode agent, oldest first

    def __call__(self, pid, name, m):
        """store.type_in: queue a message for an OpenCode agent."""
        with self.lock:
            self.pending.setdefault((pid, name), []).append(m)

    def start(self, pid, tool, model=None):
        project = self.store.project(pid)
        token = secrets.token_hex(16)
        session = "agent-chat-%s-%s" % (pid, token[:6])
        home = None
        if tool == "opencode":
            from . import opencode
            usable = opencode.tool_models(self.models.ollama, self.models.gpu_total())
            if model not in usable:
                raise StoreError(400, "%s cannot call tools or is not installed; choose one of: %s"
                                 % (model, ", ".join(usable) or "none"))
            opencode.write_config(self.store.root, self.models.ollama.url, usable, self.port)
            home = str(opencode.config_home(self.store.root))
        start(tool, project["path"], session, token, model=model, config_home=home)
        record = self.store.add_spawned(pid, token, tool, session)
        if model:
            record = self.store.update_spawned(pid, token, model=model)
        return record
```

3. Replace `Spawner.stop` with:

```python
    def stop(self, pid, token):
        r = self.record(pid, token)
        stop(r["session"])
        self.store.drop_spawned(pid, token)
        if r.get("model") and self.models and not self._model_in_use(r["model"]):
            try:
                self.models.ollama.unload(r["model"])
            except Exception as e:  # stopping still succeeded
                print("agent-chat: could not unload %s: %s" % (r["model"], e), file=sys.stderr)

    def _model_in_use(self, model):
        for p in self.store.projects():
            for a in self.store.agents(p["id"]).values():
                if a.get("model") == model and not a.get("removed") and not a.get("gone"):
                    return True
            if any(r.get("model") == model for r in self.store.spawned(p["id"]).values()):
                return True
        return False
```

4. In `poll`, replace the block from `                if r["name"] and self.store.waiting.get((pid, r["name"])):` through `                self.store.set_needs(pid, token, need)` with:

```python
                if r["name"] and self.store.waiting.get((pid, r["name"])):
                    need = False  # an open chat wait means the agent is idle
                else:
                    try:
                        text = screen(r["session"])
                    except StoreError:
                        continue
                    need = needs_you(text)
                    if not r["name"]:
                        age = time.time() - datetime.fromisoformat(r["started"]).timestamp()
                        need = need or age > JOIN_GRACE
                    elif r["tool"] == "opencode":
                        self._type_pending(pid, r, text, need)
                self.store.set_needs(pid, token, need)
```

and add the method:

```python
    def _type_pending(self, pid, r, text, need):
        """Type the oldest pending message into an idle OpenCode agent."""
        key = (pid, r["name"])
        busy = working(text)
        with self.lock:
            queue = self.pending.get(key) or []
            m = queue.pop(0) if queue and not busy and not need else None
            left = bool(queue)
        if m:
            send_text(r["session"], format_message(m, r["name"]))
            self.store.delivered(pid, r["name"], m["n"])
        self.store.set_local(pid, r["name"], busy=busy or left or bool(m))
```

5. `agentchat/server.py`:
   - In `serve()`, move `models = models or models_mod.Models(store.root)` above `spawner = spawn.Spawner(store)`, and change that line to `spawner = spawn.Spawner(store, models, server.server_address[1])`; in the `if deliver:` block add `store.type_in = spawner`.
   - Replace `return 201, {"spawned": spawner.start(pid, self.field(self.body(), "tool"))}` with:

```python
                    data = self.body()
                    return 201, {"spawned": spawner.start(pid, self.field(data, "tool"),
                                                          data.get("model"))}
```

   - After the `if rest == ["tools"] and method == "GET":` block add:

```python
            if rest == ["opencode", "models"] and method == "GET":
                from . import opencode
                names = opencode.tool_models(models.ollama, models.gpu_total())
                return 200, {"models": names, "default": names[0] if names else None}
```

- [ ] **Step 4: Run all tests** → `OK`.

- [ ] **Step 5: Commit**

```bash
git add agentchat/spawn.py agentchat/ollama.py agentchat/server.py tests/test_spawn.py tests/test_server.py
git commit -m "OpenCode agents: start with a model and config, typed messages, unload on Stop"
```

---

### Task 5: The page

**Files:**
- Modify: `agentchat/page.html`

- [ ] **Step 1: Models for OpenCode**

In `loadLocals()`, after the line setting `locals={ok:true,…}`, add `oc=await api('api/opencode/models').catch(()=>({models:[],default:null}));`, declare `let oc={models:[],default:null};` next to `let locals=…`, and in the `catch` of `loadLocals` add `oc={models:[],default:null};`.

- [ ] **Step 2: Menu entries**

In `startItems()`, after the `items` are built from `['claude','codex']` and before the local-model entries, add:

```js
  const ocOff=!cur?'Add a project first':!tools.tmux?'tmux is not installed':!tools.opencode?'OpenCode is not installed':
    !locals.ok?locals.reason:'';
  if(ocOff||!oc.models.length)items.push(['Start OpenCode',()=>{},ocOff||'No model that can call tools; get one in Models']);
  else for(const m of oc.models)items.push(['Start OpenCode: '+m+(m===oc.default?' (default)':''),
    ()=>api(`api/projects/${cur}/spawned`,{tool:'opencode',model:m}),'']);
```

and add `opencode:'OpenCode'` to the `KIND` object.

- [ ] **Step 3: Member line and /start**

1. In the member line, change `a.model?'local LLM · '+a.model:KIND[a.kind]||a.kind` to `a.model?'local LLM · '+a.model:a.kind==='opencode'?'OpenCode · '+((spawned.find(s=>s.token===a.spawn)||{}).model||''):KIND[a.kind]||a.kind`.
2. In `runCommand`'s `start` branch, replace the tool check with:

```js
  if(cmd==='start'){
    if(arg==='opencode'){const model=arg2||oc.default;if(!model)throw new Error('No model that can call tools; get one in Models.');
      await api(`api/projects/${cur}/spawned`,{tool:'opencode',model});return `Starting OpenCode with ${model}…`;}
    if(!['claude','codex'].includes(arg))throw new Error('Use /start claude, /start codex or /start opencode [model].');
    await api(`api/projects/${cur}/spawned`,{tool:arg});return `Starting ${KIND[arg]}…`;}
```

3. In the overlay, change `start Claude in this project (or <code>/start codex</code>)` to `start Claude in this project (or <code>/start codex</code>, <code>/start opencode [model]</code>)`.

- [ ] **Step 4: Check syntax; run tests** → `node --check` on the extracted script; suite `OK`.

- [ ] **Step 5: Browser check**

A demo service (scratchpad script) with a `FakeOllama` holding `coder:30b` (tools) and `talker` (no tools), stub `tmux`/`opencode` on the service's PATH (so nothing real starts), `deliver=True`. Check: the menu shows **Start OpenCode: coder:30b (default)** and no entry for `talker`; choosing it creates a "starting…" entry; joining it through the API with its token (`{"name":"kit","kind":"opencode","spawn":…}`) shows `kit` · "OpenCode · coder:30b"; `/start opencode` works; with the stub `opencode` removed from PATH, the entry is greyed "OpenCode is not installed" after a reload. Stop the demo by PID.

- [ ] **Step 6: Commit**

```bash
git add agentchat/page.html
git commit -m "Page: Start OpenCode with a local model, its member line, /start opencode"
```

---

### Task 6: Docs, deploy, live check

- [ ] **Step 1: README** — under "Use", after the local-member item, add:

```markdown
7. Start a local coding agent: **+ Agent → Start OpenCode: <model>** (or
   `/start opencode [model]`). OpenCode runs in tmux with a local model from
   agent-chat's Ollama and its own config (your OpenCode settings are not
   used), joins the chat, and gets chat messages typed into its terminal when
   it is idle. `qwen3-coder:30b` works best in tests; Stop unloads its model
   when nothing else uses it.
```

- [ ] **Step 2:** commit; after the final review and its fixes merge into `main`, push, `systemctl --user restart agent-chat`.

- [ ] **Step 3: Live check with the user** in a scratch project registered with `chat add`: start OpenCode with `qwen3-coder:30b`, ask it in the chat to create a small file, answer one permission question in View terminal, see the reply in the chat, Stop, and see the model unloaded (`Models → Installed`). Record a "Verified" line in the spec; commit; push.
