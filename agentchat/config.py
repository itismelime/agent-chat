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
