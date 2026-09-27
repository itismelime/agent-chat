import os
import tempfile
import time
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


class SpawnerTest(unittest.TestCase):
    def setUp(self):
        from agentchat.store import Store
        self.d = stub_tools(self)
        tmp = Path(tempfile.mkdtemp())
        (tmp / "proj").mkdir()
        self.store = Store(tmp / "data")
        self.store.add_project(str(tmp / "proj"))
        self.sp = spawn.Spawner(self.store)

    def test_two_starts_get_their_own_sessions(self):
        a, b = self.sp.start("proj", "claude"), self.sp.start("proj", "claude")
        self.assertNotEqual(a["token"], b["token"])
        self.assertNotEqual(a["session"], b["session"])
        self.assertTrue(a["session"].startswith("agent-chat-proj-"))
        self.assertEqual(len(self.store.spawned("proj")), 2)

    def test_linked_renames_the_session(self):
        r = self.sp.start("proj", "claude")
        self.sp.linked("proj", r["token"], "alice")
        self.assertEqual(self.store.spawned("proj")[r["token"]]["session"], "agent-chat-proj-alice")
        r2 = self.sp.start("proj", "codex")
        (self.d / "norename").touch()
        self.sp.linked("proj", r2["token"], "alice")  # name clash in tmux: keeps its old name
        self.assertEqual(self.store.spawned("proj")[r2["token"]]["session"], r2["session"])

    def test_stop_even_when_already_gone(self):
        r = self.sp.start("proj", "claude")
        (self.d / "dead").touch()
        self.sp.stop("proj", r["token"])
        self.assertEqual(self.store.spawned("proj"), {})
        with self.assertRaises(StoreError) as e:
            self.sp.stop("proj", r["token"])
        self.assertEqual(e.exception.code, 404)

    def test_poll_marks_questions_and_drops_ended_sessions(self):
        r = self.sp.start("proj", "codex")
        self.store.join("proj", "cody", "codex", thread="t-1", spawn=r["token"])  # always "waiting"
        (self.d / "screen").write_text((SCREENS / "question-codex-approval.txt").read_text())
        self.sp.poll()
        self.assertEqual(self.store.status("proj")[0]["status"], "needs_you")
        (self.d / "screen").write_text((SCREENS / "idle-codex-idle.txt").read_text())
        self.sp.poll()
        self.assertEqual(self.store.status("proj")[0]["status"], "waiting")
        (self.d / "dead").touch()
        self.sp.poll()
        self.assertEqual(self.store.spawned("proj"), {})

    def test_poll_unjoined_needs_you_after_grace(self):
        r = self.sp.start("proj", "claude")
        (self.d / "screen").write_text("starting...\n")
        self.sp.poll()
        self.assertEqual(self.store.spawned_list("proj")[0]["state"], "starting")
        self.store.update_spawned("proj", r["token"], started="2020-01-01T00:00:00+00:00")
        self.sp.poll()
        self.assertEqual(self.store.spawned_list("proj")[0]["state"], "needs_you")

    def test_poll_skips_an_agent_with_an_open_wait(self):
        r = self.sp.start("proj", "claude")
        self.store.join("proj", "alice", "claude", spawn=r["token"])
        (self.d / "screen").write_text((SCREENS / "question-claude-trust.txt").read_text())
        self.store.waiting[("proj", "alice")] = 1
        self.sp.poll()
        self.assertEqual(self.store.status("proj")[0]["status"], "waiting")

    def test_records_survive_a_restart(self):
        from agentchat.store import Store
        r = self.sp.start("proj", "claude")
        again = spawn.Spawner(Store(self.store.root))
        self.assertEqual(again.record("proj", r["token"])["tool"], "claude")

@unittest.skipIf(not os.path.exists("/usr/bin/tmux"), "tmux not installed")
class RealTmuxTest(unittest.TestCase):
    """A fake `claude` in a real tmux server of its own."""

    def setUp(self):
        from agentchat.store import Store
        tmp = Path(tempfile.mkdtemp())
        (tmp / "bin").mkdir()
        (tmp / "proj").mkdir()
        fake = tmp / "bin" / "claude"
        fake.write_text('#!/bin/sh\necho "fake agent: $1"\necho "Do you want to proceed?"\n'
                        'read line\necho "got: $line"\nsleep 30\n')
        fake.chmod(0o755)
        env = {"PATH": "%s:/usr/bin:/bin" % (tmp / "bin"), "TMUX_TMPDIR": str(tmp)}
        old = {k: os.environ.get(k) for k in ("PATH", "TMUX_TMPDIR", "TMUX")}
        os.environ.update(env)
        os.environ.pop("TMUX", None)
        self.addCleanup(self.restore, old)
        self.store = Store(tmp / "data")
        self.store.add_project(str(tmp / "proj"))
        self.sp = spawn.Spawner(self.store)

    def restore(self, old):
        spawn.tmux("kill-server")
        for k, v in old.items():
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    def wait_for(self, session, text):
        for _ in range(50):
            got = spawn.screen(session)
            if text in got:
                return got
            time.sleep(0.1)
        self.fail("%r never appeared in:\n%s" % (text, got))

    def test_start_read_answer_stop(self):
        r = self.sp.start("proj", "claude")
        got = self.wait_for(r["session"], "fake agent: join the chat")
        self.assertTrue(spawn.needs_you(got))
        spawn.send_text(r["session"], "hello")
        self.wait_for(r["session"], "got: hello")
        self.sp.stop("proj", r["token"])
        self.assertFalse(spawn.alive(r["session"]))

if __name__ == "__main__":
    unittest.main()
