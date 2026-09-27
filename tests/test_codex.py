import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path

from agentchat.codex import deliver, find_thread
from agentchat.store import Store

T1 = "01a0e3df-6b97-7233-b194-a7cb90765ce4"
T2 = "01a0e3b1-5a19-7bf1-a9ab-e18b595b5590"
STUB = """#!/bin/sh
printf '%s\\n' "$@" > {out}
exit {code}
"""


class FindThreadTest(unittest.TestCase):
    def setUp(self):
        self.proc = Path(tempfile.mkdtemp())
        fd = self.proc / "123" / "fd"
        fd.mkdir(parents=True)
        (fd / "3").symlink_to("/s/2026/09/27/rollout-2026-09-27T19-17-45-%s.jsonl" % T1)
        (fd / "4").symlink_to("/s/2026/09/27/rollout-2026-09-27T18-27-26-%s.jsonl" % T2)
        (fd / "5").symlink_to("/dev/null")

    def test_picks_the_session_started_with_us(self):
        started = datetime(2026, 9, 27, 19, 17, 44).timestamp()
        self.assertEqual(find_thread(123, started, self.proc), T1)
        started = datetime(2026, 9, 27, 18, 27, 30).timestamp()
        self.assertEqual(find_thread(123, started, self.proc), T2)

    def test_none_when_no_session_is_close_or_no_process(self):
        started = datetime(2026, 9, 27, 21, 0, 0).timestamp()
        self.assertIsNone(find_thread(123, started, self.proc))
        self.assertIsNone(find_thread(999, started, self.proc))


class DeliverTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.out, self.bin = tmp / "args", tmp / "bin"
        self.bin.mkdir()
        self.path = os.environ["PATH"]
        os.environ["PATH"] = "%s:%s" % (self.bin, self.path)
        self.addCleanup(os.environ.__setitem__, "PATH", self.path)
        (tmp / "proj").mkdir()
        self.store = Store(tmp / "data")
        self.store.add_project(str(tmp / "proj"))
        self.store.join("proj", "cody", "codex", thread=T1)
        self.m = self.store.post("proj", "user", "@cody hello")

    def stub(self, code):
        s = self.bin / "codex"
        s.write_text(STUB.format(out=self.out, code=code))
        s.chmod(0o755)

    def test_queues_and_moves_the_cursor(self):
        self.stub(0)
        self.assertTrue(deliver(self.store, "proj", "cody", T1, self.m))
        args = self.out.read_text().splitlines()
        self.assertEqual(args[:4], ["queue", "--thread", T1, "--message"])
        self.assertIn("user: @cody hello", "\n".join(args[4:]))
        self.assertIn("addressed to you: reply", "\n".join(args[4:]))
        self.assertEqual(self.store.read("proj", "cody"), [])

    def test_failure_keeps_the_message_unread(self):
        self.stub(1)
        self.assertFalse(deliver(self.store, "proj", "cody", T1, self.m))
        self.assertEqual([m["text"] for m in self.store.read("proj", "cody")], ["@cody hello"])


if __name__ == "__main__":
    unittest.main()
