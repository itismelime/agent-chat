"""OpenCode agents: which local models they can use, and OpenCode's own config.

The service starts OpenCode with XDG_CONFIG_HOME pointing at this config, so
the user's own OpenCode settings and MCP servers are neither used nor changed.
"""
import json
import re
import sqlite3
from pathlib import Path

from . import rating
from .ollama import OllamaError
from .store import write_json

DEFAULT = "gpt-oss:20b"
CLONE = Path(__file__).resolve().parent.parent
LINK_MS = 60_000  # a start's OpenCode session opens within this after the start
# a chat tool call written out as text, as qwen3-coder does under OpenCode's long
# prompt: "<function=bullpen_chat_join>\n<parameter=name>\nx\n</parameter>\n</function>"
CALL = re.compile(r"<function=(?:bullpen|agent-chat)_(chat_join|chat_post)>(.*?)</function>", re.S)
PARAM = re.compile(r"<parameter=(\w+)>\n?(.*?)\n?</parameter>", re.S)


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


def data_home(root):
    """OpenCode's own data (sessions), apart from the user's ~/.local/share/opencode."""
    return Path(root) / "opencode-data"


def write_config(root, ollama_url, models, port):
    """OpenCode's config: bullpen's Ollama, the chat tools, no skill tool
    (OpenCode would list every installed skill in its prompt)."""
    path = config_home(root) / "opencode" / "opencode.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    write_json(path, {
        "$schema": "https://opencode.ai/config.json",
        "provider": {"ac": {"npm": "@ai-sdk/openai-compatible", "name": "bullpen Ollama",
                            "options": {"baseURL": ollama_url.rstrip("/") + "/v1"},
                            "models": {m: {"name": m, "tools": True} for m in models}}},
        "mcp": {"bullpen": {"type": "local", "enabled": True,
                               "command": [str(CLONE / "bin" / "bullpen"), "mcp"],
                               "environment": {"BULLPEN_PORT": str(port)}}},
        "tools": {"skill": False}})
    return path


def _db(root):
    f = data_home(root) / "opencode" / "opencode.db"
    return sqlite3.connect("file:%s?mode=ro" % f, uri=True, timeout=2) if f.exists() else None


def find_session(root, directory, since_ms, taken=()):
    """The OpenCode session a start opened: the first top-level session in its
    folder created within LINK_MS after the start that no other start took, or None."""
    # ponytail: two OpenCode starts in one folder within a minute can swap;
    # OpenCode taking a session id on start would fix it.
    db = _db(root)
    if db is None:
        return None
    try:
        rows = db.execute("SELECT id FROM session WHERE directory = ? AND parent_id IS NULL "
                          "AND time_created BETWEEN ? AND ? ORDER BY time_created",
                          (directory, since_ms, since_ms + LINK_MS)).fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    return next((r[0] for r in rows if r[0] not in taken), None)


def written_calls(root, session, after_ms):
    """chat_join and chat_post calls the model wrote as text in that session
    after after_ms: [(time_ms, tool, {param: value})], oldest first."""
    db = _db(root)
    if db is None:
        return []
    try:
        rows = db.execute("SELECT time_created, data FROM part WHERE session_id = ? AND "
                          "time_created > ? ORDER BY time_created", (session, after_ms)).fetchall()
    except sqlite3.Error:
        return []
    finally:
        db.close()
    out = []
    for t, data in rows:
        try:
            part = json.loads(data)
        except ValueError:
            continue
        if part.get("type") == "text":
            for m in CALL.finditer(part.get("text") or ""):
                out.append((t, m[1], dict(PARAM.findall(m[2]))))
    return out


def named_session(root, name):
    """(session id, model) of the newest OpenCode session whose chat_join or
    chat_rename took this name, or None. The MCP server was agent-chat before
    the rename to bullpen."""
    db = _db(root)
    if db is None:
        return None
    try:
        rows = db.execute("SELECT p.session_id, p.data, s.model FROM part p JOIN session s ON s.id = p.session_id "
                          "WHERE p.data LIKE '%chat_join%' OR p.data LIKE '%chat_rename%' "
                          "ORDER BY p.time_created DESC").fetchall()
    except sqlite3.Error:
        return None
    finally:
        db.close()
    for sid, data, model in rows:
        try:
            part, model = json.loads(data), json.loads(model or "{}").get("id")
        except (ValueError, AttributeError):
            continue
        tool = part.get("tool") or ""
        if (part.get("type") == "tool" and tool.split("_chat_")[0] in ("bullpen", "agent-chat")
                and ((part.get("state") or {}).get("input") or {}).get("name") == name and model):
            return sid, model
    return None
