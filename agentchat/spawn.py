"""Agents started from the page, each in its own detached tmux session.

tmux is always called with an argument list, never through a shell, so a
project path or typed text cannot run anything.
"""
import json
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .store import StoreError, wakes

TOOLS = ("claude", "codex", "opencode")
PROMPT = "join the chat"
KEYS = {"1": "1", "2": "2", "3": "3", "up": "Up", "down": "Down",
        "enter": "Enter", "esc": "Escape"}
MAX_TEXT = 2000
# Codex treats fast input as a paste and swallows an Enter sent right after it;
# half a second apart it submits (checked with Codex 0.157 and Claude Code 2.1).
PASTE_PAUSE = 0.5
SCREEN_LINES = 25
JOIN_GRACE = 60   # seconds a start may take to join before it needs the user
POLL_SECONDS = 2
# A screen that asks something: menu footers and question lines from real
# Claude Code 2.1 and Codex 0.157 prompts (tests/data/screens), and a numbered
# menu with its cursor on an option.
# ponytail: text matching, so a new wording can slip past; add it here and a
# screen to tests/data/screens.
QUESTION = re.compile(r"enter to confirm|enter confirm|enter continue|esc to cancel|do you want to|"
                      r"would you like to|permission required|^\s*[❯›]\s*\d+\.", re.I | re.M)
# OpenCode's footer while it works (tests/data/screens/working-*)
WORKING = re.compile(r"esc interrupt", re.I)


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)


# tmux treats a target it cannot find as a prefix ("x" would hit "x2"); "="
# makes it match the session name exactly, and ":" names its current pane.
def exact(session):
    return "=" + session


def pane(session):
    return "=" + session + ":"


def available():
    return {t: shutil.which(t) is not None for t in ("tmux",) + TOOLS}


def claude_session(path, name, home=None):
    """The id of the Claude Code session in this project that took the chat
    name: the newest session log under ~/.claude/projects whose chat_join or
    chat_rename set it. None if there is none."""
    # ponytail: a name set with `chat rename` from a shell is not found; the
    # MCP tools are what agents use.
    root = Path(home or Path.home()) / ".claude" / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(path))
    needles = ['"name":"mcp__agent-chat__chat_%s","input":{"name":%s' % (tool, json.dumps(name))
               for tool in ("join", "rename")]
    best = None
    for d in root.glob(slug + "*"):  # the folder and its subfolders
        for f in d.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                if (best is None or mtime > best[0]) and any(n in f.read_text(errors="replace")
                                                              for n in needles):
                    best = (mtime, f.stem)
            except OSError:
                continue
    return best[1] if best else None


def start(tool, path, session, token, model=None, config_home=None, data_home=None,
          resume=None, name=None, personality=None):
    """Start the tool with the prompt "join the chat" in a detached tmux session;
    with resume (a session id), continue that session and rejoin as name."""
    if tool not in TOOLS:
        raise StoreError(400, "tool must be one of: " + ", ".join(TOOLS))
    if tool == "opencode" and not model:
        raise StoreError(400, "OpenCode needs a model")
    missing = [t for t in ("tmux", tool) if shutil.which(t) is None]
    if missing:
        raise StoreError(503, "%s is not installed" % " and ".join(missing))
    env = ["-e", "AGENT_CHAT_SPAWN=" + token]
    if tool == "opencode":
        env += ["-e", "XDG_CONFIG_HOME=" + config_home] if config_home else []
        env += ["-e", "XDG_DATA_HOME=" + data_home] if data_home else []
        # none of the user's own OpenCode or Claude Code setup: no CLAUDE.md, no
        # config from a tmux server's environment
        env += ["-e", "OPENCODE_DISABLE_CLAUDE_CODE=1", "-e", "OPENCODE_CONFIG=",
                "-e", "OPENCODE_CONFIG_DIR=", "-e", "OPENCODE_CONFIG_CONTENT="]
        command = ["opencode", "-m", "ac/" + model, "--prompt", PROMPT]
    elif resume:
        prompt = ("You are back in the chat: call chat_join with the name %s, then carry on "
                  "as before." % name)
        command = (["claude", "--resume", resume, prompt] if tool == "claude"
                   else ["codex", "resume", resume, "%s (start %s)" % (prompt, token)])
    else:
        # Codex's MCP servers are started by its app-server daemon and do not see
        # AGENT_CHAT_SPAWN, so Codex gets the token in its prompt for chat_join.
        prompt = PROMPT + (" with a name that fits your personality: %s" % personality
                           if personality else "")
        command = [tool, prompt if tool == "claude" else "%s (start %s)" % (prompt, token)]
    r = tmux("new-session", "-d", "-s", session, "-c", path, *env, "--", *command)
    if r.returncode:
        raise StoreError(503, "tmux could not start it: %s" % r.stderr.strip())


def alive(session):
    return tmux("has-session", "-t", exact(session)).returncode == 0


def screen(session):
    r = tmux("capture-pane", "-p", "-t", pane(session))
    if r.returncode:
        raise StoreError(404, "the agent's terminal has ended")
    return r.stdout


def send_key(session, key):
    if key not in KEYS:
        raise StoreError(400, "key must be one of: " + " ".join(KEYS))
    tmux("send-keys", "-t", pane(session), KEYS[key])


def send_text(session, text):
    if not 0 < len(text) <= MAX_TEXT:
        raise StoreError(400, "text must be 1-%d characters" % MAX_TEXT)
    tmux("send-keys", "-t", pane(session), "-l", "--", text)
    time.sleep(PASTE_PAUSE)
    tmux("send-keys", "-t", pane(session), "Enter")


def rename(session, new):
    return tmux("rename-session", "-t", exact(session), new).returncode == 0


def stop(session):
    tmux("kill-session", "-t", exact(session))  # already gone is fine


def needs_you(text):
    """Whether the bottom of a terminal screen asks the user something."""
    return bool(QUESTION.search("\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])))


def opencode_state(text):
    """OpenCode's state from its footer only (the conversation above it may
    contain any words): working, question, idle, or unknown (a dialog, starting)."""
    foot = "\n".join([l for l in text.rstrip().splitlines() if l.strip()][-3:]).lower()
    if "esc interrupt" in foot:
        return "working"
    if "enter confirm" in foot:
        return "question"
    return "idle" if "ctrl+p" in foot else "unknown"


def working(text):
    """Whether the bottom of the screen shows OpenCode working."""
    return bool(WORKING.search("\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])))


def format_message(m, name, personality=None):
    """One line to type into an OpenCode agent's terminal."""
    from .client import label
    who = (" Your personality: %s." % re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", personality).rstrip(".")
           if personality else "")
    tail = " (%s)%s Reply with chat_post." % (label(m, name), who)
    # typed as keystrokes into a coding agent: no control characters, which
    # could erase the "[chat]" prefix, interrupt it, or start a "!" shell line
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", m["text"].replace("\r", "").replace("\n", " / "))
    line = "[chat] %s: %s%s" % (m["from"], text, tail)
    if len(line) > MAX_TEXT:
        cut = " (… cut; chat_read has the whole message)"
        room = MAX_TEXT - len("[chat] %s: " % m["from"]) - len(cut) - len(tail)
        line = "[chat] %s: %s%s%s" % (m["from"], text[:room], cut, tail)
    return line


class Spawner:
    """Starts, links, watches and stops the agents started from the page."""

    def __init__(self, store, models=None, port=8765):
        self.store, self.models, self.port = store, models, port
        self.lock = threading.Lock()  # a rename on join vs the poller's liveness check

    def start(self, pid, tool, model=None, personality=None):
        self.store._check_role(personality)  # before tmux starts anything
        project = self.store.project(pid)
        token = secrets.token_hex(16)
        session = "agent-chat-%s-%s" % (pid, token[:6])
        home = None
        if tool == "opencode":
            from . import opencode
            usable = opencode.tool_models(self.models.ollama, self.models.gpu_total())
            if model not in usable:
                raise StoreError(400, "%s cannot call tools or is not installed; choose one of: %s"
                                 % (model, ", ".join(usable) or "none"))
            opencode.write_config(self.store.root, self.models.ollama.url, usable, self.port)
            home = str(opencode.config_home(self.store.root))
        data = str(opencode.data_home(self.store.root)) if tool == "opencode" else None
        start(tool, project["path"], session, token, model=model, config_home=home, data_home=data,
              personality=personality)
        record = self.store.add_spawned(pid, token, tool, session, personality)
        if model:
            record = self.store.update_spawned(pid, token, model=model)
        return record

    def resume(self, pid, name):
        """Continue an offline Claude or Codex agent's own session in tmux; it
        rejoins under its name, so it then has View terminal and Stop."""
        project, agent = self.store.project(pid), self.store._agent(pid, name)
        if any(name in (r["name"], r.get("resume")) for r in self.store.spawned(pid).values()):
            raise StoreError(409, "%s already has a terminal here; use View terminal" % name)
        if any(a["name"] == name and a["status"] != "offline" for a in self.store.status(pid)):
            raise StoreError(409, "%s is not offline" % name)
        if agent["kind"] == "claude":
            sid = claude_session(project["path"], name)
        elif agent["kind"] == "codex":
            sid = agent.get("thread")
        else:
            raise StoreError(400, "only Claude and Codex agents can be resumed")
        if not sid:
            raise StoreError(404, "no session of %s found to resume" % name)
        token = secrets.token_hex(16)
        session = "agent-chat-%s-%s" % (pid, token[:6])
        start(agent["kind"], project["path"], session, token, resume=sid, name=name)
        self.store.add_spawned(pid, token, agent["kind"], session)
        return self.store.update_spawned(pid, token, resume=name)

    def record(self, pid, token):
        records = self.store.spawned(pid)
        if token not in records:
            raise StoreError(404, "no started agent %s" % token)
        return records[token]

    def linked(self, pid, token, name):
        """After a join: give the tmux session the agent's name, if free."""
        new = "agent-chat-%s-%s" % (pid, name)
        with self.lock:
            if rename(self.record(pid, token)["session"], new):
                self.store.update_spawned(pid, token, session=new)

    def stop(self, pid, token):
        r = self.record(pid, token)
        stop(r["session"])
        self.store.drop_spawned(pid, token)
        if r.get("model") and self.models and not self._model_in_use(r["model"]):
            try:
                self.models.ollama.unload(r["model"])
            except Exception as e:  # stopping still succeeded
                print("agent-chat: could not unload %s: %s" % (r["model"], e), file=sys.stderr)

    def _model_in_use(self, model):
        for p in self.store.projects():
            for a in self.store.agents(p["id"]).values():
                if a.get("model") == model and not a.get("removed") and not a.get("gone"):
                    return True
            if any(r.get("model") == model for r in self.store.spawned(p["id"]).values()):
                return True
        return False

    def poll(self):
        """Drop starts whose session ended; flag those whose terminal asks."""
        for project in self.store.projects():
            pid = project["id"]
            for token in list(self.store.spawned(pid)):
                with self.lock:
                    r = self.store.spawned(pid).get(token)
                    if r is None:
                        continue
                    if not alive(r["session"]):
                        self.store.drop_spawned(pid, token, session=r["session"])
                        continue
                if r["name"] and self.store.waiting.get((pid, r["name"])):
                    need = False  # an open chat wait means the agent is idle
                else:
                    try:
                        text = screen(r["session"])
                    except StoreError:
                        continue
                    need = needs_you(text)
                    if r["tool"] == "opencode" and opencode_state(text) == "idle":
                        seen, r = r.get("oc_seen"), self._rescue(pid, token, r)
                        if r.get("oc_seen") != seen:
                            continue  # it was just typed to: look again next poll
                    if not r["name"]:
                        age = time.time() - datetime.fromisoformat(r["started"]).timestamp()
                        need = need or age > JOIN_GRACE
                    elif r["tool"] == "opencode":
                        need = self._type_unread(pid, r, text)
                self.store.set_needs(pid, token, need)

    def _rescue(self, pid, token, r):
        """Run the chat calls an idle OpenCode model wrote out as text instead of
        making them, and tell it so. Returns the start's fresh record."""
        from . import opencode
        root = self.store.root
        sid = r.get("oc_session") or opencode.find_session(
            root, self.store.project(pid)["path"],
            int(datetime.fromisoformat(r["started"]).timestamp() * 1000),
            {x.get("oc_session") for x in self.store.spawned(pid).values()})
        if not sid:
            return r
        fields = {"oc_session": sid}
        for t, tool, args in opencode.written_calls(root, sid, r.get("oc_seen", 0)):
            fields["oc_seen"] = t
            name = self.store.spawned(pid)[token]["name"]
            try:
                if tool == "chat_join" and not name:
                    name = self.store.join(pid, args.get("name", ""), "opencode", spawn=token)["name"]
                    self.linked(pid, token, name)
                    note = "You joined the chat as %s." % name
                elif tool == "chat_post" and name:
                    self.store.post(pid, name, args.get("text", ""))
                    note = "Your message was posted."
                else:
                    continue
            except StoreError as e:
                note = "Your %s failed: %s." % (tool, e)
            send_text(self.store.spawned(pid)[token]["session"],
                      "[chat] %s (Your tool call came out as text; agent-chat ran it for you.)" % note)
        return self.store.update_spawned(pid, token, **fields)

    def _type_unread(self, pid, r, text):
        """Type the oldest unread message that wakes this OpenCode agent, if its
        screen is idle. Unread comes from its stored cursor, so nothing is lost
        on a restart and nothing it already read with chat_read is typed.
        Returns whether its screen asks the user something."""
        name, state = r["name"], opencode_state(text)
        agent = self.store.agents(pid).get(name)
        if not agent or agent.get("removed") or agent.get("gone"):
            return state == "question"
        unread = [m for m in self.store.messages(pid, agent["cursor"]) if wakes(m, name)]
        if state == "idle" and unread:
            send_text(r["session"], format_message(unread[0], name, agent.get("role")))
            self.store.delivered(pid, name, unread[0]["n"])
        self.store.set_local(pid, name, busy=state != "idle" or bool(unread))
        return state == "question"

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # keep watching whatever one poll ran into
                print("agent-chat: poll failed: %s" % e, file=sys.stderr)
            time.sleep(POLL_SECONDS)

    def start_poller(self):
        threading.Thread(target=self.run, daemon=True).start()
