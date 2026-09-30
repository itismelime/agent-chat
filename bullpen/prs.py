"""GitHub pull request state for the chips the page puts after PR links:
state (open, draft, merged, closed) and checks, through the user's own `gh`.
Merged and closed PRs are remembered for good (they do not change), open ones
for TTL seconds, and at most GH_AT_ONCE `gh` run at a time."""
import json
import re
import shutil
import subprocess
import threading
import time

from .store import StoreError

REPO = re.compile(r"^[A-Za-z0-9_.-]{1,100}/[A-Za-z0-9_.-]{1,100}$")
TTL = 120
GH_AT_ONCE = threading.BoundedSemaphore(4)
_cache = {}  # (repo, n) -> (time, result)


def checks(rollup):
    """passed, failed or running for a statusCheckRollup, or None when there are none."""
    if not rollup:
        return None
    states = [(c.get("conclusion") or c.get("state") or c.get("status") or "").upper() for c in rollup]
    if any(s in ("FAILURE", "ERROR", "CANCELLED", "TIMED_OUT", "ACTION_REQUIRED") for s in states):
        return "failed"
    if all(s in ("SUCCESS", "NEUTRAL", "SKIPPED") for s in states):
        return "passed"
    return "running"


def state(repo, n, run=subprocess.run):
    if not (isinstance(repo, str) and REPO.match(repo)) or not str(n).isdigit():
        raise StoreError(400, "repo must be owner/name and n a number")
    key = (repo, int(n))
    hit = _cache.get(key)
    if hit and (hit[1]["state"] in ("merged", "closed") or time.monotonic() - hit[0] < TTL):
        return hit[1]
    if not shutil.which("gh"):
        raise StoreError(503, "gh is not installed")
    with GH_AT_ONCE:
        r = run(["gh", "pr", "view", str(int(n)), "-R", repo, "--json", "state,isDraft,title,statusCheckRollup"],
                capture_output=True, text=True, timeout=20)
    if r.returncode:
        raise StoreError(404, "no pull request %s#%s (%s)" % (repo, n, r.stderr.strip()[:200]))
    d = json.loads(r.stdout)
    result = {"state": "draft" if d.get("isDraft") and d.get("state") == "OPEN" else d.get("state", "").lower(),
              "title": d.get("title", ""), "checks": checks(d.get("statusCheckRollup")),
              "detail": [((c.get("name") or c.get("context") or "?"),
                          (c.get("conclusion") or c.get("state") or c.get("status") or "").lower())
                         for c in d.get("statusCheckRollup") or []]}
    _cache[key] = (time.monotonic(), result)
    return result
