"""Agents started from the page, each in its own detached tmux session.

tmux is always called with an argument list, never through a shell, so a
project path or typed text cannot run anything.
"""
import re
import shutil
import subprocess

from .store import StoreError

TOOLS = ("claude", "codex")
PROMPT = "join the chat"
KEYS = {"1": "1", "2": "2", "3": "3", "up": "Up", "down": "Down",
        "enter": "Enter", "esc": "Escape"}
MAX_TEXT = 2000
SCREEN_LINES = 25
# A screen that asks something: menu footers and question lines from real
# Claude Code 2.1 and Codex 0.157 prompts (tests/data/screens), and a numbered
# menu with its cursor on an option.
# ponytail: text matching, so a new wording can slip past; add it here and a
# screen to tests/data/screens.
QUESTION = re.compile(r"enter to confirm|enter continue|esc to cancel|do you want to|"
                      r"would you like to|^\s*[❯›]\s*\d+\.", re.I | re.M)


def tmux(*args):
    return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)


def available():
    return {t: shutil.which(t) is not None for t in ("tmux",) + TOOLS}


def start(tool, path, session, token):
    """Start `tool "join the chat"` in a detached tmux session."""
    if tool not in TOOLS:
        raise StoreError(400, "tool must be claude or codex")
    missing = [t for t in ("tmux", tool) if shutil.which(t) is None]
    if missing:
        raise StoreError(503, "%s is not installed" % " and ".join(missing))
    r = tmux("new-session", "-d", "-s", session, "-c", path,
             "-e", "AGENT_CHAT_SPAWN=" + token, "--", tool, PROMPT)
    if r.returncode:
        raise StoreError(503, "tmux could not start it: %s" % r.stderr.strip())


def alive(session):
    return tmux("has-session", "-t", session).returncode == 0


def screen(session):
    r = tmux("capture-pane", "-p", "-t", session)
    if r.returncode:
        raise StoreError(404, "the agent's terminal has ended")
    return r.stdout


def send_key(session, key):
    if key not in KEYS:
        raise StoreError(400, "key must be one of: " + " ".join(KEYS))
    tmux("send-keys", "-t", session, KEYS[key])


def send_text(session, text):
    if not 0 < len(text) <= MAX_TEXT:
        raise StoreError(400, "text must be 1-%d characters" % MAX_TEXT)
    tmux("send-keys", "-t", session, "-l", "--", text)
    tmux("send-keys", "-t", session, "Enter")


def rename(session, new):
    return tmux("rename-session", "-t", session, new).returncode == 0


def stop(session):
    tmux("kill-session", "-t", session)  # already gone is fine


def needs_you(text):
    """Whether the bottom of a terminal screen asks the user something."""
    return bool(QUESTION.search("\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])))
