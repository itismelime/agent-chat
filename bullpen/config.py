"""bullpen settings, in ${XDG_CONFIG_HOME:-~/.config}/bullpen/config.json."""
import json
import os
import re
from pathlib import Path

OWN_OLLAMA = "http://127.0.0.1:%s" % os.environ.get("BULLPEN_OLLAMA_PORT", "11436")
LOCAL_URL = re.compile(r"^http://(?:127\.0\.0\.1|localhost):\d{1,5}$")


def config_path():
    base = os.environ.get("XDG_CONFIG_HOME") or (
        os.environ.get("APPDATA") if os.name == "nt" else None) or os.path.expanduser("~/.config")
    from .store import moved
    return moved(Path(base) / "agent-chat", Path(base) / "bullpen") / "config.json"


def load():
    try:
        data = json.loads(config_path().read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def ollama_url():
    """The Ollama in use: the configured one, else bullpen's own."""
    url = load().get("ollama_url")
    return url if isinstance(url, str) and LOCAL_URL.match(url) else OWN_OLLAMA


def set_ollama_url(url):
    """Use the Ollama at url; "own" goes back to bullpen's own."""
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
