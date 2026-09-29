"""Reactions: acknowledge a message without a reply. One emoji per person per
kind, toggled; they wake nobody. A reaction from the user on an agent's
question also clears it from Needs an answer (on the page)."""
import json

from .store import StoreError, write_json

REACTIONS = ("👍", "✅", "👀", "❤️", "🎉", "🙏", "😄", "👎")


def _path(store, pid):
    return store._dir(pid) / "reactions.json"


def get(store, pid):
    """{"<n>": {emoji: [who, ...]}} for the messages that have any."""
    f = _path(store, pid)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except ValueError:
        raise StoreError(500, "the reactions file %s is damaged; fix or remove it" % f) from None


def toggle(store, pid, n, who, emoji):
    """Add who's emoji to message n, or take it off again. Returns n's reactions."""
    if emoji not in REACTIONS:
        raise StoreError(400, "emoji must be one of: " + " ".join(REACTIONS))
    if who != "user":
        agent = store.agents(pid).get(who)
        if not agent or agent.get("removed"):
            raise StoreError(403, "join the chat before reacting")
    if type(n) is not int or n < 1 or not any(m["n"] == n for m in store.messages(pid, n - 1)[:1]):
        raise StoreError(404, "no message #%s" % n)
    with store.changed:
        all_ = get(store, pid)
        mine = all_.setdefault(str(n), {})
        names = mine.setdefault(emoji, [])
        if who in names:
            names.remove(who)
        else:
            names.append(who)
        for e in [e for e, w in mine.items() if not w]:
            del mine[e]
        if not mine:
            del all_[str(n)]
        write_json(_path(store, pid), all_)
    return all_.get(str(n), {})
