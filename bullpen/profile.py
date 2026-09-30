"""You, as bullpen shows you: a name (instead of "you") and a few lines about
you that agents get with their instructions. Both are optional."""
import json

from .store import StoreError, write_json

MAX_NAME, MAX_ABOUT = 32, 500


def get(store):
    f = store.root / "profile.json"
    try:
        p = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    except ValueError:
        p = {}
    return {"name": p.get("name", ""), "about": p.get("about", ""), "away": p.get("away") is True}


def save(store, name=None, about=None, away=None):
    """What is given changes (None keeps it). Away: agents only wake for the user
    (store.wakes); back, the waits look again and pick up what came meanwhile."""
    p = get(store)
    if name is not None:
        p["name"] = " ".join(name.split()) if isinstance(name, str) else ""
    if about is not None:
        p["about"] = about.strip() if isinstance(about, str) else ""
    if away is not None:
        if not isinstance(away, bool):
            raise StoreError(400, "away must be true or false")
        p["away"] = away
    if len(p["name"]) > MAX_NAME or len(p["about"]) > MAX_ABOUT:
        raise StoreError(400, "a name is at most %d characters and about at most %d" % (MAX_NAME, MAX_ABOUT))
    with store.changed:
        write_json(store.root / "profile.json", p)
        store.changed.notify_all()
    return p


def for_agents(store):
    """The line agents get about the user, or ""."""
    p = get(store)
    bits = (["The user's name is %s." % p["name"]] if p["name"] else []) + (
        ["About the user: %s" % " ".join(p["about"].split())] if p["about"] else [])
    return " ".join(bits)
