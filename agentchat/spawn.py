"""Agents started from the page, each in its own detached tmux session.

tmux is always called with an argument list, never through a shell, so a
project path or typed text cannot run anything.
"""
import re
import secrets
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime

from .store import StoreError

TOOLS = ("claude", "codex")
PROMPT = "join the chat"
KEYS = {"1": "1", "2": "2", "3": "3", "up": "Up", "down": "Down",
        "enter": "Enter", "esc": "Escape"}
MAX_TEXT = 2000
SCREEN_LINES = 25
JOIN_GRACE = 60   # seconds a start may take to join before it needs the user
POLL_SECONDS = 2
# A screen that asks something: menu footers and question lines from real
# Claude Code 2.1 and Codex 0.157 prompts (tests/data/screens), and a numbered
# menu with its cursor on an option.
# ponytail: text matching, so a new wording can slip past; add it here and a
# screen to tests/data/screens.
QUESTION = re.compile(r"enter to confirm|enter continue|esc to cancel|do you want to|"
                      r"would you like to|^\s*[❯›]\s*\d+\.", re.I | re.M)


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


def start(tool, path, session, token):
    """Start `tool "join the chat"` in a detached tmux session."""
    if tool not in TOOLS:
        raise StoreError(400, "tool must be claude or codex")
    missing = [t for t in ("tmux", tool) if shutil.which(t) is None]
    if missing:
        raise StoreError(503, "%s is not installed" % " and ".join(missing))
    # Codex's MCP servers are started by its app-server daemon and do not see
    # AGENT_CHAT_SPAWN, so Codex gets the token in its prompt for chat_join.
    prompt = PROMPT if tool == "claude" else "%s (start %s)" % (PROMPT, token)
    r = tmux("new-session", "-d", "-s", session, "-c", path,
             "-e", "AGENT_CHAT_SPAWN=" + token, "--", tool, prompt)
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
    tmux("send-keys", "-t", pane(session), "Enter")


def rename(session, new):
    return tmux("rename-session", "-t", exact(session), new).returncode == 0


def stop(session):
    tmux("kill-session", "-t", exact(session))  # already gone is fine


def needs_you(text):
    """Whether the bottom of a terminal screen asks the user something."""
    return bool(QUESTION.search("\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])))


class Spawner:
    """Starts, links, watches and stops the agents started from the page."""

    def __init__(self, store):
        self.store = store

    def start(self, pid, tool):
        project = self.store.project(pid)
        token = secrets.token_hex(16)
        session = "agent-chat-%s-%s" % (pid, token[:6])
        start(tool, project["path"], session, token)
        return self.store.add_spawned(pid, token, tool, session)

    def record(self, pid, token):
        records = self.store.spawned(pid)
        if token not in records:
            raise StoreError(404, "no started agent %s" % token)
        return records[token]

    def linked(self, pid, token, name):
        """After a join: give the tmux session the agent's name, if free."""
        new = "agent-chat-%s-%s" % (pid, name)
        if rename(self.record(pid, token)["session"], new):
            self.store.update_spawned(pid, token, session=new)

    def stop(self, pid, token):
        stop(self.record(pid, token)["session"])
        self.store.drop_spawned(pid, token)

    def poll(self):
        """Drop starts whose session ended; flag those whose terminal asks."""
        for project in self.store.projects():
            pid = project["id"]
            for token, r in self.store.spawned(pid).items():
                if not alive(r["session"]):
                    self.store.drop_spawned(pid, token)
                    continue
                if r["name"] and self.store.waiting.get((pid, r["name"])):
                    need = False  # an open chat wait means the agent is idle
                else:
                    try:
                        need = needs_you(screen(r["session"]))
                    except StoreError:
                        continue
                    if not r["name"]:
                        age = time.time() - datetime.fromisoformat(r["started"]).timestamp()
                        need = need or age > JOIN_GRACE
                self.store.set_needs(pid, token, need)

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # keep watching whatever one poll ran into
                print("agent-chat: poll failed: %s" % e, file=sys.stderr)
            time.sleep(POLL_SECONDS)

    def start_poller(self):
        threading.Thread(target=self.run, daemon=True).start()
