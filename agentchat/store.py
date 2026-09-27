"""agent-chat storage: projects, messages and agents on disk.

Only the service process uses this; one lock serialises every write, and a
condition on that lock wakes blocked waits when a message is posted.
"""
import json
import os
import re
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
RESERVED = {"user", "all"}
KINDS = {"claude", "codex", "llm"}
THREAD = re.compile(r"^[0-9A-Za-z-]{1,64}$")
BUSY_SECONDS = 600
LINK_SECONDS = 300  # a join links to a start at most this old


class StoreError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def data_dir():
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "agent-chat"


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def slug(name):
    return re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:32] or "project"


def addressed(text):
    """Names in the leading @words: '@a, @b hi' -> ['a', 'b']."""
    names = []
    for word in text.split():
        if not word.startswith("@") or len(word) == 1:
            break
        names.append(word[1:].rstrip(",:").lower())
    return names


def wakes(message, name):
    """User messages wake every agent; agent messages only those addressed."""
    if message["from"] == name:
        return False
    return message["from"] == "user" or name in addressed(message["text"])


def write_json(path, data):
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(data, indent=1))
    os.replace(tmp, path)


class Store:
    def __init__(self, root=None):
        self.root = Path(root) if root else data_dir()
        (self.root / "projects").mkdir(parents=True, exist_ok=True)
        self.changed = threading.Condition(threading.RLock())
        self.waiting = {}  # (project id, name) -> number of open waits
        # deliver(pid, name, thread, message) hands a message to an agent that
        # is woken by queueing into its session (Codex); it must not block.
        self.deliver = None
        self.needs = set()  # (project id, start token) whose terminal asks the user

    # projects

    def projects(self):
        f = self.root / "projects.json"
        return json.loads(f.read_text())["projects"] if f.exists() else []

    def project(self, pid):
        for p in self.projects():
            if p["id"] == pid:
                return p
        raise StoreError(404, "no such project: %s" % pid)

    def find(self, path):
        """The registered project containing path (the deepest), or None."""
        by_path = {p["path"]: p for p in self.projects()}
        start = Path(path).expanduser().resolve()
        for d in (start, *start.parents):
            if str(d) in by_path:
                return by_path[str(d)]
        return None

    def add_project(self, path):
        """Register a folder. Returns (project, existing)."""
        p = Path(path).expanduser()
        if not p.is_absolute() or not p.is_dir():
            raise StoreError(400, "not an existing absolute directory: %s" % path)
        with self.changed:
            hit = self.find(p)
            if hit:
                return hit, True
            p = p.resolve()
            projects = self.projects()
            ids = {x["id"] for x in projects}
            base = pid = slug(p.name)
            i = 2
            while pid in ids:
                pid, i = "%s-%d" % (base, i), i + 1
            project = {"id": pid, "name": p.name, "path": str(p), "added": now()}
            (self.root / "projects" / pid).mkdir(exist_ok=True)
            write_json(self.root / "projects.json", {"projects": projects + [project]})
            return project, False

    def _dir(self, pid):
        self.project(pid)
        return self.root / "projects" / pid

    # messages

    def messages(self, pid, after=0):
        f = self._dir(pid) / "messages.jsonl"
        if not f.exists():
            return []
        out = []
        # ponytail: reads the whole log per call; add an offset index if logs grow large
        for line in f.read_text().splitlines():
            try:
                m = json.loads(line)
                n = m["n"]
            except (ValueError, KeyError, TypeError):
                print("agent-chat: skipped a corrupt line in %s" % f, file=sys.stderr)
                continue
            if n > after:
                out.append(m)
        return out

    def _last_n(self, pid):
        msgs = self.messages(pid)
        return msgs[-1]["n"] if msgs else 0

    def post(self, pid, sender, text):
        text = text.strip()
        if not text:
            raise StoreError(400, "empty message")
        with self.changed:
            if sender == "user":
                kind = "user"
            else:
                if sender not in self.agents(pid):
                    raise StoreError(403, "join the chat before posting")
                kind = self._agent(pid, sender)["kind"]
                self._touch(pid, sender)
            m = {"n": self._last_n(pid) + 1, "time": now(), "from": sender,
                 "kind": kind, "text": text}
            with open(self._dir(pid) / "messages.jsonl", "a") as f:
                f.write(json.dumps(m) + "\n")
            self.changed.notify_all()
            if self.deliver:
                for name, a in self.agents(pid).items():
                    if a.get("thread") and not a.get("removed") and not a.get("gone") \
                            and wakes(m, name):
                        self.deliver(pid, name, a["thread"], m)
            return m

    # agents

    def agents(self, pid):
        f = self._dir(pid) / "agents.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def _agent(self, pid, name, active=True):
        """The agent's record; with active=True, refuse a removed agent."""
        agents = self.agents(pid)
        if name not in agents:
            raise StoreError(404, "no agent %s in this project" % name)
        if active and agents[name].get("removed"):
            raise StoreError(403, "you were removed from this chat")
        return agents[name]

    def _update(self, pid, name, **fields):
        agents = self.agents(pid)
        agents[name].update(fields)
        write_json(self._dir(pid) / "agents.json", agents)

    def _touch(self, pid, name, cursor=None):
        fields = {"last_seen": now()}
        if cursor is not None:
            fields["cursor"] = cursor
        self._update(pid, name, **fields)

    def join(self, pid, name, kind, thread=None, spawn=None):
        name = (name or "").strip().lower()
        if not NAME.match(name) or name in RESERVED:
            raise StoreError(400, "a name is 1-32 of a-z, 0-9 and '-', starting with a "
                                  "letter or digit, and not 'user' or 'all'")
        if kind not in KINDS:
            raise StoreError(400, "kind must be one of: claude, codex, llm")
        if thread is not None and not (isinstance(thread, str) and THREAD.match(thread)):
            raise StoreError(400, "thread must be a session id")
        with self.changed:
            agents = self.agents(pid)
            if name in agents:
                raise StoreError(409, "the name %s is taken in this project; pick another" % name)
            agents[name] = {"kind": kind, "joined": now(), "last_seen": now(),
                            "cursor": self._last_n(pid), "removed": False, "notice": None,
                            "thread": thread}
            write_json(self._dir(pid) / "agents.json", agents)
            return dict(agents[name], name=name, spawn=self._link(pid, name, kind, spawn))

    def _link(self, pid, name, kind, spawn):
        """Link a joining agent to the start it came from. A known token always
        wins. Without one, a Codex agent (whose MCP server may not see the
        token) takes the only unlinked Codex start of the last LINK_SECONDS;
        anyone else is not linked, so a hand-started agent takes nothing."""
        records = self.spawned(pid)
        if not (isinstance(spawn, str) and spawn in records):
            recent = [t for t, r in records.items() if r["name"] is None and r["tool"] == kind
                      and time.time() - datetime.fromisoformat(r["started"]).timestamp()
                      < LINK_SECONDS]
            spawn = recent[0] if kind == "codex" and len(recent) == 1 else None
        if spawn:
            self.update_spawned(pid, spawn, name=name)
        return spawn

    # agents started from the page (spawn.py runs them; these are the records)

    def spawned(self, pid):
        f = self._dir(pid) / "spawned.json"
        return json.loads(f.read_text()) if f.exists() else {}

    def add_spawned(self, pid, token, tool, session):
        with self.changed:
            records = self.spawned(pid)
            records[token] = {"tool": tool, "session": session, "started": now(), "name": None}
            write_json(self._dir(pid) / "spawned.json", records)
            return dict(records[token], token=token)

    def update_spawned(self, pid, token, **fields):
        with self.changed:
            records = self.spawned(pid)
            if token not in records:
                raise StoreError(404, "no started agent %s" % token)
            records[token].update(fields)
            write_json(self._dir(pid) / "spawned.json", records)
            return dict(records[token], token=token)

    def drop_spawned(self, pid, token, session=None):
        """Forget a start; with session, only if it still has that session
        (a join may have renamed it since the caller checked)."""
        with self.changed:
            records = self.spawned(pid)
            if token in records:
                if session is not None and records[token]["session"] != session:
                    return
                name = records.pop(token)["name"]
                write_json(self._dir(pid) / "spawned.json", records)
                if name in self.agents(pid):
                    # its terminal is gone, so is the agent: Offline, nothing delivered
                    self._update(pid, name, gone=True)
            self.needs.discard((pid, token))

    def set_needs(self, pid, token, flag):
        with self.changed:
            (self.needs.add if flag else self.needs.discard)((pid, token))

    def spawned_list(self, pid):
        out = []
        for token, r in sorted(self.spawned(pid).items(), key=lambda kv: kv[1]["started"]):
            state = ("needs_you" if (pid, token) in self.needs
                     else "joined" if r["name"] else "starting")
            out.append(dict(r, token=token, state=state))
        return out

    def read(self, pid, name):
        """Messages after the agent's cursor; moves the cursor to the end."""
        with self.changed:
            msgs = self.messages(pid, self._agent(pid, name)["cursor"])
            self._touch(pid, name, msgs[-1]["n"] if msgs else None)
            return msgs

    def delivered(self, pid, name, n):
        """A message up to n was queued into the agent's session."""
        with self.changed:
            if self._agent(pid, name, active=False)["cursor"] < n:
                self._update(pid, name, cursor=n)

    def remove(self, pid, name):
        """User action: the agent may no longer post or read; its name stays reserved."""
        with self.changed:
            if self._agent(pid, name, active=False).get("removed"):
                raise StoreError(400, "%s is already removed" % name)
            self._update(pid, name, removed=True, notice="removed")
            self.changed.notify_all()

    def readd(self, pid, name):
        """User action: let a removed agent back in, from the newest message on."""
        with self.changed:
            if not self._agent(pid, name, active=False).get("removed"):
                raise StoreError(400, "%s is not removed" % name)
            self._update(pid, name, removed=False, notice="added back",
                         cursor=self._last_n(pid))
            self.changed.notify_all()

    def wait(self, pid, name, timeout, alive=None):
        """Block until a notice is pending or a message wakes the agent.
        Returns {"notice", "messages"} (all unread messages; the cursor moves
        past them), or None after timeout. A removed agent only gets notices.
        alive() is checked every second and before anything is handed over,
        so a waiter that went away does not consume messages."""
        deadline = time.monotonic() + timeout
        key = (pid, name)
        with self.changed:
            self._agent(pid, name, active=False)
            self._touch(pid, name)
            self.waiting[key] = self.waiting.get(key, 0) + 1
            try:
                while True:
                    if alive is not None and not alive():
                        return None
                    agent = self._agent(pid, name, active=False)
                    if agent.get("notice"):
                        self._update(pid, name, notice=None)
                        return {"notice": agent["notice"], "messages": []}
                    if not agent.get("removed"):
                        msgs = self.messages(pid, agent["cursor"])
                        if any(wakes(m, name) for m in msgs):
                            self._touch(pid, name, msgs[-1]["n"])
                            return {"notice": None, "messages": msgs}
                    left = deadline - time.monotonic()
                    if left <= 0:
                        return None
                    self.changed.wait(min(left, 1))
            finally:
                self.waiting[key] -= 1

    def status(self, pid):
        out = []
        with self.changed:
            started = {r["name"]: t for t, r in self.spawned(pid).items() if r["name"]}
            for name, a in sorted(self.agents(pid).items()):
                seen = datetime.fromisoformat(a["last_seen"]).timestamp()
                token = started.get(name)
                if a.get("removed"):
                    status = "removed"
                elif a.get("gone"):
                    status = "offline"
                elif token and (pid, token) in self.needs:
                    status = "needs_you"
                elif self.waiting.get((pid, name)) or a.get("thread"):
                    status = "waiting"
                elif time.time() - seen < BUSY_SECONDS:
                    status = "busy"
                else:
                    status = "offline"
                out.append({"name": name, "kind": a["kind"], "joined": a["joined"],
                            "status": status, "spawn": token})
        return out
