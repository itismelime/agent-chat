"""You, as bullpen shows you: a name (instead of "you") and a few lines about
you that agents get with their instructions. Both are optional."""
import json

from .store import StoreError, write_json

MAX_NAME, MAX_ABOUT = 32, 500


def get(store):
    f = store.root / "profile.json"
    try:
        return json.loads(f.read_text(encoding="utf-8")) if f.exists() else {"name": "", "about": ""}
    except ValueError:
        return {"name": "", "about": ""}


def save(store, name, about):
    name = " ".join(name.split()) if isinstance(name, str) else ""
    about = about.strip() if isinstance(about, str) else ""
    if len(name) > MAX_NAME or len(about) > MAX_ABOUT:
        raise StoreError(400, "a name is at most %d characters and about at most %d" % (MAX_NAME, MAX_ABOUT))
    p = {"name": name, "about": about}
    write_json(store.root / "profile.json", p)
    return p


def for_agents(store):
    """The line agents get about the user, or ""."""
    p = get(store)
    bits = (["The user's name is %s." % p["name"]] if p["name"] else []) + (
        ["About the user: %s" % " ".join(p["about"].split())] if p["about"] else [])
    return " ".join(bits)
