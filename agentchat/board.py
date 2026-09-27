"""A kanban board per project. Every change is announced in the chat by the
sender "board", which (like an agent) wakes only the names it addresses."""
import json

from .store import StoreError, now, write_json

COLUMNS = {"todo": "To do", "doing": "In progress", "review": "Review", "done": "Done"}
MAX_TITLE, MAX_DESCRIPTION = 200, 4000


class Board:
    def __init__(self, store):
        self.store = store

    def _path(self, pid):
        return self.store._dir(pid) / "board.json"

    def _load(self, pid):
        f = self._path(pid)
        if not f.exists():
            return {"next": 1, "cards": {}}
        try:
            b = json.loads(f.read_text())
            b["next"], b["cards"]  # noqa: B018 (the shape this file must have)
            return b
        except (ValueError, KeyError, TypeError):
            raise StoreError(500, "the board file %s is damaged; fix or remove it" % f) from None

    def get(self, pid):
        b = self._load(pid)
        cards = [dict(c, id=int(n)) for n, c in b["cards"].items()]
        return {"columns": [[k, v] for k, v in COLUMNS.items()],
                "cards": sorted(cards, key=lambda c: c["id"])}

    # checks

    def _by(self, pid, by):
        agent = self.store.agents(pid).get(by)
        if by != "user" and (not agent or agent.get("removed")):
            raise StoreError(403, "only the user or an agent in this chat can change the board")

    def _assignee(self, pid, who):
        if who is not None and not isinstance(who, str):
            raise StoreError(400, "assignee must be a name")
        if who in (None, "", "user"):
            return who or None
        agent = self.store.agents(pid).get(who)
        if not agent or agent.get("removed"):
            raise StoreError(400, "no agent %s in this chat" % who)
        return who

    @staticmethod
    def _text(value, field, limit, required=False):
        if value is None:
            value = ""
        if not isinstance(value, str):
            raise StoreError(400, "%s must be text" % field)
        if field == "title":
            value = " ".join(value.split())
        if (required and not value) or len(value) > limit:
            raise StoreError(400, "%s must be %s%d characters" % (field, "1-" if required else "at most ", limit))
        return value

    @staticmethod
    def _column(column):
        if not isinstance(column, str) or column not in COLUMNS:
            raise StoreError(400, "column must be one of: " + ", ".join(COLUMNS))
        return column

    # changes

    def add(self, pid, by, title, description="", column="todo", assignee=None):
        self._by(pid, by)
        card = {"title": self._text(title, "title", MAX_TITLE, True),
                "description": self._text(description, "description", MAX_DESCRIPTION),
                "column": self._column(column or "todo"),
                "assignee": self._assignee(pid, assignee), "created_by": by, "updated": now()}
        with self.store.changed:
            b = self._load(pid)
            n = b["next"]
            b["cards"][str(n)], b["next"] = card, n + 1
            write_json(self._path(pid), b)
            self.store.notice(pid, '#%d "%s" added by %s (%s)' % (n, card["title"], by,
                                                                  COLUMNS[card["column"]]))
            if card["assignee"] and card["assignee"] not in ("user", by):
                self.store.notice(pid, '@%s you were assigned #%d "%s" by %s'
                                  % (card["assignee"], n, card["title"], by))
        return dict(card, id=n)

    def update(self, pid, n, by, title=None, description=None, column=None, assignee=False):
        """assignee=False leaves it; None clears it."""
        self._by(pid, by)
        with self.store.changed:
            b = self._load(pid)
            card = b["cards"].get(str(n))
            if card is None:
                raise StoreError(404, "no card #%s" % n)
            old = dict(card)
            if title is not None:
                card["title"] = self._text(title, "title", MAX_TITLE, True)
            if description is not None:
                card["description"] = self._text(description, "description", MAX_DESCRIPTION)
            if column is not None:
                card["column"] = self._column(column)
            if assignee is not False:
                card["assignee"] = self._assignee(pid, assignee)
            if card == old:
                return dict(card, id=int(n))
            card["updated"] = now()
            write_json(self._path(pid), b)
            to = card["assignee"]
            at = "@%s " % to if to and to not in ("user", by) else ""
            if card["assignee"] != old["assignee"] and at:
                self.store.notice(pid, '@%s you were assigned #%s "%s" by %s' % (to, n, old["title"], by))
            if card["column"] != old["column"]:
                self.store.notice(pid, '%s%s moved #%s "%s" to %s'
                                  % (at, by, n, old["title"], COLUMNS[card["column"]]))
            if (card["title"], card["description"]) != (old["title"], old["description"]):
                self.store.notice(pid, '%s%s edited #%s "%s"' % (at, by, n, card["title"]))
        return dict(card, id=int(n))

    def unassign(self, pid, name):
        """After an agent is forgotten: its cards lose their assignee."""
        with self.store.changed:
            b = self._load(pid)
            mine = [(n, c) for n, c in b["cards"].items() if c["assignee"] == name]
            if not mine:
                return
            for n, c in mine:
                c["assignee"], c["updated"] = None, now()
            write_json(self._path(pid), b)
            for n, c in mine:
                self.store.notice(pid, '#%s "%s" is unassigned (%s was forgotten)' % (n, c["title"], name))

    def delete(self, pid, n, by):
        self._by(pid, by)
        with self.store.changed:
            b = self._load(pid)
            card = b["cards"].pop(str(n), None)
            if card is None:
                raise StoreError(404, "no card #%s" % n)
            write_json(self._path(pid), b)
            to = card["assignee"]
            at = "@%s " % to if to and to not in ("user", by) else ""
            self.store.notice(pid, '%s%s deleted #%s "%s"' % (at, by, n, card["title"]))
