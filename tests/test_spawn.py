import os
import tempfile
import unittest
from pathlib import Path

from agentchat import spawn
from agentchat.store import StoreError

SCREENS = Path(__file__).resolve().parent / "data" / "screens"
TMUX_STUB = """#!/bin/sh
d="$(dirname "$0")"
printf '%s\\n' "$*" >> "$d/calls"
case "$1" in
  capture-pane) [ -e "$d/dead" ] && exit 1; cat "$d/screen" 2>/dev/null ;;
  has-session) [ -e "$d/dead" ] && exit 1 ;;
  new-session) [ -e "$d/fail" ] && { echo boom >&2; exit 1; } ;;
  rename-session) [ -e "$d/norename" ] && exit 1 ;;
esac
exit 0
"""


def stub_tools(test, names=("tmux", "claude", "codex")):
    """Put stub tmux/claude/codex first on PATH for the test; returns the stub dir."""
    d = Path(tempfile.mkdtemp())
    for name in names:
        f = d / name
        f.write_text(TMUX_STUB if name == "tmux" else "#!/bin/sh\nexit 0\n")
        f.chmod(0o755)
    old = os.environ["PATH"]
    os.environ["PATH"] = "%s:/usr/bin:/bin" % d
    test.addCleanup(os.environ.__setitem__, "PATH", old)
    return d


def calls(d):
    f = d / "calls"
    return f.read_text().splitlines() if f.exists() else []


class TmuxTest(unittest.TestCase):
    def setUp(self):
        self.d = stub_tools(self)

    def test_available(self):
        self.assertEqual(spawn.available(), {"tmux": True, "claude": True, "codex": True})
        (self.d / "codex").unlink()
        self.assertFalse(spawn.available()["codex"])

    def test_start_runs_the_tool_in_tmux(self):
        spawn.start("claude", "/proj dir", "agent-chat-p-abc123", "tok")
        self.assertEqual(calls(self.d), [
            "new-session -d -s agent-chat-p-abc123 -c /proj dir -e AGENT_CHAT_SPAWN=tok "
            "-- claude join the chat"])

    def test_start_errors(self):
        for tool, code in (("bash", 400),):
            with self.assertRaises(StoreError) as e:
                spawn.start(tool, "/p", "s", "t")
            self.assertEqual(e.exception.code, code)
        (self.d / "codex").unlink()
        with self.assertRaises(StoreError) as e:
            spawn.start("codex", "/p", "s", "t")
        self.assertEqual((e.exception.code, str(e.exception)), (503, "codex is not installed"))
        (self.d / "fail").touch()
        with self.assertRaises(StoreError) as e:
            spawn.start("claude", "/p", "s", "t")
        self.assertEqual(e.exception.code, 503)
        self.assertIn("boom", str(e.exception))

    def test_start_without_tmux(self):
        (self.d / "tmux").unlink()
        os.environ["PATH"] = str(self.d)
        with self.assertRaises(StoreError) as e:
            spawn.start("claude", "/p", "s", "t")
        self.assertEqual(str(e.exception), "tmux is not installed")

    def test_keys_and_text(self):
        spawn.send_key("s", "esc")
        spawn.send_key("s", "1")
        spawn.send_text("s", "-rf is not an option")
        self.assertEqual(calls(self.d), ["send-keys -t s Escape", "send-keys -t s 1",
                                         "send-keys -t s -l -- -rf is not an option",
                                         "send-keys -t s Enter"])
        for bad in (lambda: spawn.send_key("s", "F12"), lambda: spawn.send_text("s", ""),
                    lambda: spawn.send_text("s", "x" * 2001)):
            with self.assertRaises(StoreError) as e:
                bad()
            self.assertEqual(e.exception.code, 400)

    def test_screen_alive_rename_stop(self):
        (self.d / "screen").write_text("hello\n")
        self.assertEqual(spawn.screen("s"), "hello\n")
        self.assertTrue(spawn.alive("s"))
        self.assertTrue(spawn.rename("s", "t"))
        spawn.stop("s")
        self.assertEqual(calls(self.d)[-2:], ["rename-session -t s t", "kill-session -t s"])
        (self.d / "dead").touch()
        self.assertFalse(spawn.alive("s"))
        with self.assertRaises(StoreError) as e:
            spawn.screen("s")
        self.assertEqual(e.exception.code, 404)
        spawn.stop("s")  # already gone: no error


class NeedsYouTest(unittest.TestCase):
    def test_real_screens(self):
        files = sorted(SCREENS.glob("*.txt"))
        self.assertEqual(len(files), 6)
        for f in files:
            self.assertEqual(spawn.needs_you(f.read_text()), f.name.startswith("question-"), f.name)

    def test_only_the_bottom_of_the_screen_counts(self):
        old_question = (SCREENS / "question-codex-approval.txt").read_text()
        self.assertFalse(spawn.needs_you(old_question + "\n" * 30 + "› Ask Codex to do anything\n"))


if __name__ == "__main__":
    unittest.main()
