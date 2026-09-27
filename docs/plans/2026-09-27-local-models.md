# Local models (runtime and Model Hub) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** agent-chat installs and runs its own tuned Ollama (or uses an existing one), and the page gets a Models panel: Hugging Face search with fit verdicts and scores, one-click GGUF import, pull, benchmark (with thinking off and on), per-model context and thinking settings, and Unload now.

**Architecture:** `agentchat/config.py` holds the Ollama URL setting; `agentchat/ollama.py` is a small client for Ollama's native API; `agentchat/rating.py` ports local-ai-chat's Model Hub rules; `agentchat/models.py` has the GPU reading, tuning store, Hugging Face search, jobs, import/pull/benchmark and the `Models` facade the server uses. `install.sh` downloads the pinned Ollama and runs it as `agent-chat-ollama.service`. The page loads the panel from `agentchat/models.js`.

**Tech Stack:** Python ≥ 3.9 stdlib (urllib, threading, hashlib, concurrent.futures), bash, systemd user units, vanilla JS.

**Spec:** `docs/specs/2026-09-27-local-models-design.md`

## Global Constraints

- Python 3.9+ standard library only.
- Ollama `v0.34.2`, `ollama-linux-amd64.tar.zst`, SHA-256 `e155b83589986d2c581fdbf1381ea3ebdb16549883679cd5a0627f7cdc05b12b` (1 427 542 079 bytes, from GitHub's release asset digest).
- Own Ollama on `127.0.0.1:11436`; env for tests: `AGENT_CHAT_OLLAMA_PORT`, `AGENT_CHAT_OLLAMA_DOWNLOAD`, `AGENT_CHAT_OLLAMA_SHA256`, `AGENT_CHAT_RUNTIME` (runtime folder; default `<clone>/runtime`). Tests must never touch the real `<clone>/runtime`.
- Service env: `OLLAMA_NUM_PARALLEL=1 OLLAMA_MAX_LOADED_MODELS=1 OLLAMA_CONTEXT_LENGTH=32768 OLLAMA_KEEP_ALIVE=5m OLLAMA_FLASH_ATTENTION=1 OLLAMA_KV_CACHE_TYPE=q8_0`.
- External Ollama URL must match `http://(127.0.0.1|localhost):<port>`.
- Rating numbers exactly as the spec (margin 1.2, limit 0.9, fit thresholds, quant table, score parts, labels).
- Import: `https://huggingface.co/.../resolve/...` only, plain `.gguf` file name, free disk ≥ 3.1 × size.
- Context 512–131072; thinking values `off`, `on` (on/off models) or `low`/`medium`/`high` (level models); default `off` for use `talk`, `on` for `agent`; `on` for a level model means `medium`.
- Files under 500 lines; the Models panel's JS lives in `agentchat/models.js`.
- Tests: `python3 -m unittest`; no network beyond 127.0.0.1.

## Review Focus

- A Hugging Face file listed inside a folder (`sub/model-Q4_K_M.gguf`) must not be picked as the best file, since the import refuses such names (test in Task 3).
- A model whose `/api/show` fails (for example it is being deleted) must not break the Installed list (test in Task 4).
- Cancelling an import mid-download must delete the partial file (test in Task 5).
- A second import of the same model while one runs must be refused, not run twice (test in Task 5).
- Running the install tests must not create or delete the real `runtime/` folder (test in Task 2).

## File map

| File | Responsibility |
|---|---|
| `agentchat/config.py` | new: `config.json`, Ollama URL setting |
| `agentchat/ollama.py` | new: Ollama client |
| `agentchat/rating.py` | new: best file, fit, score, suggested name |
| `agentchat/models.py` | new: GPU, context, thinking, tuning store, Hub search, jobs, import/pull/benchmark, `Models` |
| `agentchat/models.js` | new: the Models panel |
| `agentchat/server.py` | model routes, `/models.js` |
| `agentchat/page.html` | Models button, panel markup, CSS, script tag |
| `bin/chat` | `chat config ollama-url [<url>|own]` |
| `install.sh` | Ollama download, `agent-chat-ollama.service`, `--ollama-url`, uninstall |
| `.gitignore` | `runtime/` |
| `tests/fake_ollama.py` | new: fake Ollama and fake file host |
| `tests/test_config.py`, `test_rating.py`, `test_models.py` | new |
| `tests/test_install.py`, `test_server.py`, `helpers.py` | additions |

---

### Task 1: The Ollama URL setting

**Files:**
- Create: `agentchat/config.py`, `tests/test_config.py`
- Modify: `bin/chat`

**Interfaces:**
- Produces: `config.OWN_OLLAMA` (`"http://127.0.0.1:%s" % os.environ.get("AGENT_CHAT_OLLAMA_PORT", "11436")`, read at import), `config.config_path() -> Path`, `config.load() -> dict`, `config.ollama_url() -> str`, `config.set_ollama_url(url)` (`"own"` clears; `ValueError` otherwise); CLI `chat config ollama-url` prints the URL in use, `chat config ollama-url <url|own>` sets it (exit 2 with the message on a bad URL).

- [ ] **Step 1: Write the failing tests**

`tests/test_config.py`:

```python
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentchat import config

CHAT = str(Path(__file__).resolve().parent.parent / "bin" / "chat")


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self.addCleanup(lambda: os.environ.__setitem__("XDG_CONFIG_HOME", old) if old
                        else os.environ.pop("XDG_CONFIG_HOME", None))

    def test_default_is_own_ollama(self):
        self.assertEqual(config.ollama_url(), config.OWN_OLLAMA)
        self.assertTrue(config.OWN_OLLAMA.startswith("http://127.0.0.1:"))

    def test_set_external_and_back(self):
        config.set_ollama_url("http://localhost:11434/")
        self.assertEqual(config.ollama_url(), "http://localhost:11434")
        self.assertEqual(config.load(), {"ollama_url": "http://localhost:11434"})
        config.set_ollama_url("own")
        self.assertEqual(config.ollama_url(), config.OWN_OLLAMA)

    def test_refuses_non_local_urls(self):
        for bad in ("http://evil.example:11434", "https://127.0.0.1:1", "127.0.0.1:11434", ""):
            with self.assertRaises(ValueError):
                config.set_ollama_url(bad)

    def test_a_broken_file_means_defaults(self):
        config.config_path().parent.mkdir(parents=True)
        config.config_path().write_text("{not json")
        self.assertEqual(config.ollama_url(), config.OWN_OLLAMA)

    def test_cli(self):
        env = dict(os.environ, XDG_CONFIG_HOME=self.tmp)
        run = lambda *a: subprocess.run([CHAT, "config", "ollama-url", *a], env=env,
                                        capture_output=True, text=True)
        self.assertEqual(run("http://127.0.0.1:11434").returncode, 0)
        self.assertEqual(run().stdout.strip(), "http://127.0.0.1:11434")
        bad = run("http://evil.example:1")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("must be", bad.stderr)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_config`
Expected: `ImportError: cannot import name 'config' from 'agentchat'`

- [ ] **Step 3: Implement**

`agentchat/config.py`:

```python
"""agent-chat settings, in ${XDG_CONFIG_HOME:-~/.config}/agent-chat/config.json."""
import json
import os
import re
from pathlib import Path

OWN_OLLAMA = "http://127.0.0.1:%s" % os.environ.get("AGENT_CHAT_OLLAMA_PORT", "11436")
LOCAL_URL = re.compile(r"^http://(?:127\.0\.0\.1|localhost):\d{1,5}$")


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.expanduser("~/.config")
    return Path(base) / "agent-chat" / "config.json"


def load():
    try:
        data = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ollama_url():
    """The Ollama in use: the configured one, else agent-chat's own."""
    url = load().get("ollama_url")
    return url if isinstance(url, str) and LOCAL_URL.match(url) else OWN_OLLAMA


def set_ollama_url(url):
    """Use the Ollama at url; "own" goes back to agent-chat's own."""
    data = load()
    if url == "own":
        data.pop("ollama_url", None)
    elif isinstance(url, str) and LOCAL_URL.match(url.rstrip("/")):
        data["ollama_url"] = url.rstrip("/")
    else:
        raise ValueError("the Ollama URL must be http://127.0.0.1:<port> or "
                         "http://localhost:<port>, or own")
    path = config_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)
```

In `bin/chat`:
1. In the docstring, after the `chat mcp` line add:
   `  chat config ollama-url [<url>|own]  show or set the Ollama agent-chat uses`
2. After `    sub.add_parser("mcp")` add:

```python
    cfg = sub.add_parser("config")
    cfg.add_argument("key", choices=["ollama-url"])
    cfg.add_argument("value", nargs="?")
```

3. After the `if args.cmd == "mcp":` block add:

```python
    if args.cmd == "config":
        from agentchat import config
        if args.value is None:
            print(config.ollama_url())
            return
        try:
            config.set_ollama_url(args.value)
        except ValueError as e:
            print("chat: %s" % e, file=sys.stderr)
            sys.exit(2)
        return
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python3 -m unittest tests.test_config -v`
Expected: all `ok`.

- [ ] **Step 5: Commit**

```bash
git add agentchat/config.py tests/test_config.py bin/chat
git commit -m "Setting for the Ollama agent-chat uses"
```

---

### Task 2: install.sh installs and runs Ollama

**Files:**
- Modify: `install.sh`, `.gitignore`, `tests/test_install.py`
- Create: `tests/fake_ollama.py`

**Interfaces:**
- Consumes: `chat config ollama-url` (Task 1).
- Produces: `./install.sh [--uninstall | --ollama-url <url|own>]`; `agent-chat-ollama.service`; `<runtime>/ollama/bin/ollama` and `<runtime>/ollama/.version`; `tests.fake_ollama.FakeOllama` (`.url`, `.add(name, size=, capabilities=, arch=, ctx=)`, `.models`, `.loaded`, `.blobs`, `.calls`, `.fail_chat`, `.close()`) and `tests.fake_ollama.FileHost(data, chunk_delay=0)` (`.url_for(path)`, `.close()`).

- [ ] **Step 1: The fake servers**

`tests/fake_ollama.py`:

```python
"""A fake Ollama and a fake file host for tests (local http.server)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GIB = 1024 ** 3


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class FakeOllama:
    def __init__(self):
        self.models, self.loaded, self.blobs, self.calls = {}, set(), set(), []
        self.fail_chat = None
        self.server = _serve(self._handler())
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add(self, name, size=4 * GIB, capabilities=("completion",), arch="llama", ctx=131072):
        self.models[name] = {"size": size, "capabilities": list(capabilities),
                             "arch": arch, "ctx": ctx}

    def _handler(fake):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body=None, lines=None):
                if lines is not None:
                    data = b"".join(json.dumps(x).encode() + b"\n" for x in lines)
                else:
                    data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_HEAD(self):
                fake.calls.append(("HEAD", self.path))
                self.send_response(200 if self.path.rsplit("/", 1)[-1] in fake.blobs else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                fake.calls.append(("GET", self.path))
                if self.path == "/api/version":
                    return self.reply(200, {"version": "0.34.2"})
                if self.path == "/api/tags":
                    return self.reply(200, {"models": [{"name": n, "size": m["size"]}
                                                       for n, m in fake.models.items()]})
                if self.path == "/api/ps":
                    return self.reply(200, {"models": [{"name": n} for n in sorted(fake.loaded)]})
                self.reply(404, {"error": "not found"})

            def do_DELETE(self):
                data = json.loads(self.body() or b"{}")
                fake.calls.append(("DELETE", self.path, data))
                if fake.models.pop(data.get("model"), None) is None:
                    return self.reply(404, {"error": "model not found"})
                self.reply(200)

            def do_POST(self):
                raw = self.body()
                if self.path.startswith("/api/blobs/"):
                    fake.calls.append(("POST", self.path, len(raw)))
                    fake.blobs.add(self.path.rsplit("/", 1)[-1])
                    return self.reply(201)
                data = json.loads(raw or b"{}")
                fake.calls.append(("POST", self.path, data))
                name = data.get("model")
                if self.path == "/api/show":
                    m = fake.models.get(name)
                    if not m:
                        return self.reply(404, {"error": "model '%s' not found" % name})
                    info = {"general.architecture": m["arch"],
                            "%s.context_length" % m["arch"]: m["ctx"]}
                    return self.reply(200, {"capabilities": m["capabilities"], "model_info": info})
                if self.path == "/api/generate":
                    if data.get("keep_alive") == 0:
                        fake.loaded.discard(name)
                    return self.reply(200, {"done": True})
                if self.path == "/api/chat":
                    if fake.fail_chat:
                        return self.reply(500, {"error": fake.fail_chat})
                    fake.loaded.add(name)
                    think = data.get("think")
                    content = "391" if "17" in data["messages"][-1]["content"] else "benchmark ok"
                    message = {"role": "assistant", "content": content}
                    if think:
                        message["thinking"] = "x" * 300
                    return self.reply(200, {"message": message, "eval_count": 40 if think else 4,
                                            "eval_duration": 2 * 10 ** 8})
                if self.path == "/api/create":
                    fake.add(name)
                    return self.reply(200, lines=[{"status": "creating"}, {"status": "success"}])
                if self.path == "/api/pull":
                    if name == "bad":
                        return self.reply(200, lines=[{"error": "file does not exist"}])
                    fake.add(name)
                    return self.reply(200, lines=[{"status": "pulling", "completed": 5, "total": 10},
                                                  {"status": "success"}])
                self.reply(404, {"error": "not found"})
        return Handler


class FileHost:
    """Serves `data` at any path, in 64 KiB chunks, chunk_delay seconds apart."""

    def __init__(self, data, chunk_delay=0):
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(host.data)))
                self.end_headers()
                for i in range(0, len(host.data), 65536):
                    try:
                        self.wfile.write(host.data[i:i + 65536])
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    time.sleep(host.chunk_delay)

        self.data, self.chunk_delay = data, chunk_delay
        self.server = _serve(Handler)

    def url_for(self, path):
        return "http://127.0.0.1:%d/%s" % (self.server.server_address[1], path.lstrip("/"))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
```

- [ ] **Step 2: Write the failing install tests**

In `tests/test_install.py`:

1. Add imports at the top: `import hashlib` and `from tests.fake_ollama import FakeOllama`.
2. In `setUp`, after `self.addCleanup(stop, self.server)`, add:

```python
        # a fake Ollama release and a fake running Ollama, so nothing is downloaded
        # and the real <clone>/runtime is never touched
        tmp = self.home.parent
        (tmp / "rel" / "bin").mkdir(parents=True)
        (tmp / "rel" / "bin" / "ollama").write_text("#!/bin/sh\nexit 0\n")
        (tmp / "rel" / "bin" / "ollama").chmod(0o755)
        self.tarball = tmp / "ollama.tar.zst"
        subprocess.run(["tar", "--zstd", "-cf", str(self.tarball), "-C", str(tmp / "rel"), "."],
                       check=True)
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.close)
        self.runtime = tmp / "runtime"
```

3. Replace the `install()` method's env with:

```python
        env = {"HOME": str(self.home), "PATH": "%s:/usr/bin:/bin" % self.stubs,
               "AGENT_CHAT_PORT": str(self.port),
               "AGENT_CHAT_RUNTIME": str(self.runtime),
               "AGENT_CHAT_OLLAMA_DOWNLOAD": "file://%s" % self.tarball,
               "AGENT_CHAT_OLLAMA_SHA256": hashlib.sha256(self.tarball.read_bytes()).hexdigest(),
               "AGENT_CHAT_OLLAMA_PORT": self.ollama.url.rsplit(":", 1)[1]}
        env.update(getattr(self, "extra_env", {}))
```

4. Add these tests before `def test_missing_tool_is_skipped(self):`:

```python
    def test_installs_its_own_ollama_once(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.runtime / "ollama" / "bin" / "ollama").exists())
        self.assertEqual((self.runtime / "ollama" / ".version").read_text().strip(), "v0.34.2")
        unit = (self.home / ".config/systemd/user/agent-chat-ollama.service").read_text()
        for line in ("OLLAMA_HOST=127.0.0.1:%s" % self.ollama.url.rsplit(":", 1)[1],
                     "OLLAMA_FLASH_ATTENTION=1", "OLLAMA_KV_CACHE_TYPE=q8_0",
                     "OLLAMA_NUM_PARALLEL=1", "OLLAMA_MAX_LOADED_MODELS=1",
                     "OLLAMA_CONTEXT_LENGTH=32768", "OLLAMA_KEEP_ALIVE=5m",
                     "agent-chat/ollama-models"):
            self.assertIn(line, unit)
        self.assertIn("systemctl --user restart agent-chat-ollama", self.calls_made())
        again = self.install()
        self.assertIn("already installed", again.stdout)

    def test_a_bad_checksum_installs_nothing(self):
        self.extra_env = {"AGENT_CHAT_OLLAMA_SHA256": "0" * 64}
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("checksum", r.stderr)
        self.assertFalse((self.runtime / "ollama").exists())

    def test_external_ollama_and_back(self):
        r = self.install("--ollama-url", "http://127.0.0.1:11434")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("using http://127.0.0.1:11434", r.stdout)
        self.assertFalse((self.runtime / "ollama").exists())
        self.assertNotIn("systemctl --user restart agent-chat-ollama", self.calls_made())
        cfg = self.home / ".config/agent-chat/config.json"
        self.assertIn("11434", cfg.read_text())
        r = self.install("--ollama-url", "own")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.runtime / "ollama").exists())
        self.assertEqual(self.install("--ollama-url", "http://evil.example:1").returncode, 2)

    def test_uninstall_keeps_the_models(self):
        self.install()
        models = self.home / ".local/share/agent-chat/ollama-models"
        self.assertTrue(models.is_dir())
        r = self.install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.runtime / "ollama").exists())
        self.assertFalse((self.home / ".config/systemd/user/agent-chat-ollama.service").exists())
        self.assertTrue(models.is_dir())

    def test_tests_leave_the_real_runtime_alone(self):
        before = (ROOT / "runtime").exists()
        self.install()
        self.install("--uninstall")
        self.assertEqual((ROOT / "runtime").exists(), before)
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python3 -m unittest tests.test_install 2>&1 | tail -3`
Expected: failures (no `agent-chat-ollama.service`, no `--ollama-url`).

- [ ] **Step 4: Rewrite install.sh**

Replace `install.sh` with:

```bash
#!/usr/bin/env bash
# Install agent-chat for the current user (Linux with systemd). Safe to rerun.
#   ./install.sh                     install or update
#   ./install.sh --ollama-url <url>  use an existing Ollama (http://127.0.0.1:<port>),
#                                    or "own" for agent-chat's own
#   ./install.sh --uninstall         remove; chats and models stay in ~/.local/share/agent-chat
set -euo pipefail
here=$(cd "$(dirname "$(readlink -f "$0")")" && pwd)
bin=$HOME/.local/bin
units=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
unit=$units/agent-chat.service
ollama_unit=$units/agent-chat-ollama.service
data=${XDG_DATA_HOME:-$HOME/.local/share}/agent-chat
port=${AGENT_CHAT_PORT:-8765}
runtime=${AGENT_CHAT_RUNTIME:-$here/runtime}
ollama_version=v0.34.2
ollama_sha256=${AGENT_CHAT_OLLAMA_SHA256:-e155b83589986d2c581fdbf1381ea3ebdb16549883679cd5a0627f7cdc05b12b}
ollama_download=${AGENT_CHAT_OLLAMA_DOWNLOAD:-https://github.com/ollama/ollama/releases/download/$ollama_version/ollama-linux-amd64.tar.zst}
ollama_port=${AGENT_CHAT_OLLAMA_PORT:-11436}
say() { printf '  %s\n' "$*"; }
usage() { echo "usage: ./install.sh [--uninstall | --ollama-url <url>|own]" >&2; exit 2; }
# ~/.local/bin/chat may be replaced if it is missing, a dangling link, or ours
replaceable() {
    [[ ! -e $1 ]] && return 0
    [[ -L $1 ]] && [[ -f $(dirname "$(readlink -f "$1")")/../agentchat/store.py ]]
}
wait_for() {  # url [tries]: poll every 0.5 s
    for _ in $(seq "${2:-20}"); do
        if python3 - "$1" 2>/dev/null <<'PY'
import sys, urllib.request
urllib.request.urlopen(urllib.request.Request(sys.argv[1], headers={"X-Agent-Chat": "1"}), timeout=2)
PY
        then return 0; fi
        sleep 0.5
    done
    return 1
}

mode=install url=
case ${1:-} in
    "") ;;
    --uninstall) mode=uninstall ;;
    --ollama-url) [[ -n ${2:-} ]] || usage; url=$2 ;;
    *) usage ;;
esac

if [[ $mode == uninstall ]]; then
    for u in agent-chat agent-chat-ollama; do
        systemctl --user disable --now "$u" >/dev/null 2>&1 || true
    done
    rm -f "$unit" "$ollama_unit"
    systemctl --user daemon-reload >/dev/null 2>&1 || true
    rm -rf "$runtime/ollama" "$runtime/ollama.new"
    if [[ $(readlink "$bin/chat" || true) == "$here/bin/chat" ]]; then rm "$bin/chat"; fi
    if command -v claude >/dev/null; then claude mcp remove --scope user agent-chat >/dev/null 2>&1 || true; fi
    if command -v codex >/dev/null; then codex mcp remove agent-chat >/dev/null 2>&1 || true; fi
    echo "agent-chat removed; chats and models kept in $data"
    exit 0
fi

echo "Installing agent-chat from $here"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 9))' 2>/dev/null ||
    { echo "agent-chat needs python3 3.9 or newer" >&2; exit 1; }
mkdir -p "$bin" "$units"
if replaceable "$bin/chat"; then
    ln -sfn "$here/bin/chat" "$bin/chat"
    say "linked $bin/chat"
else
    say "warning: $bin/chat is another program; left it alone (run $here/bin/chat instead)"
fi
case ":$PATH:" in *":$bin:"*) ;; *) say "warning: $bin is not on your PATH" ;; esac

# Ollama: agent-chat's own, or the one the setting names
if [[ -n $url ]]; then
    AGENT_CHAT_OLLAMA_PORT=$ollama_port "$here/bin/chat" config ollama-url "$url" || exit 2
fi
in_use=$(AGENT_CHAT_OLLAMA_PORT=$ollama_port "$here/bin/chat" config ollama-url)
if [[ $in_use == "http://127.0.0.1:$ollama_port" ]]; then
    if [[ $(cat "$runtime/ollama/.version" 2>/dev/null) == "$ollama_version" ]]; then
        say "Ollama $ollama_version already installed"
    else
        for tool in curl tar zstd sha256sum; do
            command -v "$tool" >/dev/null || { echo "installing Ollama needs $tool" >&2; exit 1; }
        done
        say "downloading Ollama $ollama_version (about 1.4 GB)"
        mkdir -p "$runtime"
        tarball=$runtime/ollama.tar.zst.part
        curl -fL --progress-bar -o "$tarball" "$ollama_download"
        if [[ $(sha256sum "$tarball" | cut -d' ' -f1) != "$ollama_sha256" ]]; then
            rm -f "$tarball"
            echo "the Ollama download failed its checksum; nothing installed" >&2
            exit 1
        fi
        rm -rf "$runtime/ollama.new"
        mkdir -p "$runtime/ollama.new"
        tar --zstd -xf "$tarball" -C "$runtime/ollama.new"
        rm -f "$tarball"
        echo "$ollama_version" >"$runtime/ollama.new/.version"
        rm -rf "$runtime/ollama"
        mv "$runtime/ollama.new" "$runtime/ollama"
        say "unpacked Ollama to $runtime/ollama"
    fi
    mkdir -p "$data/ollama-models"
    cat >"$ollama_unit" <<EOF
[Unit]
Description=agent-chat's Ollama (127.0.0.1:$ollama_port)

[Service]
ExecStart="$runtime/ollama/bin/ollama" serve
Environment=OLLAMA_HOST=127.0.0.1:$ollama_port
Environment="OLLAMA_MODELS=$data/ollama-models"
Environment=OLLAMA_NUM_PARALLEL=1
Environment=OLLAMA_MAX_LOADED_MODELS=1
Environment=OLLAMA_CONTEXT_LENGTH=32768
Environment=OLLAMA_KEEP_ALIVE=5m
Environment=OLLAMA_FLASH_ATTENTION=1
Environment=OLLAMA_KV_CACHE_TYPE=q8_0
Restart=on-failure
RestartSec=5
KillSignal=SIGINT
TimeoutStopSec=60

[Install]
WantedBy=default.target
EOF
    systemctl --user daemon-reload
    systemctl --user enable agent-chat-ollama
    systemctl --user restart agent-chat-ollama
    wait_for "http://127.0.0.1:$ollama_port/api/version" 60 ||
        { echo "agent-chat's Ollama did not start; see: journalctl --user -u agent-chat-ollama" >&2; exit 1; }
    say "Ollama running on http://127.0.0.1:$ollama_port"
else
    systemctl --user disable --now agent-chat-ollama >/dev/null 2>&1 || true
    rm -f "$ollama_unit"
    say "Ollama: using $in_use (agent-chat's own is not installed)"
fi

cat >"$unit" <<EOF
[Unit]
Description=agent-chat service (127.0.0.1:$port)

[Service]
Environment=AGENT_CHAT_PORT=$port
Environment=AGENT_CHAT_OLLAMA_PORT=$ollama_port
Environment=PATH=$PATH
ExecStart=/usr/bin/env python3 "$here/bin/chat" serve
Restart=on-failure
RestartSec=5
# agents started from the page live in tmux servers this service may start;
# a restart must stop only the service, not them
KillMode=process

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable agent-chat
systemctl --user restart agent-chat
wait_for "http://127.0.0.1:$port/api/projects" ||
    { echo "agent-chat did not start on port $port (is something else using it?);" \
           "see: journalctl --user -u agent-chat" >&2; exit 1; }
say "service agent-chat running on http://127.0.0.1:$port"

for tool in claude codex; do
    if ! command -v "$tool" >/dev/null; then say "$tool not found: skipped"; continue; fi
    if "$tool" mcp get agent-chat >/dev/null 2>&1; then
        say "$tool: agent-chat already registered"
    elif [[ $tool == claude ]]; then
        claude mcp add --scope user agent-chat -- "$here/bin/chat" mcp >/dev/null
        say "claude: registered agent-chat for all your sessions"
    else
        codex mcp add agent-chat -- "$here/bin/chat" mcp >/dev/null
        say "codex: registered agent-chat for all your sessions"
    fi
done
echo "Done. Open http://127.0.0.1:$port and add a project, or run: chat add <folder>"
```

Add `runtime/` to `.gitignore`.

- [ ] **Step 5: Run the tests to verify they pass**

Run: `python3 -m unittest tests.test_install -v 2>&1 | tail -4`
Expected: all `ok`.

- [ ] **Step 6: Commit**

```bash
git add install.sh .gitignore tests/test_install.py tests/fake_ollama.py
git commit -m "install.sh: agent-chat's own tuned Ollama, or an existing one"
```

---

### Task 3: Ratings (ported from local-ai-chat)

**Files:**
- Create: `agentchat/rating.py`, `tests/test_rating.py`

**Interfaces:**
- Produces: `rating.GIB`, `MARGIN`, `LIMIT`, `file_size(f)`, `quant_quality(name)`, `usable(f)`, `best_file(model, gpu_bytes=0) -> dict|None`, `fit(size, gpu_bytes) -> (points, verdict)`, `community(model)`, `freshness(last_modified, now=None)`, `suggest_name(repo_id, filename) -> str`, `rate(model, gpu_bytes, now=None) -> {"id","score","label","file","size","verdict","downloads","likes","name","url"}`. `model` is a Hugging Face record: `id, downloads, likes, trendingScore, lastModified, siblings[{rfilename, size|lfs.size}]`.

- [ ] **Step 1: Write the failing tests**

`tests/test_rating.py`:

```python
import time
import unittest
from datetime import datetime, timezone

from agentchat import rating

GIB = rating.GIB
NOW = datetime.now(timezone.utc).isoformat()
CHAT = {"id": "example/chat-GGUF", "downloads": 100000, "likes": 500, "trendingScore": 50,
        "lastModified": NOW, "siblings": [
            {"rfilename": "model-Q8_0.gguf", "size": 12 * GIB},
            {"rfilename": "model-Q5_K_M.gguf", "size": 8 * GIB},
            {"rfilename": "model-Q4_K_M.gguf", "size": 6 * GIB},
            {"rfilename": "model-mmproj-Q8_0.gguf", "size": GIB},
            {"rfilename": "model-Q8_0-mtp.gguf", "size": GIB},
            {"rfilename": "model-Q8_0-00001-of-00002.gguf", "size": 6 * GIB},
            {"rfilename": "sub/model-F16.gguf", "size": GIB}]}


class RatingTest(unittest.TestCase):
    # the first cases are local-ai-chat's test-recommendations.mjs, ported
    def test_best_file_fits_and_has_the_best_quant(self):
        self.assertEqual(rating.best_file(CHAT, 11 * GIB)["rfilename"], "model-Q5_K_M.gguf")

    def test_rating_of_a_good_model(self):
        r = rating.rate(CHAT, 11 * GIB)
        self.assertEqual(r["file"], "model-Q5_K_M.gguf")
        self.assertGreaterEqual(r["score"], 70)
        self.assertEqual(r["name"], "chat:q5_k_m")
        self.assertEqual(r["url"], "https://huggingface.co/example/chat-GGUF/resolve/main/model-Q5_K_M.gguf")

    def test_oversized_scores_lower(self):
        huge = dict(CHAT, siblings=[{"rfilename": "huge-Q8_0.gguf", "size": 30 * GIB}])
        r = rating.rate(huge, 10 * GIB)
        self.assertEqual(r["verdict"], "Won't fit GPU memory")
        self.assertLess(r["score"], rating.rate(CHAT, 11 * GIB)["score"])

    def test_files_in_folders_are_not_picked(self):
        only_sub = dict(CHAT, siblings=[{"rfilename": "sub/model-Q4_K_M.gguf", "size": GIB}])
        self.assertIsNone(rating.best_file(only_sub, 16 * GIB))
        self.assertEqual(rating.rate(only_sub, 16 * GIB)["label"], "Poor")

    def test_fit_boundaries(self):
        gpu = 100 * GIB
        cases = [(0.55, 45, "Plenty of room"), (0.75, 40, "Comfortable"), (0.9, 33, "Good fit"),
                 (1.0, 20, "Tight fit"), (1.01, 0, "Won't fit GPU memory")]
        for ratio, points, verdict in cases:
            self.assertEqual(rating.fit(ratio * gpu / rating.MARGIN, gpu), (points, verdict), ratio)
        self.assertEqual(rating.fit(0, gpu), (10, "Unknown fit"))
        self.assertEqual(rating.fit(GIB, 0), (10, "Unknown fit"))

    def test_quant_table(self):
        for name, q in (("x-F16.gguf", 100), ("x-Q8_0.gguf", 92), ("x-Q6_K.gguf", 86),
                        ("x-Q5_K_M.gguf", 82), ("x-Q4_K_M.gguf", 73), ("x-IQ4_XS.gguf", 68),
                        ("x-Q3_K_L.gguf", 57), ("x-Q2_K.gguf", 46), ("x.gguf", 60)):
            self.assertEqual(rating.quant_quality(name), q, name)

    def test_freshness_and_community(self):
        now = time.time()
        day = lambda d: datetime.fromtimestamp(now - d * 86400, timezone.utc).isoformat()
        for days, points in ((1, 10), (100, 8), (300, 6), (700, 3), (1000, 1)):
            self.assertEqual(rating.freshness(day(days), now), points, days)
        self.assertEqual(rating.freshness(None, now), 2)
        self.assertEqual(rating.community({}), 0)
        self.assertLessEqual(rating.community({"downloads": 10 ** 9, "likes": 10 ** 6,
                                               "trendingScore": 10 ** 6}), 20)

    def test_suggest_name(self):
        self.assertEqual(rating.suggest_name("Qwen/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf"),
                         "qwen3.5-9b:q4_k_m")
        self.assertEqual(rating.suggest_name("a/Weird Name!", "w.gguf"), "weird-name")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_rating`
Expected: `ImportError: cannot import name 'rating'`

- [ ] **Step 3: Implement**

`agentchat/rating.py`:

```python
"""Model Hub ratings for GGUF chat models, ported from local-ai-chat's
extensions/model-hub/recommendations.mjs (chat models only)."""
import math
import re
import time
from datetime import datetime
from urllib.parse import quote

GIB = 1024 ** 3
MARGIN = 1.2   # a GGUF file's size × MARGIN ≈ the video memory it needs
LIMIT = 0.9    # a preferred file needs at most this share of the GPU's memory
HF = "https://huggingface.co"
SUPPORT_FILE = re.compile(
    r"(?:^|[/_.-])(?:ae|autoencoder|mmproj|imatrix|tokenizer|text[_-]?encoder|clip|vae|lora|"
    r"controlnet|refiner|optimizer|encoder|decoder)(?:[/_.-]|$)", re.I)
UNSUPPORTED = re.compile(r"(?:mtp|\.part|\.split|-\d{5}-of-\d{5}\.gguf$)", re.I)
QUANTS = [(re.compile(p, re.I), q) for p, q in (
    (r"F(?:16|32)", 100), (r"Q8(?:_0)?", 92), (r"Q6_K", 86), (r"Q5_K_M", 82), (r"Q5_K_S", 79),
    (r"Q5(?:_\d)?", 76), (r"Q4_K_M", 73), (r"Q4_K_S", 70), (r"(?:IQ4|Q4)(?:_\w+)?", 68),
    (r"(?:IQ3|Q3)(?:_\w+)?", 57), (r"(?:IQ2|Q2|PQ2|PTQ1)(?:_\w+)?", 46))]
QUANT_TAG = re.compile(r"IQ\d_[A-Z0-9]+(?:_[A-Z0-9]+)*|Q\d_K_[SML]|Q\d_K|Q\d_\d|BF16|F16|F32", re.I)


def file_size(f):
    return int(f.get("size") or (f.get("lfs") or {}).get("size") or 0)


def quant_quality(name):
    return next((q for pattern, q in QUANTS if pattern.search(name)), 60)


def usable(f):
    """A single, plain GGUF chat-model file (the import refuses paths)."""
    name = f.get("rfilename") or ""
    return (name.lower().endswith(".gguf") and "/" not in name
            and not SUPPORT_FILE.search(name) and not UNSUPPORTED.search(name))


def best_file(model, gpu_bytes=0):
    files = [f for f in model.get("siblings") or [] if usable(f)]
    if not files:
        return None
    fitting = [f for f in files if not gpu_bytes or not file_size(f)
               or file_size(f) * MARGIN <= gpu_bytes * LIMIT]
    return sorted(fitting or files, key=lambda f: (-quant_quality(f["rfilename"]), -file_size(f)))[0]


def fit(size, gpu_bytes):
    """(points, verdict) for a file of `size` bytes on a GPU of `gpu_bytes`."""
    if not size or not gpu_bytes:
        return 10, "Unknown fit"
    ratio = size * MARGIN / gpu_bytes
    for limit, points, verdict in ((0.55, 45, "Plenty of room"), (0.75, 40, "Comfortable"),
                                   (0.9, 33, "Good fit"), (1.0, 20, "Tight fit")):
        if ratio <= limit + 1e-9:
            return points, verdict
    return 0, "Won't fit GPU memory"


def community(model):
    downloads = math.log10(max(1, model.get("downloads") or 0)) / 6 * 11
    likes = math.log10(max(1, model.get("likes") or 0)) / 4.5 * 5
    trending = math.log10(max(1, model.get("trendingScore") or 0)) / 4 * 4
    return min(20, max(0, downloads + likes + trending))


def freshness(last_modified, now=None):
    try:
        ts = datetime.fromisoformat(last_modified.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return 2
    days = max(0, ((now or time.time()) - ts) / 86400)
    for limit, points in ((30, 10), (180, 8), (365, 6), (730, 3)):
        if days <= limit:
            return points
    return 1


def suggest_name(repo_id, filename):
    base = re.sub(r"[-_.]?gguf$", "", repo_id.split("/")[-1].lower())
    base = re.sub(r"[^a-z0-9._-]+", "-", base).strip("-.") or "model"
    tag = QUANT_TAG.search(filename)
    return base + (":" + tag.group(0).lower() if tag else "")


def rate(model, gpu_bytes, now=None):
    f = best_file(model, gpu_bytes)
    size = file_size(f) if f else 0
    fit_points, verdict = fit(size, gpu_bytes)
    score = fit_points + (25 if f else 0) + community(model) + freshness(model.get("lastModified"), now)
    score = int(min(100, max(0, score)) + 0.5)
    label = "Excellent" if score >= 85 else "Good" if score >= 70 else "Fair" if score >= 50 else "Poor"
    repo = model.get("id") or ""
    return {"id": repo, "score": score, "label": label, "size": size, "verdict": verdict,
            "downloads": model.get("downloads") or 0, "likes": model.get("likes") or 0,
            "file": f["rfilename"] if f else None,
            "name": suggest_name(repo, f["rfilename"]) if f else None,
            "url": "%s/%s/resolve/main/%s" % (HF, repo, quote(f["rfilename"])) if f else None}
```

- [ ] **Step 4: Run them to verify they pass**

Run: `python3 -m unittest tests.test_rating -v`
Expected: all `ok`.

- [ ] **Step 5: Commit**

```bash
git add agentchat/rating.py tests/test_rating.py
git commit -m "Model Hub ratings ported from local-ai-chat"
```

---

### Task 4: Ollama client, GPU, context and thinking

**Files:**
- Create: `agentchat/ollama.py`, `agentchat/models.py` (first part), `tests/test_models.py`

**Interfaces:**
- Produces:
  - `ollama.OllamaError`; `ollama.Ollama(url)` with `.url`, `request(method, path, body=None, timeout=30, raw=None, headers=None)`, `json(method, path, body=None, timeout=30) -> dict`, `stream(path, body, timeout=3600)` (yields NDJSON dicts; raises on an `error` line), `version()`, `tags()`, `loaded() -> [name]`, `show(model)`, `delete(model)`, `unload_all() -> [name]`, `has_blob(digest) -> bool`, `push_blob(digest, path, size)`, `chat(model, messages, num_ctx=None, think=False, timeout=600) -> dict`.
  - `models.gpu() -> {"name","total","used"}|None`, `models.recommend_ctx(gpu_total, model_limit=None) -> int`, `models.model_facts(show) -> {"limit","can_think","levels"}`, `models.Tuning(root)` with `get(model, ollama, gpu_total) -> entry`, `set(model, ollama, gpu_total, **fields) -> entry`, `forget(model)`, `request(model, ollama, gpu_total, use) -> (num_ctx, think)`; `models.installed(ollama, tuning, gpu_info) -> [row]`. Entry: `{"num_ctx","override","think","can_think","levels"}`.

- [ ] **Step 1: Write the failing tests**

`tests/test_models.py`:

```python
import os
import tempfile
import unittest
from pathlib import Path

from agentchat import models
from agentchat.ollama import Ollama, OllamaError
from agentchat.store import StoreError
from tests.fake_ollama import GIB, FakeOllama

NVIDIA = """#!/bin/sh
echo "NVIDIA GeForce RTX 5070 Ti, 16303, 1200"
"""


class OllamaSetup(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.ollama = Ollama(self.fake.url)
        self.root = Path(tempfile.mkdtemp())
        self.tuning = models.Tuning(self.root)
        self.fake.add("qwen3.5:9b", size=6 * GIB, capabilities=["completion", "thinking"],
                      arch="qwen35", ctx=262144)
        self.fake.add("coder:14b", size=9 * GIB, capabilities=["completion"], arch="qwen2", ctx=32768)
        self.fake.add("gpt-oss:20b", size=13 * GIB, capabilities=["completion", "thinking"],
                      arch="gptoss", ctx=131072)


class ClientTest(OllamaSetup):
    def test_basic_calls(self):
        self.assertEqual(self.ollama.version(), "0.34.2")
        self.assertEqual(len(self.ollama.tags()), 3)
        self.ollama.chat("coder:14b", [{"role": "user", "content": "hi"}])
        self.assertEqual(self.ollama.loaded(), ["coder:14b"])
        self.assertEqual(self.ollama.unload_all(), ["coder:14b"])
        self.assertEqual(self.ollama.loaded(), [])
        self.ollama.delete("coder:14b")
        self.assertNotIn("coder:14b", self.fake.models)

    def test_errors(self):
        with self.assertRaises(OllamaError) as e:
            self.ollama.show("nope")
        self.assertIn("not found", str(e.exception))
        with self.assertRaises(OllamaError) as e:
            Ollama("http://127.0.0.1:1").version()
        self.assertIn("not reachable", str(e.exception))
        with self.assertRaises(OllamaError):
            list(self.ollama.stream("/api/pull", {"model": "bad"}))


class GpuTest(unittest.TestCase):
    def test_nvidia_smi(self):
        d = Path(tempfile.mkdtemp())
        (d / "nvidia-smi").write_text(NVIDIA)
        (d / "nvidia-smi").chmod(0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = "%s:%s" % (d, old)
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        g = models.gpu()
        self.assertEqual(g, {"name": "NVIDIA GeForce RTX 5070 Ti", "total": 16303 * 1024 ** 2,
                             "used": 1200 * 1024 ** 2})
        os.environ["PATH"] = "/nonexistent"
        self.assertIsNone(models.gpu())


class ContextTest(unittest.TestCase):
    def test_recommend_ctx(self):
        self.assertEqual(models.recommend_ctx(16 * GIB), 32768)
        self.assertEqual(models.recommend_ctx(12 * GIB), 32768)
        self.assertEqual(models.recommend_ctx(12 * GIB - 1), 16384)
        self.assertEqual(models.recommend_ctx(8 * GIB), 16384)
        self.assertEqual(models.recommend_ctx(8 * GIB - 1), 8192)
        self.assertEqual(models.recommend_ctx(0), 8192)
        self.assertEqual(models.recommend_ctx(16 * GIB, 4000), 3968)
        self.assertEqual(models.recommend_ctx(16 * GIB, 100), 512)


class TuningTest(OllamaSetup):
    def test_facts_and_defaults(self):
        e = self.tuning.get("qwen3.5:9b", self.ollama, 16 * GIB)
        self.assertEqual(e, {"num_ctx": 32768, "override": None, "think": None,
                             "can_think": True, "levels": None})
        self.assertEqual(self.tuning.get("gpt-oss:20b", self.ollama, 16 * GIB)["levels"],
                         ["low", "medium", "high"])
        self.assertFalse(self.tuning.get("coder:14b", self.ollama, 16 * GIB)["can_think"])
        self.assertTrue((self.root / "models.json").exists())

    def test_request_values_per_use(self):
        req = lambda m, use: self.tuning.request(m, self.ollama, 16 * GIB, use)
        self.assertEqual(req("qwen3.5:9b", "talk"), (32768, False))
        self.assertEqual(req("qwen3.5:9b", "agent"), (32768, True))
        self.assertEqual(req("gpt-oss:20b", "agent"), (32768, "medium"))
        self.assertEqual(req("coder:14b", "agent"), (32768, False))
        self.tuning.set("qwen3.5:9b", self.ollama, 16 * GIB, num_ctx=8192, think="on")
        self.assertEqual(req("qwen3.5:9b", "talk"), (8192, True))
        self.tuning.set("gpt-oss:20b", self.ollama, 16 * GIB, think="high")
        self.assertEqual(req("gpt-oss:20b", "talk"), (32768, "high"))
        self.tuning.set("qwen3.5:9b", self.ollama, 16 * GIB, num_ctx=None, think=None)
        self.assertEqual(req("qwen3.5:9b", "talk"), (32768, False))

    def test_refuses_bad_settings(self):
        for model, fields in (("qwen3.5:9b", {"num_ctx": 100}), ("qwen3.5:9b", {"num_ctx": "big"}),
                              ("qwen3.5:9b", {"think": "high"}), ("coder:14b", {"think": "on"}),
                              ("gpt-oss:20b", {"think": "on"})):
            with self.assertRaises(StoreError) as e:
                self.tuning.set(model, self.ollama, 16 * GIB, **fields)
            self.assertEqual(e.exception.code, 400, (model, fields))

    def test_installed_list(self):
        self.ollama.chat("coder:14b", [{"role": "user", "content": "hi"}])
        rows = {r["name"]: r for r in models.installed(self.ollama, self.tuning,
                                                       {"total": 16 * GIB, "used": 0, "name": "x"})}
        self.assertTrue(rows["coder:14b"]["loaded"])
        self.assertEqual(rows["qwen3.5:9b"]["verdict"], "Plenty of room")  # 6 GiB × 1.2 / 16 GiB
        self.assertEqual(rows["gpt-oss:20b"]["levels"], ["low", "medium", "high"])

    def test_installed_survives_a_failing_show(self):
        real_show = self.ollama.show
        self.ollama.show = lambda m: (_ for _ in ()).throw(OllamaError("gone")) if m == "coder:14b" \
            else real_show(m)
        rows = {r["name"]: r for r in models.installed(self.ollama, self.tuning, None)}
        self.assertIsNone(rows["coder:14b"]["num_ctx"])
        self.assertEqual(rows["coder:14b"]["verdict"], "Unknown fit")
        self.assertEqual(rows["qwen3.5:9b"]["num_ctx"], 8192)  # no GPU known


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_models`
Expected: `ImportError: cannot import name 'models'` (or `No module named 'agentchat.ollama'`).

- [ ] **Step 3: The Ollama client**

`agentchat/ollama.py`:

```python
"""A small client for Ollama's native API (urllib, no dependencies)."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(Exception):
    pass


class Ollama:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def request(self, method, path, body=None, timeout=30, raw=None, headers=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        h = dict(headers or {})
        if body is not None:
            h["Content-Type"] = "application/json"
        try:
            return urlopen(Request(self.url + path, data, h, method=method), timeout=timeout)
        except HTTPError as e:
            text = e.read().decode(errors="replace")
            e.close()
            try:
                text = json.loads(text).get("error", text)
            except (ValueError, AttributeError):
                pass
            raise OllamaError(text or str(e)) from None
        except (URLError, OSError) as e:
            raise OllamaError("Ollama at %s is not reachable (%s)"
                              % (self.url, getattr(e, "reason", e))) from None

    def json(self, method, path, body=None, timeout=30):
        with self.request(method, path, body, timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    def stream(self, path, body, timeout=3600):
        """The NDJSON objects of a streaming call such as /api/pull."""
        with self.request("POST", path, body, timeout) as r:
            for line in r:
                if line.strip():
                    obj = json.loads(line)
                    if "error" in obj:
                        raise OllamaError(obj["error"])
                    yield obj

    def version(self):
        return self.json("GET", "/api/version").get("version")

    def tags(self):
        return self.json("GET", "/api/tags").get("models", [])

    def loaded(self):
        return [m["name"] for m in self.json("GET", "/api/ps").get("models", [])]

    def show(self, model):
        return self.json("POST", "/api/show", {"model": model})

    def delete(self, model):
        self.json("DELETE", "/api/delete", {"model": model})

    def unload_all(self):
        names = self.loaded()
        for name in names:
            self.json("POST", "/api/generate", {"model": name, "keep_alive": 0}, timeout=120)
        return names

    def has_blob(self, digest):
        try:
            with self.request("HEAD", "/api/blobs/" + digest):
                return True
        except OllamaError:
            return False

    def push_blob(self, digest, path, size):
        with open(path, "rb") as f:
            with self.request("POST", "/api/blobs/" + digest, raw=f, timeout=3600,
                              headers={"Content-Type": "application/octet-stream",
                                       "Content-Length": str(size)}):
                pass

    def chat(self, model, messages, num_ctx=None, think=False, timeout=600):
        body = {"model": model, "messages": messages, "stream": False, "think": think}
        if num_ctx:
            body["options"] = {"num_ctx": num_ctx}
        return self.json("POST", "/api/chat", body, timeout)
```

- [ ] **Step 4: models.py, first part**

`agentchat/models.py`:

```python
"""Local models: the GPU, per-model context and thinking, Hugging Face
search, and the import/pull/benchmark jobs behind the Models panel."""
import hashlib
import json
import re
import shutil
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.parse import urlencode, urlparse
from urllib.request import Request, urlopen

from . import rating
from .ollama import Ollama, OllamaError
from .store import StoreError, data_dir, write_json

CTX_MIN, CTX_MAX = 512, 131072
LEVEL_ARCHS = ("gptoss",)   # models that take a thinking level instead of on/off
LEVELS = ["low", "medium", "high"]
THINK_DEFAULT = {"talk": "off", "agent": "on"}


def gpu():
    """{"name", "total", "used"} in bytes over all NVIDIA GPUs, or None."""
    try:
        r = subprocess.run(["nvidia-smi", "--query-gpu=name,memory.total,memory.used",
                            "--format=csv,noheader,nounits"],
                           capture_output=True, text=True, timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return None
    rows = [line.split(",") for line in r.stdout.strip().splitlines() if line.count(",") == 2]
    if r.returncode or not rows:
        return None
    mib = 1024 * 1024
    return {"name": " + ".join(x[0].strip() for x in rows),
            "total": sum(int(x[1]) for x in rows) * mib,
            "used": sum(int(x[2]) for x in rows) * mib}


def recommend_ctx(gpu_total, model_limit=None):
    """local-ai-chat's rule: 32K from 12 GiB, 16K from 8 GiB, else 8K;
    never above the model's own limit."""
    ctx = 32768 if gpu_total >= 12 * rating.GIB else 16384 if gpu_total >= 8 * rating.GIB else 8192
    if model_limit and model_limit > 0:
        ctx = min(ctx, model_limit)
    return max(CTX_MIN, ctx // 64 * 64)


def model_facts(show):
    info = show.get("model_info") or {}
    arch = info.get("general.architecture") or ""
    can = "thinking" in (show.get("capabilities") or [])
    return {"limit": info.get("%s.context_length" % arch), "can_think": can,
            "levels": LEVELS if can and arch.startswith(LEVEL_ARCHS) else None}


class Tuning:
    """Per-model context and thinking settings in <data>/models.json."""

    def __init__(self, root):
        self.path = Path(root) / "models.json"
        self.lock = threading.RLock()

    def all(self):
        try:
            return json.loads(self.path.read_text())
        except (OSError, ValueError):
            return {}

    def _save(self, model, entry):
        with self.lock:
            data = self.all()
            data[model] = entry
            self.path.parent.mkdir(parents=True, exist_ok=True)
            write_json(self.path, data)

    def get(self, model, ollama, gpu_total):
        """The model's entry, filled in from Ollama the first time."""
        with self.lock:
            entry = self.all().get(model)
            if entry is None:
                facts = model_facts(ollama.show(model))
                entry = {"num_ctx": recommend_ctx(gpu_total, facts["limit"]), "override": None,
                         "think": None, "can_think": facts["can_think"], "levels": facts["levels"]}
                self._save(model, entry)
            return entry

    def set(self, model, ollama, gpu_total, **fields):
        entry = dict(self.get(model, ollama, gpu_total))
        if "num_ctx" in fields:
            v = fields["num_ctx"]
            if v is not None and not (isinstance(v, int) and not isinstance(v, bool)
                                      and CTX_MIN <= v <= CTX_MAX):
                raise StoreError(400, "context must be %d-%d tokens" % (CTX_MIN, CTX_MAX))
            entry["override"] = v
        if "think" in fields:
            v = fields["think"]
            allowed = ["off"] + ((entry["levels"] or ["on"]) if entry["can_think"] else [])
            if v is not None and v not in allowed:
                raise StoreError(400, "thinking for %s can be: %s" % (model, ", ".join(allowed)))
            entry["think"] = v
        self._save(model, entry)
        return entry

    def forget(self, model):
        with self.lock:
            data = self.all()
            if data.pop(model, None) is not None:
                write_json(self.path, data)

    def request(self, model, ollama, gpu_total, use):
        """(num_ctx, think) to send with a request; use is "talk" or "agent"."""
        e = self.get(model, ollama, gpu_total)
        setting = e["think"] or THINK_DEFAULT[use]
        if not e["can_think"] or setting == "off":
            think = False
        elif setting == "on":
            think = "medium" if e["levels"] else True
        else:
            think = setting
        return e["override"] or e["num_ctx"], think


def installed(ollama, tuning, gpu_info):
    """The Installed tab: every model Ollama has, with its settings."""
    total = (gpu_info or {}).get("total", 0)
    loaded = set(ollama.loaded())
    rows = []
    for m in ollama.tags():
        name, size = m["name"], m.get("size", 0)
        try:
            e = tuning.get(name, ollama, total)
        except OllamaError:  # e.g. deleted meanwhile
            e = {"num_ctx": None, "override": None, "think": None, "can_think": False, "levels": None}
        rows.append({"name": name, "size": size, "loaded": name in loaded,
                     "verdict": rating.fit(size, total)[1], "num_ctx": e["num_ctx"],
                     "override": e["override"], "think": e["think"],
                     "can_think": e["can_think"], "levels": e["levels"]})
    return rows
```

(`hashlib`, `shutil`, `time`, `uuid`, `ThreadPoolExecutor`, `urlencode`, `urlparse`, `Request`, `urlopen`, `data_dir`, `Ollama` are used by Task 5; keep the imports.)

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest tests.test_models -v 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 6: Commit**

```bash
git add agentchat/ollama.py agentchat/models.py tests/test_models.py
git commit -m "Ollama client, GPU reading, context and thinking per model"
```

---

### Task 5: Hub search, jobs, import, pull, benchmark

**Files:**
- Modify: `agentchat/models.py` (append), `tests/test_models.py` (append)

**Interfaces:**
- Consumes: Tasks 3 and 4.
- Produces: `models.Hub(fetch=None)` with `search(q, gpu_total) -> [rated]`; `models.Cancelled`; `models.Job` (`progress(completed=None, total=None, message=None)`, `view()`); `models.Jobs()` with `start(kind, model, work) -> view` (409 if the same kind and model runs), `list()`, `cancel(job_id)`; `models.check_model_name(model)`, `models.check_import(url, filename, model)`; `models.import_gguf(job, ollama, url, filename, model, work_dir, free=...) -> {"model"}`; `models.pull(job, ollama, model)`; `models.benchmark(job, ollama, model, tuning, gpu_fn=gpu) -> result`; `models.Models(root=None, ollama_url=None, hub=None)` with `.ollama .tuning .hub .jobs .root`, `status()`, `gpu_total()`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_models.py`, before `if __name__`:

```python
import threading  # noqa: E402
import time  # noqa: E402

from tests.fake_ollama import FileHost  # noqa: E402


def wait_done(jobs, job_id, seconds=10):
    for _ in range(seconds * 20):
        j = next(j for j in jobs.list() if j["id"] == job_id)
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job still running")


class HubTest(unittest.TestCase):
    def test_search_rates_and_caches(self):
        calls = []
        listing = [{"id": "a/good-GGUF", "downloads": 10 ** 5, "likes": 100, "trendingScore": 10,
                    "lastModified": "2026-09-01T00:00:00Z", "siblings": []},
                   {"id": "b/bad", "downloads": 1, "siblings": []}]
        details = {"a/good-GGUF": [{"rfilename": "good-Q4_K_M.gguf", "size": 5 * GIB}],
                   "b/bad": [{"rfilename": "README.md", "size": 1}]}

        def fetch(url):
            calls.append(url)
            if "/api/models?" in url:
                return listing
            return {"siblings": details[url.split("/api/models/")[1].split("?")[0]]}
        hub = models.Hub(fetch=fetch)
        rated = hub.search("good", 16 * GIB)
        self.assertEqual([r["id"] for r in rated], ["a/good-GGUF", "b/bad"])
        self.assertEqual(rated[0]["file"], "good-Q4_K_M.gguf")
        self.assertIn("filter=gguf", calls[0])
        self.assertIn("search=good", calls[0])
        n = len(calls)
        hub.search("good", 16 * GIB)
        self.assertEqual(len(calls), n)  # cached

    def test_search_error_keeps_old_results(self):
        state = {"fail": False}

        def fetch(url):
            if state["fail"]:
                raise OSError("rate limited")
            return [] if "/api/models?" in url else {"siblings": []}
        hub = models.Hub(fetch=fetch)
        hub.search("x", 0)
        hub.cache["x"] = (0, hub.cache["x"][1])  # expired
        state["fail"] = True
        self.assertEqual(hub.search("x", 0), [])
        with self.assertRaises(StoreError) as e:
            hub.search("never searched", 0)
        self.assertEqual(e.exception.code, 502)


class JobsTest(unittest.TestCase):
    def test_duplicate_refused_and_cancel_rules(self):
        jobs, gate = models.Jobs(), threading.Event()
        v = jobs.start("import", "m", lambda job: gate.wait(5))
        with self.assertRaises(StoreError) as e:
            jobs.start("import", "m", lambda job: None)
        self.assertEqual(e.exception.code, 409)
        b = jobs.start("benchmark", "m", lambda job: {"ok": 1})
        self.assertEqual(wait_done(jobs, b["id"])["result"], {"ok": 1})
        with self.assertRaises(StoreError) as e:
            jobs.cancel(b["id"])
        self.assertEqual(e.exception.code, 400)
        with self.assertRaises(StoreError) as e:
            jobs.cancel("nope")
        self.assertEqual(e.exception.code, 404)
        gate.set()
        self.assertEqual(wait_done(jobs, v["id"])["state"], "done")

    def test_failures_become_messages(self):
        jobs = models.Jobs()
        j = jobs.start("pull", "m", lambda job: (_ for _ in ()).throw(OllamaError("boom")))
        done = wait_done(jobs, j["id"])
        self.assertEqual((done["state"], done["message"]), ("failed", "boom"))


class ImportTest(OllamaSetup):
    def run_import(self, data, delay=0, free=lambda p: 10 ** 15, cancel_after=None):
        host = FileHost(data, delay)
        self.addCleanup(host.close)
        jobs = models.Jobs()
        work = self.root / "imports"
        v = jobs.start("import", "new:q4", lambda job: models.import_gguf(
            job, self.ollama, host.url_for("r/resolve/main/new-Q4_K_M.gguf"),
            "new-Q4_K_M.gguf", "new:q4", work, free=free))
        if cancel_after:
            time.sleep(cancel_after)
            jobs.cancel(v["id"])
        return wait_done(jobs, v["id"]), work

    def test_import_hashes_uploads_and_creates(self):
        import hashlib
        data = os.urandom(300 * 1024)
        done, work = self.run_import(data)
        self.assertEqual(done["state"], "done", done["message"])
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self.assertIn(digest, self.fake.blobs)
        create = [c for c in self.fake.calls if c[:2] == ("POST", "/api/create")][0][2]
        self.assertEqual(create["files"], {"new-Q4_K_M.gguf": digest})
        self.assertIn("new:q4", self.fake.models)
        self.assertEqual(list(work.iterdir()), [])  # temporary file gone

    def test_known_blob_is_not_uploaded_again(self):
        import hashlib
        data = os.urandom(70 * 1024)
        self.fake.blobs.add("sha256:" + hashlib.sha256(data).hexdigest())
        done, _ = self.run_import(data)
        self.assertEqual(done["state"], "done")
        self.assertFalse([c for c in self.fake.calls if c[0] == "POST" and "/api/blobs/" in c[1]])

    def test_not_enough_disk(self):
        done, work = self.run_import(os.urandom(1000), free=lambda p: 3000)
        self.assertEqual(done["state"], "failed")
        self.assertIn("not enough disk space", done["message"])
        self.assertEqual(list(work.iterdir()), [])

    def test_cancel_deletes_the_partial_file(self):
        done, work = self.run_import(os.urandom(64 * 1024 * 40), delay=0.05, cancel_after=0.3)
        self.assertEqual(done["state"], "cancelled")
        self.assertEqual(list(work.iterdir()), [])

    def test_check_import(self):
        ok = ("https://huggingface.co/a/b-GGUF/resolve/main/b-Q4_K_M.gguf", "b-Q4_K_M.gguf", "b:q4_k_m")
        models.check_import(*ok)
        bad = [("http://huggingface.co/a/b/resolve/main/x.gguf", "x.gguf", "x"),
               ("https://evil.example/a/b/resolve/main/x.gguf", "x.gguf", "x"),
               ("https://huggingface.co/a/b/blob/main/x.gguf", "x.gguf", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "../x.gguf", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.bin", "x.bin", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "y.gguf", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "x.gguf", "has space"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "x.gguf", ".hidden")]
        for case in bad:
            with self.assertRaises(StoreError, msg=case) as e:
                models.check_import(*case)
            self.assertEqual(e.exception.code, 400)


class PullBenchTest(OllamaSetup):
    def test_pull_progress_and_error(self):
        jobs = models.Jobs()
        ok = wait_done(jobs, jobs.start("pull", "tiny", lambda j: models.pull(j, self.ollama, "tiny"))["id"])
        self.assertEqual((ok["state"], ok["completed"], ok["total"]), ("done", 5, 10))
        bad = wait_done(jobs, jobs.start("pull", "bad", lambda j: models.pull(j, self.ollama, "bad"))["id"])
        self.assertEqual(bad["state"], "failed")

    def test_benchmark_with_and_without_thinking(self):
        jobs, g = models.Jobs(), (lambda: {"name": "x", "total": 16 * GIB, "used": GIB})
        run = lambda m: wait_done(jobs, jobs.start("benchmark", m, lambda j: models.benchmark(
            j, self.ollama, m, self.tuning, gpu_fn=g))["id"])
        plain = run("coder:14b")["result"]
        self.assertEqual(plain["tokens_per_second"], 20.0)  # 4 tokens in 0.2 s
        self.assertEqual(plain["reply"], "benchmark ok")
        self.assertNotIn("thinking", plain)
        think = run("qwen3.5:9b")["result"]["thinking"]
        self.assertEqual(think["off"]["thinking_tokens"], 0)
        self.assertEqual(think["on"]["tokens"], 40)
        self.assertEqual(think["on"]["thinking_tokens"], 40)  # 300 thinking chars, 3 answer chars → ~40
        level = [c[2] for c in self.fake.calls if c[:2] == ("POST", "/api/chat")]
        run("gpt-oss:20b")
        level = [c[2]["think"] for c in self.fake.calls if c[:2] == ("POST", "/api/chat")][len(level):]
        self.assertEqual(level, [False, False, "low"])

    def test_benchmark_failure_has_the_hint(self):
        self.fake.fail_chat = "model requires more system memory"
        jobs = models.Jobs()
        j = wait_done(jobs, jobs.start("benchmark", "coder:14b", lambda job: models.benchmark(
            job, self.ollama, "coder:14b", self.tuning, gpu_fn=lambda: None))["id"])
        self.assertEqual(j["state"], "failed")
        self.assertIn("more system memory", j["message"])
        self.assertIn("free video memory", j["message"])


class FacadeTest(unittest.TestCase):
    def test_status(self):
        fake = FakeOllama()
        self.addCleanup(fake.close)
        m = models.Models(root=tempfile.mkdtemp(), ollama_url=fake.url)
        s = m.status()
        self.assertEqual((s["reachable"], s["version"], s["own"]), (True, "0.34.2", False))
        down = models.Models(root=tempfile.mkdtemp(), ollama_url="http://127.0.0.1:1").status()
        self.assertFalse(down["reachable"])
        self.assertIn("not reachable", down["error"])
```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_models 2>&1 | tail -3`
Expected: errors — `AttributeError: module 'agentchat.models' has no attribute 'Hub'` (and `Jobs`, …).

- [ ] **Step 3: Append to models.py**

Append to `agentchat/models.py`:

```python
HF = rating.HF
SEARCH_TTL = 600
DISK_FACTOR = 3.1   # Ollama copies an imported file into its own layers
MODEL_NAME = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*(?::[A-Za-z0-9._-]+)?$")
GGUF_NAME = re.compile(r"^[^/\\\s][^/\\]*\.gguf$", re.I)
BENCH_PROMPT = "Reply with exactly: benchmark ok"
THINK_PROMPT = "What is 17 × 23? Answer with the number only."
LOAD_HINT = (" (if the model did not load: free video memory, another program may have a"
             " model loaded, or pick a smaller file)")


def fetch_json(url):
    with urlopen(Request(url, headers={"User-Agent": "agent-chat"}), timeout=20) as r:
        return json.loads(r.read())


class Hub:
    """Hugging Face GGUF search, rated for this GPU; cached for SEARCH_TTL."""

    def __init__(self, fetch=None):
        self.fetch, self.cache = fetch or fetch_json, {}

    def search(self, q, gpu_total):
        key = (q or "").strip().lower()
        hit = self.cache.get(key)
        if hit and time.time() - hit[0] < SEARCH_TTL:
            found = hit[1]
        else:
            params = [("filter", "gguf"), ("sort", "trendingScore"), ("direction", "-1"),
                      ("limit", "30")] + ([("search", q.strip())] if key else [])
            params += [("expand[]", f) for f in ("downloads", "likes", "tags", "trendingScore",
                                                  "lastModified", "siblings")]
            try:
                listing = self.fetch(HF + "/api/models?" + urlencode(params))
                with ThreadPoolExecutor(8) as pool:
                    found = list(pool.map(self._with_sizes, listing))
            except (OSError, ValueError) as e:
                if not hit:
                    raise StoreError(502, "Hugging Face search failed: %s" % e) from None
                found = hit[1]  # keep the last results
            else:
                self.cache[key] = (time.time(), found)
        return sorted((rating.rate(m, gpu_total) for m in found), key=lambda r: -r["score"])

    def _with_sizes(self, m):
        try:
            detail = self.fetch("%s/api/models/%s?blobs=true" % (HF, m["id"]))
        except (OSError, ValueError):
            return m
        return dict(m, siblings=detail.get("siblings") or m.get("siblings") or [])


class Cancelled(Exception):
    pass


class Job:
    def __init__(self, kind, model):
        self.id, self.kind, self.model = uuid.uuid4().hex[:12], kind, model
        self.state, self.completed, self.total, self.message, self.result = "running", 0, 0, "", None
        self.cancel = threading.Event()

    def progress(self, completed=None, total=None, message=None):
        """Report progress; raises Cancelled once the job was cancelled."""
        if self.cancel.is_set():
            raise Cancelled()
        if completed is not None:
            self.completed = completed
        if total is not None:
            self.total = total
        if message is not None:
            self.message = message

    def view(self):
        return {k: getattr(self, k) for k in
                ("id", "kind", "model", "state", "completed", "total", "message", "result")}


class Jobs:
    KEEP = 50

    def __init__(self):
        self.jobs, self.lock = [], threading.Lock()

    def start(self, kind, model, work):
        with self.lock:
            if any(j.kind == kind and j.model == model and j.state == "running" for j in self.jobs):
                raise StoreError(409, "a %s of %s is already running" % (kind, model))
            job = Job(kind, model)
            self.jobs.insert(0, job)
            del self.jobs[self.KEEP:]

        def run():
            try:
                job.result = work(job)
                job.state = "done"
            except Cancelled:
                job.state, job.message = "cancelled", "cancelled"
            except (OllamaError, StoreError, OSError, ValueError) as e:
                job.state, job.message = "failed", str(e)
        threading.Thread(target=run, daemon=True).start()
        return job.view()

    def list(self):
        return [j.view() for j in self.jobs]

    def cancel(self, job_id):
        for j in self.jobs:
            if j.id == job_id:
                if j.kind not in ("import", "pull"):
                    raise StoreError(400, "only imports and pulls can be cancelled")
                j.cancel.set()
                return j.view()
        raise StoreError(404, "no job %s" % job_id)


def check_model_name(model):
    if not isinstance(model, str) or not MODEL_NAME.match(model):
        raise StoreError(400, "model must be an Ollama model name without spaces, "
                              "not starting with '.'")


def check_import(url, filename, model):
    u = urlparse(url or "")
    if u.scheme != "https" or u.hostname != "huggingface.co" or "/resolve/" not in u.path:
        raise StoreError(400, "url must be an https://huggingface.co/<repo>/resolve/<rev>/<file> link")
    if not isinstance(filename, str) or not GGUF_NAME.match(filename) or filename.startswith("."):
        raise StoreError(400, "unsafe GGUF file name: %r" % (filename,))
    if u.path.rsplit("/", 1)[-1] not in (filename, filename.replace(" ", "%20")):
        raise StoreError(400, "the url must point at %s" % filename)
    check_model_name(model)


def import_gguf(job, ollama, url, filename, model, work_dir,
                free=lambda p: shutil.disk_usage(p).free):
    """Download a GGUF file (hashing it), hand it to Ollama, create the model."""
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    tmp = work_dir / (uuid.uuid4().hex + ".gguf.part")
    try:
        with urlopen(Request(url, headers={"User-Agent": "agent-chat"}), timeout=60) as r:
            total = int(r.headers.get("Content-Length") or 0)
            if total and free(work_dir) < total * DISK_FACTOR:
                raise StoreError(507, "not enough disk space: importing %s needs %.1f GB free"
                                 % (filename, total * DISK_FACTOR / rating.GIB))
            job.progress(0, total, "downloading")
            h, done = hashlib.sha256(), 0
            with open(tmp, "wb") as out:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    out.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    job.progress(done)
        digest = "sha256:" + h.hexdigest()
        job.progress(message="adding to Ollama")
        if not ollama.has_blob(digest):
            ollama.push_blob(digest, tmp, done)
        for event in ollama.stream("/api/create", {"model": model, "files": {filename: digest}}):
            job.progress(message=event.get("status", ""))
        return {"model": model}
    finally:
        tmp.unlink(missing_ok=True)


def pull(job, ollama, model):
    for event in ollama.stream("/api/pull", {"model": model}):
        job.progress(event.get("completed"), event.get("total"), event.get("status"))
    return {"model": model}


def _tokens_per_second(r):
    d = r.get("eval_duration") or 0
    return round(r.get("eval_count", 0) / (d / 1e9), 1) if d else None


def _thinking_tokens(r):
    """An estimate: Ollama counts thinking and answer tokens together."""
    msg = r.get("message") or {}
    thought, answer = len(msg.get("thinking") or ""), len(msg.get("content") or "")
    return round(r.get("eval_count", 0) * thought / (thought + answer)) if thought else 0


def benchmark(job, ollama, model, tuning, gpu_fn=gpu):
    info = gpu_fn() or {}
    entry = tuning.get(model, ollama, info.get("total", 0))
    try:
        job.progress(message="loading and generating")
        start = time.monotonic()
        r = ollama.chat(model, [{"role": "user", "content": BENCH_PROMPT}], think=False)
        seconds = time.monotonic() - start
        after = (gpu_fn() or {}).get("used")
        result = {"seconds": round(seconds, 2), "tokens_per_second": _tokens_per_second(r),
                  "vram_used": after - info["used"] if after is not None and "used" in info else None,
                  "reply": (r.get("message") or {}).get("content", "")}
        if entry["can_think"]:
            result["thinking"] = {}
            for label, think in (("off", False), ("on", "low" if entry["levels"] else True)):
                job.progress(message="thinking " + label)
                start = time.monotonic()
                r = ollama.chat(model, [{"role": "user", "content": THINK_PROMPT}], think=think)
                result["thinking"][label] = {
                    "seconds": round(time.monotonic() - start, 2), "tokens": r.get("eval_count", 0),
                    "thinking_tokens": _thinking_tokens(r),
                    "reply": (r.get("message") or {}).get("content", "")}
        return result
    except OllamaError as e:
        raise OllamaError(str(e) + LOAD_HINT) from None


class Models:
    """Everything the server's model routes need."""

    def __init__(self, root=None, ollama_url=None, hub=None):
        from . import config
        self.own = config.OWN_OLLAMA
        self.ollama = Ollama(ollama_url or config.ollama_url())
        self.root = Path(root) if root else data_dir()
        self.tuning, self.hub, self.jobs = Tuning(self.root), hub or Hub(), Jobs()

    def gpu_total(self):
        return (gpu() or {}).get("total", 0)

    def status(self):
        try:
            version, reachable, error = self.ollama.version(), True, None
        except OllamaError as e:
            version, reachable, error = None, False, str(e)
        return {"url": self.ollama.url, "own": self.ollama.url == self.own,
                "reachable": reachable, "version": version, "error": error, "gpu": gpu()}
```

- [ ] **Step 4: Run the tests**

Run: `python3 -m unittest tests.test_models -v 2>&1 | tail -3`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add agentchat/models.py tests/test_models.py
git commit -m "Hugging Face search, jobs, GGUF import, pull and benchmark"
```

---

### Task 6: Routes and the Models panel

**Files:**
- Modify: `agentchat/server.py`, `agentchat/page.html`, `tests/helpers.py`, `tests/test_server.py`
- Create: `agentchat/models.js`

**Interfaces:**
- Consumes: `models.Models` and friends (Task 5).
- Produces: the spec's model routes; `GET /models.js`; `serve(..., models=None)`; `tests.helpers.start(wait_seconds=1, models=None)`.

- [ ] **Step 1: Write the failing route tests**

In `tests/helpers.py`, change `def start(wait_seconds=1):` to `def start(wait_seconds=1, models=None):` and the `serve(...)` call to `serve(port=0, store=store, wait_seconds=wait_seconds, deliver=False, models=models)`.

Add to `tests/test_server.py` (imports: `from agentchat import models as models_mod` and `from tests.fake_ollama import GIB, FakeOllama`), a new class before `if __name__`:

```python
class ModelRoutesTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.fake.add("qwen3.5:9b", size=6 * GIB, capabilities=["completion", "thinking"],
                      arch="qwen35", ctx=262144)
        hub = models_mod.Hub(fetch=lambda url: [] if "/api/models?" in url else {"siblings": []})
        m = models_mod.Models(root=tempfile.mkdtemp(), ollama_url=self.fake.url, hub=hub)
        _, self.server, self.port, _ = start(models=m)
        self.addCleanup(stop, self.server)
        self.c = Client(self.port)

    def test_routes(self):
        c = self.c
        self.assertTrue(c.call("GET", "/api/models/status")[1]["reachable"])
        rows = c.call("GET", "/api/models")[1]["models"]
        self.assertEqual([r["name"] for r in rows], ["qwen3.5:9b"])
        tuned = c.call("POST", "/api/models/tune", {"model": "qwen3.5:9b", "num_ctx": 8192,
                                                     "think": "on"})[1]["model"]
        self.assertEqual((tuned["override"], tuned["think"]), (8192, "on"))
        self.assertEqual(c.call("GET", "/api/models/search?q=x")[1]["results"], [])
        job = c.call("POST", "/api/models/pull", {"model": "tiny"})[1]["job"]
        for _ in range(100):
            jobs = c.call("GET", "/api/models/jobs")[1]["jobs"]
            if jobs[0]["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual((jobs[0]["id"], jobs[0]["state"]), (job["id"], "done"))
        self.assertEqual(c.call("POST", "/api/models/unload", {})[1], {"unloaded": []})
        c.call("POST", "/api/models/delete", {"model": "tiny"})
        self.assertNotIn("tiny", self.fake.models)
        for path, body, code in (("/api/models/tune", {"model": "qwen3.5:9b", "num_ctx": 5}, 400),
                                 ("/api/models/import", {"url": "http://x/y.gguf",
                                                         "filename": "y.gguf", "model": "y"}, 400),
                                 ("/api/models/pull", {"model": "has space"}, 400),
                                 ("/api/models/jobs/nope/cancel", {}, 404),
                                 ("/api/models/delete", {"model": "ghost"}, 502)):
            with self.assertRaises(ApiError, msg=path) as e:
                c.call("POST", path, body)
            self.assertEqual(e.exception.code, code, path)

    def test_models_js_is_served(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request("GET", "/models.js")
        r = conn.getresponse()
        self.assertEqual(r.status, 200)
        self.assertIn(b"openModels", r.read())
        conn.close()
```

Also add `import tempfile` to the imports of `tests/test_server.py` if missing.

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_server.ModelRoutesTest 2>&1 | tail -3`
Expected: `TypeError: serve() got an unexpected keyword argument 'models'`.

- [ ] **Step 3: The routes**

In `agentchat/server.py`:

1. Imports: add `from . import models as models_mod` and `from .ollama import OllamaError`.
2. After `PAGE = Path(__file__).with_name("page.html")` add `MODELS_JS = Path(__file__).with_name("models.js")`.
3. `def make_handler(store, port, wait_seconds, spawner, owner):` → `def make_handler(store, port, wait_seconds, spawner, owner, models):`.
4. In `dispatch`, replace

```python
            try:
                self.send(*self.route(method, parts, query))
            except StoreError as e:
                self.send(e.code, {"error": str(e)})
```

with

```python
            try:
                self.send(*self.route(method, parts, query))
            except StoreError as e:
                self.send(e.code, {"error": str(e)})
            except OllamaError as e:
                self.send(502, {"error": str(e)})
```

5. In `route`, replace

```python
            if method == "GET" and not parts:
                return 200, PAGE.read_bytes(), "text/html; charset=utf-8"
```

with

```python
            if method == "GET" and not parts:
                return 200, PAGE.read_bytes(), "text/html; charset=utf-8"
            if method == "GET" and parts == ["models.js"]:
                return 200, MODELS_JS.read_bytes(), "text/javascript; charset=utf-8"
```

6. After the `if rest == ["tools"] and method == "GET":` block add:

```python
            if rest[:1] == ["models"]:
                return self.model_route(method, rest[1:], query)
```

7. Add this method to `Handler`, after `route`:

```python
        def model_route(self, method, what, query):
            m = models
            if method == "GET" and what == ["status"]:
                return 200, m.status()
            if method == "GET" and what == []:
                return 200, {"models": models_mod.installed(m.ollama, m.tuning, models_mod.gpu())}
            if method == "GET" and what == ["search"]:
                return 200, {"results": m.hub.search(query.get("q", ""), m.gpu_total())}
            if method == "GET" and what == ["jobs"]:
                return 200, {"jobs": m.jobs.list()}
            if method != "POST":
                raise StoreError(404, "not found")
            data = self.body()
            if len(what) == 3 and what[0] == "jobs" and what[2] == "cancel":
                return 200, {"job": m.jobs.cancel(what[1])}
            if what == ["unload"]:
                return 200, {"unloaded": m.ollama.unload_all()}
            name = data.get("model")
            if what == ["import"]:
                url, filename = data.get("url"), data.get("filename")
                models_mod.check_import(url, filename, name)
                work = m.root / "imports"
                return 202, {"job": m.jobs.start("import", name, lambda job: models_mod.import_gguf(
                    job, m.ollama, url, filename, name, work))}
            models_mod.check_model_name(name)
            if what == ["pull"]:
                return 202, {"job": m.jobs.start("pull", name,
                                                 lambda job: models_mod.pull(job, m.ollama, name))}
            if what == ["benchmark"]:
                return 202, {"job": m.jobs.start("benchmark", name, lambda job: models_mod.benchmark(
                    job, m.ollama, name, m.tuning))}
            if what == ["delete"]:
                m.ollama.delete(name)
                m.tuning.forget(name)
                return 200, {"deleted": name}
            if what == ["tune"]:
                fields = {k: data[k] for k in ("num_ctx", "think") if k in data}
                return 200, {"model": m.tuning.set(name, m.ollama, m.gpu_total(), **fields)}
            raise StoreError(404, "not found")
```

8. `def serve(port=None, store=None, wait_seconds=WAIT_SECONDS, deliver=True, owner=None):` → add `models=None` at the end, and replace

```python
    server.RequestHandlerClass = make_handler(store, server.server_address[1],
                                              wait_seconds, spawner,
                                              os.getuid() if owner is None else owner)
```

with

```python
    server.RequestHandlerClass = make_handler(store, server.server_address[1],
                                              wait_seconds, spawner,
                                              os.getuid() if owner is None else owner,
                                              models or models_mod.Models(store.root))
```

- [ ] **Step 4: The panel**

`agentchat/models.js`:

```js
// Models panel: installed models, Hugging Face search, jobs.
// Uses the page's $, el, api and KIND; loaded after the page's own script.
const mp=$('modelspanel'),GB=b=>b?(b/1024**3).toFixed(1)+' GB':'?';
let mtab='installed',mtimer=null,mdrawn='';
function openModels(){mp.hidden=false;showTab(mtab);clearInterval(mtimer);mtimer=setInterval(mrefresh,2000);}
function closeModels(){mp.hidden=true;clearInterval(mtimer);t.focus();}
function showTab(tab){mtab=tab;mdrawn='';
  for(const b of mp.querySelectorAll('.mtab'))b.classList.toggle('on',b.dataset.tab===tab);
  for(const s of mp.querySelectorAll('.mview'))s.hidden=s.dataset.tab!==tab;mrefresh();}
function merr(e){$('merr').textContent=e?e.message||String(e):'';}
async function mrefresh(){
  try{
    const s=await api('api/models/status');
    $('mstatus').textContent=(s.own?"agent-chat's Ollama":'External Ollama')+' at '+s.url+' · '+
      (s.reachable?'v'+s.version:'not reachable: '+(s.own?'systemctl --user start agent-chat-ollama':
        'check the address (./install.sh --ollama-url)'))+' · '+
      (s.gpu?`${s.gpu.name}, ${GB(s.gpu.used)} of ${GB(s.gpu.total)} used`:'GPU unknown');
    if(mtab==='installed'&&s.reachable)drawInstalled((await api('api/models')).models);
    if(mtab==='jobs')drawJobs((await api('api/models/jobs')).jobs);
  }catch(e){merr(e);}}
function button(text,fn){const b=el('button','ghost',text);b.type='button';
  b.onclick=async()=>{try{merr();await fn();}catch(e){merr(e);}mrefresh();};return b;}
function drawInstalled(rows){
  const key=JSON.stringify(rows);if(key===mdrawn||$('mlist').contains(document.activeElement))return;mdrawn=key;
  $('mlist').replaceChildren(...rows.map(r=>{
    const d=el('div','mrow'),ctx=el('input');ctx.type='number';ctx.min=512;ctx.max=131072;ctx.step=64;
    ctx.value=r.override||'';ctx.placeholder=r.num_ctx?String(r.num_ctx):'?';ctx.title='Context tokens (empty: recommended)';
    ctx.onchange=async()=>{try{merr();await api('api/models/tune',{model:r.name,num_ctx:ctx.value?+ctx.value:null});}catch(e){merr(e);}mdrawn='';};
    d.append(el('b','',r.name),el('span','',GB(r.size)),el('span',r.loaded?'badge':'',r.loaded?'Loaded':''),
      el('span','',r.verdict),ctx);
    if(r.can_think){const sel=el('select');sel.title='Thinking';
      for(const [v,label] of [['','Default (off to talk, on for agents)'],['off','Off'],
        ...(r.levels||['on']).map(v=>[v,v[0].toUpperCase()+v.slice(1)])])sel.append(new Option(label,v));
      sel.value=r.think||'';
      sel.onchange=async()=>{try{merr();await api('api/models/tune',{model:r.name,think:sel.value||null});}catch(e){merr(e);}mdrawn='';};
      d.append(sel);}
    d.append(button('Benchmark',async()=>{await api('api/models/benchmark',{model:r.name});showTab('jobs');}),
      button('Delete',async()=>{if(confirm(`Delete ${r.name}? Its files are removed.`))await api('api/models/delete',{model:r.name});}));
    return d;}));}
async function search(){
  const q=$('mq').value.trim();$('mresults').replaceChildren(el('p','','Searching Hugging Face…'));
  try{merr();const {results}=await api('api/models/search?q='+encodeURIComponent(q));
    $('mresults').replaceChildren(...(results.length?results:[null]).map(r=>{
      if(!r)return el('p','','Nothing found.');
      const d=el('div','mrow'),name=el('input');name.value=r.name||'';name.title='Name in Ollama';
      d.append(el('b','',`${r.label} ${r.score}`),el('span','',r.id),el('span','',r.verdict),
        el('span','',r.file?`${r.file} · ${GB(r.size)}`:'no usable GGUF file'),el('span','',r.downloads.toLocaleString()+' downloads'),name);
      if(r.file)d.append(button('Get',async()=>{await api('api/models/import',{url:r.url,filename:r.file,model:name.value.trim()});showTab('jobs');}));
      return d;}));
  }catch(e){merr(e);$('mresults').replaceChildren();}}
function drawJobs(jobs){
  $('mjobs').replaceChildren(...(jobs.length?jobs:[null]).map(j=>{
    if(!j)return el('p','','No jobs yet.');
    const d=el('div','mrow'),bar=el('progress');bar.max=j.total||1;bar.value=j.state==='done'?bar.max:j.completed||0;
    d.append(el('b','',`${j.kind} ${j.model}`),el('span','',j.state),bar,el('span','',j.message||''));
    const r=j.result;
    if(r&&j.kind==='benchmark'){
      d.append(el('span','',`${r.seconds}s · ${r.tokens_per_second??'?'} tok/s · VRAM ${r.vram_used==null?'?':GB(r.vram_used)}`));
      if(r.thinking)for(const k of ['off','on']){const x=r.thinking[k];
        d.append(el('span','',`thinking ${k}: ${x.seconds}s, ${x.tokens} tokens (~${x.thinking_tokens} thinking)`));}}
    if(j.state==='running'&&['import','pull'].includes(j.kind))
      d.append(button('Cancel',()=>api(`api/models/jobs/${j.id}/cancel`,{})));
    return d;}));}
$('modelsbtn').onclick=openModels;$('mclose').onclick=closeModels;
for(const b of mp.querySelectorAll('.mtab'))b.onclick=()=>showTab(b.dataset.tab);
$('msearch').onsubmit=e=>{e.preventDefault();search();};
$('mpull').onsubmit=async e=>{e.preventDefault();const v=$('mpullname').value.trim();if(!v)return;
  try{merr();await api('api/models/pull',{model:v});$('mpullname').value='';showTab('jobs');}catch(err){merr(err);}};
$('munload').onclick=async()=>{try{merr();const r=await api('api/models/unload',{});
  $('merr').textContent=r.unloaded.length?'Unloaded '+r.unloaded.join(', '):'Nothing was loaded.';}catch(e){merr(e);}mrefresh();};
addEventListener('keydown',e=>{if(e.key==='Escape'&&!mp.hidden&&!['INPUT','SELECT'].includes(document.activeElement.tagName))closeModels();});
```

In `agentchat/page.html`:

1. CSS: change `#term,#help{position:fixed` to `#term,#help,#modelspanel{position:fixed`, `#term .box,#help .box{` to `#term .box,#help .box,#modelspanel .box{`, `#term .top,#help .top{` to `#term .top,#help .top,#modelspanel .top{`, and before `@media (max-width:800px)` add:

```css
.mtabs{display:flex;gap:6px}.mtab.on{background:var(--line);color:var(--fg)}
.mview{overflow:auto;max-height:60vh}.mrow{display:flex;flex-wrap:wrap;gap:8px 14px;align-items:center;
padding:8px 0;border-bottom:1px solid var(--line);font-size:14px}.mrow input,.mrow select{font:inherit;
padding:3px 6px;border-radius:6px;border:1px solid var(--line);background:var(--bg);color:var(--fg)}
.mrow input[type=number]{width:7em}#mstatus{color:var(--mute);font-size:13px}#merr{color:var(--claude);font-size:13px}
#msearch,#mpull{display:flex;gap:8px;margin:6px 0}#msearch input,#mpull input{flex:1}
```

2. Markup: replace `<button id=add class=ghost type=button title="Add a project folder">+ Add project</button></nav>` with:

```html
<button id=add class=ghost type=button title="Add a project folder">+ Add project</button>
<button id=modelsbtn class=ghost type=button title="Local models">Models</button></nav>
```

and add after the `<div id=term hidden>…</div></div>` block:

```html
<div id=modelspanel hidden><div class=box role=dialog aria-label="Local models">
<div class=top><span>Local models</span><button id=mclose class=ghost type=button>Close</button></div>
<div id=mstatus></div>
<div class=mtabs><button class="ghost mtab" data-tab=installed type=button>Installed</button>
<button class="ghost mtab" data-tab=get type=button>Get models</button>
<button class="ghost mtab" data-tab=jobs type=button>Jobs</button></div><div id=merr></div>
<section class=mview data-tab=installed><button id=munload class=ghost type=button>Unload now</button><div id=mlist></div></section>
<section class=mview data-tab=get hidden><form id=msearch><input id=mq placeholder="Search Hugging Face for GGUF models">
<button>Search</button></form><form id=mpull><input id=mpullname placeholder="Or pull from Ollama's library, e.g. qwen3:0.6b">
<button>Pull</button></form><div id=mresults></div></section>
<section class=mview data-tab=jobs hidden><div id=mjobs></div></section></div></div>
```

3. After the closing `</script>` of the page's script, add `<script src=models.js></script>`.

Check both scripts parse: extract with `python3 -c` as in the earlier pages and run `node --check` on each.

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest 2>&1 | tail -1`
Expected: `OK`.

- [ ] **Step 6: Browser check against the fake servers**

Start a demo service whose Models uses a `FakeOllama` (a short Python script in the scratchpad: `from tests.fake_ollama import FakeOllama`, add two models, `Models(root=<tmp>, ollama_url=fake.url, hub=Hub(fetch=<canned listing with a fitting and an oversized file>))`, `serve(port=8799, store=Store(<tmp>/data), deliver=False, models=m).serve_forever()` from the repository root). Open http://127.0.0.1:8799 → **Models**, and check:
1. The header shows "External Ollama at http://127.0.0.1:… · v0.34.2" and the GPU line.
2. Installed lists both models with size, verdict and context placeholder; the thinking model has the thinking choice; changing context or thinking persists after a refresh.
3. Benchmark → Jobs shows the result with the thinking off/on line.
4. Get models → Search shows the canned results sorted by score with verdicts; Pull `tiny` completes in Jobs.
5. Unload now reports what was unloaded; Delete asks, then the model is gone.
6. Esc closes the panel; dark mode readable; nothing scrolls sideways at 400 px.

Stop the demo service by its PID (check it with `ss -ltnp | grep 8799`).

- [ ] **Step 7: Commit**

```bash
git add agentchat/server.py agentchat/models.js agentchat/page.html tests/helpers.py tests/test_server.py
git commit -m "Model routes and the Models panel"
```

---

### Task 7: Docs, real Ollama, deploy

**Files:**
- Modify: `README.md`, `docs/specs/2026-09-27-local-models-design.md` (a Verified line)
- Create: `tests/test_real_ollama.py`

- [ ] **Step 1: One real test (skipped unless asked for)**

`tests/test_real_ollama.py`:

```python
"""Against a real Ollama: AGENT_CHAT_TEST_OLLAMA=<url> AGENT_CHAT_TEST_MODEL=<small model>."""
import os
import tempfile
import unittest

from agentchat import models
from agentchat.ollama import Ollama

URL, MODEL = os.environ.get("AGENT_CHAT_TEST_OLLAMA"), os.environ.get("AGENT_CHAT_TEST_MODEL")


@unittest.skipUnless(URL and MODEL, "set AGENT_CHAT_TEST_OLLAMA and AGENT_CHAT_TEST_MODEL")
class RealOllamaTest(unittest.TestCase):
    def test_benchmark_and_unload(self):
        ollama, tuning, jobs = Ollama(URL), models.Tuning(tempfile.mkdtemp()), models.Jobs()
        self.assertIn(MODEL, [m["name"] for m in ollama.tags()])
        v = jobs.start("benchmark", MODEL, lambda j: models.benchmark(j, ollama, MODEL, tuning))
        import time
        for _ in range(600):
            j = next(x for x in jobs.list() if x["id"] == v["id"])
            if j["state"] != "running":
                break
            time.sleep(0.5)
        self.assertEqual(j["state"], "done", j["message"])
        self.assertIsNotNone(j["result"]["tokens_per_second"])
        self.assertIn(MODEL, ollama.unload_all())
        self.assertEqual(ollama.loaded(), [])
```

Run: `python3 -m unittest tests.test_real_ollama` → `skipped`.

- [ ] **Step 2: README**

Add under "Install", after the paragraph about `--uninstall`:

```markdown
`install.sh` also downloads Ollama v0.34.2 (about 1.4 GB, checksum-checked)
into `runtime/` and runs it as `agent-chat-ollama` on 127.0.0.1:11436, tuned
like local-ai-chat (flash attention, q8_0 KV cache, one model at a time, 5
minute keep-alive). Models go to `~/.local/share/agent-chat/ollama-models`.
To use an Ollama you already run instead: `./install.sh --ollama-url
http://127.0.0.1:11434` (`--ollama-url own` switches back). Two Ollamas
share the GPU without coordinating, so only one should have a model loaded.
```

and under "Use", a new item:

```markdown
5. **Models** (bottom of the sidebar): search Hugging Face for GGUF models
   rated for your GPU and get one with a click, pull by Ollama name,
   benchmark (with thinking off and on for models that can think), set a
   model's context and thinking, and unload models from video memory.
```

- [ ] **Step 3: Commit, merge, deploy**

```bash
git add README.md tests/test_real_ollama.py
git commit -m "Docs and an opt-in real Ollama test for local models"
```

Merge into `main` in the live checkout (fast-forward), push. Before running `./install.sh`, tell the user it downloads about 1.4 GB and starts a second Ollama on port 11436; then run it.

- [ ] **Step 4: Live check with the user**

With the user: open **Models**, confirm the header shows agent-chat's Ollama and the RTX 5070 Ti, pull a small model (for example `qwen3:0.6b`), benchmark it, then run
`AGENT_CHAT_TEST_OLLAMA=http://127.0.0.1:11436 AGENT_CHAT_TEST_MODEL=qwen3:0.6b python3 -m unittest tests.test_real_ollama`.
Search Hugging Face for a model the user wants and Get it if they like. Record a "Verified" line in the spec, commit, push.
