"""Your Claude plan's limits (the 5-hour and 7-day windows), as Claude Code hands them
to a statusline. Claude agents started from the page get `bullpen statusline` as
theirs (spawn.start): it keeps the limits for the page, then shows your own
statusline, so what you see in their terminal does not change."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from .config import data_dir

STALE_SECONDS = 15 * 60  # older than this, the page says when it was read
MARK = "bullpen statusline"  # never call ourselves as "your" statusline


def _file(root=None):
    return Path(root or data_dir()) / "plan.json"


def capture(raw, root=None):
    """Keep the rate limits from a statusline's input (Claude Code's JSON)."""
    try:
        limits = json.loads(raw).get("rate_limits")
    except (ValueError, AttributeError):
        return None
    if not isinstance(limits, dict) or not limits:
        return None
    f = _file(root)
    f.parent.mkdir(parents=True, exist_ok=True)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(json.dumps({"rate_limits": limits, "captured_at": time.time()}))
    os.replace(tmp, f)
    return limits


def get(root=None):
    """{"five_hour": {"used", "resets_at"}, "seven_day": {...}, "captured_at"} or None."""
    try:
        p = json.loads(_file(root).read_text())
    except (OSError, ValueError):
        return None
    out = {"captured_at": p.get("captured_at")}
    for key in ("five_hour", "seven_day"):
        w = (p.get("rate_limits") or {}).get(key) or {}
        if isinstance(w.get("used_percentage"), (int, float)):
            out[key] = {"used": round(w["used_percentage"]), "resets_at": w.get("resets_at")}
    return out if len(out) > 1 else None


def _left(ts):
    s = max(0, int((ts or 0) - time.time()))
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return "%dd %dh" % (d, h) if d else "%dh %dm" % (h, m)


def line(limits):
    """A statusline of our own, for when you have none: 5h 6% (4h 39m left) | 7d 31%."""
    bits = []
    for key, label in (("five_hour", "5h"), ("seven_day", "7d")):
        w = (limits or {}).get(key) or {}
        if isinstance(w.get("used_percentage"), (int, float)):
            bits.append("%s %d%% (%s left)" % (label, w["used_percentage"], _left(w.get("resets_at"))))
    return " | ".join(bits)


def yours():
    """Your own statusline command from ~/.claude/settings.json, or None."""
    try:
        s = json.loads((Path.home() / ".claude" / "settings.json").read_text())
        cmd = (s.get("statusLine") or {}).get("command")
    except (OSError, ValueError, AttributeError):
        return None
    return cmd if isinstance(cmd, str) and cmd.strip() and MARK not in cmd else None


def main():
    raw = sys.stdin.read()
    limits = capture(raw)
    cmd = yours()
    if cmd:
        try:
            # your own command from your settings, through a shell as Claude Code runs it;
            # Claude's JSON goes in on stdin, never into the command
            r = subprocess.run(cmd, shell=True, input=raw, capture_output=True, text=True, timeout=10)
            sys.stdout.write(r.stdout)
            return
        except (OSError, subprocess.SubprocessError):
            pass
    print(line(limits))


# Through the night: agents wrap up as a window nears its limit (usage-guard says so at
# 90%) and go idle; nothing would wake them after the reset. The Spawner holds typed
# messages while a window is used up and wakes the lead and whoever has work in progress
# a minute after the reset.
NEAR, FULL, GRACE = 90, 100, 60


def _windows(root=None):
    try:
        limits = json.loads(_file(root).read_text()).get("rate_limits") or {}
    except (OSError, ValueError, AttributeError):
        return []
    return [w for w in (limits.get("five_hour"), limits.get("seven_day"))
            if isinstance(w, dict) and isinstance(w.get("used_percentage"), (int, float))
            and isinstance(w.get("resets_at"), (int, float))]


def held_until(root=None, now=None):
    """While a window is used up: when it resets (the latest, if both are); else None."""
    now = time.time() if now is None else now
    ends = [w["resets_at"] for w in _windows(root) if w["used_percentage"] >= FULL and w["resets_at"] > now]
    return max(ends) if ends else None


def _wake_file(root=None):
    return Path(root or data_dir()) / "wake.json"


def arm(root=None, now=None):
    """A window near its limit: remember its reset, to wake the agents then (kept on
    disk, so a restart in the night does not lose it)."""
    now = time.time() if now is None else now
    ends = [w["resets_at"] for w in _windows(root) if w["used_percentage"] >= NEAR and w["resets_at"] > now]
    if not ends:
        return None
    f = _wake_file(root)
    try:
        at = json.loads(f.read_text()).get("at", 0)
    except (OSError, ValueError, AttributeError):
        at = 0
    if max(ends) > at:
        f.write_text(json.dumps({"at": max(ends)}))
    return max(max(ends), at)


def due(root=None, now=None):
    """The reset to wake the agents for, once it is GRACE seconds past (then forgotten); else None."""
    now = time.time() if now is None else now
    f = _wake_file(root)
    try:
        at = json.loads(f.read_text()).get("at")
    except (OSError, ValueError, AttributeError):
        return None
    if not isinstance(at, (int, float)) or now < at + GRACE:
        return None
    f.unlink(missing_ok=True)
    return at
