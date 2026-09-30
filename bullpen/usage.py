"""What each agent uses, read from where its tool already records it: a Claude
session log (context in use, tokens out), a Codex rollout (context, window,
plan limits), or agent-chat's Ollama for local models (context, video memory).
Logs are read incrementally and cached, so polling stays cheap."""
import json
from pathlib import Path

_logs = {}  # path -> {"offset", "out", "ctx", "model"}: Claude logs read so far


def _claude_log(f):
    """Context in use and tokens out of one Claude session log, reading only
    what was appended since last time."""
    st = _logs.setdefault(str(f), {"offset": 0, "out": 0, "ctx": 0, "model": None})
    size = f.stat().st_size
    if size < st["offset"]:  # rewritten: start over
        st.update(offset=0, out=0, ctx=0, model=None)
    with open(f, "rb") as fh:
        fh.seek(st["offset"])
        chunk = fh.read(size - st["offset"])
    end = chunk.rfind(b"\n") + 1  # whole lines only; the rest next time
    for line in chunk[:end].splitlines():
        if b'"usage"' not in line:
            continue
        try:
            m = json.loads(line).get("message") or {}
        except ValueError:
            continue
        u = m.get("usage")
        if not isinstance(u, dict):
            continue
        st["out"] += u.get("output_tokens") or 0
        st["ctx"] = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens",
                                                   "cache_creation_input_tokens"))
        st["model"] = m.get("model") or st["model"]
    st["offset"] += end
    return {"ctx": st["ctx"], "out": st["out"], "model": st["model"]}


def claude(path, name, home=None):
    from .spawn import claude_session
    sid = claude_session(path, name, home)
    if not sid:
        return None
    root = Path(home or Path.home()) / ".claude" / "projects"
    for f in root.glob("*/%s.jsonl" % sid):
        return _claude_log(f)
    return None


def codex(thread, home=None):
    """The last token count in a Codex rollout: context, window, total, limits."""
    if not thread:
        return None
    files = sorted((Path(home or Path.home()) / ".codex" / "sessions").glob("*/*/*/rollout-*-%s.jsonl" % thread))
    if not files:
        return None
    with open(files[-1], "rb") as fh:
        fh.seek(0, 2)
        fh.seek(max(0, fh.tell() - 262144))
        tail = fh.read()
    for line in reversed(tail.splitlines()):
        if b'"token_count"' not in line:
            continue
        try:
            p = json.loads(line)["payload"]
        except (ValueError, KeyError):
            continue
        info, limits = p.get("info") or {}, p.get("rate_limits") or {}
        return {"ctx": (info.get("last_token_usage") or {}).get("input_tokens"),
                "window": info.get("model_context_window"),
                "total": (info.get("total_token_usage") or {}).get("total_tokens"),
                "limits": {k: (limits.get(k) or {}).get("used_percent") for k in ("primary", "secondary")}}
    return None


def local(ollama, model):
    """A local model's context size and video memory while Ollama has it loaded."""
    try:
        loaded = {m.get("name"): m for m in ollama.json("GET", "/api/ps").get("models", [])}
    except Exception:  # Ollama down: nothing to show
        return {"model": model, "loaded": False}
    m = loaded.get(model)
    if not m:
        return {"model": model, "loaded": False}
    return {"model": model, "loaded": True, "ctx": m.get("context_length"), "vram": m.get("size_vram")}


def for_project(store, models, pid):
    """{name: usage} for the project's agents that have something to show."""
    path, out = store.project(pid)["path"], {}
    spawned = {r["name"]: r for r in store.spawned(pid).values() if r.get("name")}
    for name, a in store.agents(pid).items():
        if a.get("removed"):
            continue
        try:
            if a["kind"] == "claude":
                u = claude(path, name)
            elif a["kind"] == "codex":
                u = codex(a.get("thread"))
            elif a.get("model") or spawned.get(name, {}).get("model"):
                u = local(models.ollama, a.get("model") or spawned[name]["model"]) if models else None
            else:
                u = None
        except OSError:
            u = None
        if u:
            out[name] = u
    return out
