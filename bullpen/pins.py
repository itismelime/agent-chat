"""Pinned messages: what matters in a project for a while (a prod warning, a
decision). The page shows them above the chat; agents get them with the rules,
on join and with every message that wakes them. Only the page pins."""
import json

from .store import StoreError, write_json

MAX_PINS, MAX_SHOWN = 20, 200


def _path(store, pid):
    return store._dir(pid) / "pins.json"


def get(store, pid):
    f = _path(store, pid)
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else []
    except ValueError:
        raise StoreError(500, "the pins file %s is damaged; fix or remove it" % f) from None


def toggle(store, pid, n):
    """Pin message n, or unpin it. Returns the pinned numbers."""
    if type(n) is not int or n < 1:
        raise StoreError(404, "no message #%s" % n)
    m = next(iter(store.messages(pid, n - 1)), None)
    if not m or m["n"] != n:
        raise StoreError(404, "no message #%s" % n)
    with store.changed:
        pins = get(store, pid)
        if n in pins:
            pins.remove(n)
            what = "unpinned"
        elif len(pins) >= MAX_PINS:
            raise StoreError(400, "a project has at most %d pins" % MAX_PINS)
        else:
            pins.append(n)
            what = "pinned"
        write_json(_path(store, pid), pins)
        store.notice(pid, "📌 %s #%d (%s): %s" % (what, n, m["from"], " ".join(m["text"].split())[:80]))
    return pins


def summary(store, pid):
    """The pinned messages as agents get them, or ""."""
    pins = get(store, pid)
    if not pins:
        return ""
    by_n = {m["n"]: m for m in store.messages(pid, min(pins) - 1)}
    shown = ["#%d %s: %s" % (n, by_n[n]["from"], " ".join(by_n[n]["text"].split())[:MAX_SHOWN])
             for n in pins if n in by_n]
    return "Pinned in this chat (keep them in mind): " + " | ".join(shown) if shown else ""
