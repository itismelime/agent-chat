"""Project rules: the user's standing instructions for every agent in a project.

Agents get them when they join and with every message that wakes them (like a
personality), so they hold through a long session. Only the page changes them;
each change is announced in the chat by "board", which wakes nobody.
"""
import json

from .store import StoreError, now, write_json

MAX_RULES, MAX_TEXT = 50, 500


def _path(store, pid):
    return store._dir(pid) / "rules.json"


def _load(store, pid):
    store.project(pid)  # 404 for an unknown project
    f = _path(store, pid)
    if not f.exists():
        return {"next": 1, "rules": []}
    try:
        return json.loads(f.read_text(encoding="utf-8"))
    except ValueError:
        raise StoreError(500, "the rules file %s is damaged; fix or remove it" % f) from None


def _text(text):
    text = " ".join(text.split()) if isinstance(text, str) else ""
    if not 0 < len(text) <= MAX_TEXT:
        raise StoreError(400, "a rule is 1-%d characters" % MAX_TEXT)
    return text


def get(store, pid):
    return _load(store, pid)["rules"]


def add(store, pid, text):
    text = _text(text)
    with store.changed:
        r = _load(store, pid)
        if len(r["rules"]) >= MAX_RULES:
            raise StoreError(400, "a project has at most %d rules" % MAX_RULES)
        rule = {"id": r["next"], "text": text, "updated": now()}
        r["rules"].append(rule)
        r["next"] += 1
        write_json(_path(store, pid), r)
        store.notice(pid, "Rule %d added: %s" % (len(r["rules"]), text))
    return rule


def _find(r, n):
    for i, rule in enumerate(r["rules"]):
        if rule["id"] == n:
            return i, rule
    raise StoreError(404, "no rule %s" % n)


def edit(store, pid, n, text):
    text = _text(text)
    with store.changed:
        r = _load(store, pid)
        i, rule = _find(r, n)
        if rule["text"] != text:
            rule.update(text=text, updated=now())
            write_json(_path(store, pid), r)
            store.notice(pid, "Rule %d changed: %s" % (i + 1, text))
    return rule


def delete(store, pid, n):
    with store.changed:
        r = _load(store, pid)
        i, rule = _find(r, n)
        r["rules"].pop(i)
        write_json(_path(store, pid), r)
        store.notice(pid, "Rule %d removed: %s" % (i + 1, rule["text"]))


def summary(rules):
    """The rules as agents get them, or "" when there are none."""
    if not rules:
        return ""
    return "Project rules (follow them): " + " ".join(
        "%d. %s" % (i + 1, r["text"]) for i, r in enumerate(rules))


def standing(store, pid):
    """What agents get with every wake: the rules, then the pinned messages."""
    from . import pins
    lead = store.lead(pid)
    line = ("The lead is %s: the user's messages without @names go to %s alone, who answers or "
            "hands the work on: small things with @name, real work as board cards (board_add with "
            "a description and an assignee wakes that agent with it; it moves the card to review "
            "when done, which wakes %s). @all reaches everyone." % (lead, lead, lead)) if lead else ""
    return " ".join(x for x in (line, summary(get(store, pid)), pins.summary(store, pid)) if x)


def fresh(store, pid, name):
    """(rules, personality) to send with a wake: the first time and after either
    changed, else ("", None). An agent keeps what it was told, so every wake does
    not pay for it again. Local models are stateless and take standing() each time."""
    import hashlib
    rules = standing(store, pid)
    with store.changed:
        agent = store.agents(pid).get(name)
        if agent is None:
            return rules, None
        seen = hashlib.sha1(("%s\0%s" % (rules, agent.get("role") or "")).encode()).hexdigest()[:16]
        if agent.get("told") == seen:
            return "", None
        store._update(pid, name, told=seen)
        return rules, agent.get("role")
