"""Codex sessions: find the one an MCP server serves, and queue chat
messages into it (an idle Codex session does not resume when a background
`bullpen wait` ends, but `codex queue` wakes it)."""
import os
import queue
import re
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .client import fmt, label

ROLLOUT = re.compile(r"rollout-(\d{4}-\d\d-\d\dT\d\d-\d\d-\d\d)-([0-9a-f-]{36})\.jsonl$")
STARTED = time.time()
MAX_GAP = 60


def find_thread(ppid=None, started=STARTED, proc="/proc"):
    """The Codex session id this MCP server serves, or None.

    The parent Codex process keeps each of its sessions' rollout logs open;
    ours is the one whose start time (local time, in the file name) is
    closest to this process's start, within MAX_GAP seconds."""
    # ponytail: a resumed older session is not found (its log is older than
    # us); falls back to chat_read. Codex exposing the id would fix this.
    fd_dir = Path(proc) / str(ppid or os.getppid()) / "fd"
    try:
        fds = list(fd_dir.iterdir())
    except OSError:
        return None
    best = None
    for fd in fds:
        try:
            m = ROLLOUT.search(os.readlink(fd))
        except OSError:
            continue
        if not m:
            continue
        gap = abs(datetime.strptime(m[1], "%Y-%m-%dT%H-%M-%S").timestamp() - started)
        if gap <= MAX_GAP and (best is None or gap < best[0]):
            best = (gap, m[2])
    return best[1] if best else None


def deliver(store, pid, name, thread, m):
    """Queue one message into a Codex session; on success the agent's cursor
    moves past it. Returns whether it was queued."""
    text = ("[bullpen, %s] %s\n(%s) Reply with the chat_post tool."
            % (pid, fmt(m), label(m, name)))
    from . import rules
    personality = store.agents(pid).get(name, {}).get("role")
    if personality:
        text = "Your personality: %s\n%s" % (personality, text)
    standing = rules.summary(rules.get(store, pid))
    if standing:
        text = "%s\n%s" % (standing, text)
    try:
        # shutil.which finds codex.cmd on Windows, which subprocess alone does not
        r = subprocess.run([shutil.which("codex") or "codex", "queue", "--thread", thread, "--message", text],
                           capture_output=True, text=True, timeout=60)
        ok, err = r.returncode == 0, r.stderr.strip()
    except (OSError, subprocess.TimeoutExpired) as e:
        ok, err = False, str(e)
    if ok:
        store.delivered(pid, name, m["n"])
    else:
        print("bullpen: could not queue message %d for %s: %s" % (m["n"], name, err),
              file=sys.stderr)
    return ok


class Deliverer:
    """store.deliver hook: one background thread, so messages keep their order."""

    def __init__(self, store):
        self.store, self.jobs = store, queue.Queue()
        threading.Thread(target=self.run, daemon=True).start()

    def __call__(self, pid, name, thread, m):
        self.jobs.put((pid, name, thread, m))

    def run(self):
        while True:
            job = self.jobs.get()
            try:
                deliver(self.store, *job)
            except Exception as e:  # one bad job must not stop delivery to every Codex
                print("bullpen: Codex delivery failed: %s" % e, file=sys.stderr)
