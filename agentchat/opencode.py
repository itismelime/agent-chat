"""OpenCode agents: which local models they can use, and OpenCode's own config.

The service starts OpenCode with XDG_CONFIG_HOME pointing at this config, so
the user's own OpenCode settings and MCP servers are neither used nor changed.
"""
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
