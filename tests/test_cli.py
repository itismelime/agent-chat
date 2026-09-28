import os
import subprocess
import threading
import unittest
from pathlib import Path

from agentchat.client import DOWN, Client
from tests.helpers import start, stop

CHAT = str(Path(__file__).resolve().parent.parent / "bin" / "chat")


class CliTest(unittest.TestCase):
    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        self.dir = self.tmp / "proj"
        (self.dir / "sub").mkdir(parents=True)
        self.env = dict(os.environ, AGENT_CHAT_PORT=str(self.port))

    def chat(self, *args, cwd=None):
        return subprocess.run([CHAT, *args], cwd=cwd or self.dir, env=self.env,
                              capture_output=True, text=True, timeout=20)

    def test_add_twice(self):
        self.assertIn("registered: proj", self.chat("add", str(self.dir)).stdout)
        self.assertIn("already registered: proj", self.chat("add", "sub").stdout)

    def test_post_from_subfolder(self):
        self.chat("add", ".")
        r = self.chat("post", "--as", "user", "hello", "there", cwd=self.dir / "sub")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual(self.store.messages("proj")[0]["text"], "hello there")

    def test_wait_prints_message_label_and_restart_line(self):
        self.chat("add", ".")
        Client(self.port).call("POST", "/api/projects/proj/agents", {"name": "alice", "kind": "claude"})
        threading.Timer(1.5, self.store.post, args=("proj", "user", "@alice ping")).start()
        r = self.chat("wait", "--as", "alice")  # outlives one 1 s long-poll first
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("user: @alice ping", r.stdout)
        self.assertIn("(addressed to you: reply)", r.stdout)
        self.assertIn("%s wait --as alice --project proj" % CHAT, r.stdout)

    def test_rename_then_wait_under_the_old_name(self):
        self.chat("add", ".")
        Client(self.port).call("POST", "/api/projects/proj/agents", {"name": "alice", "kind": "claude"})
        r = self.chat("rename", "--as", "alice", "Tester")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("renamed alice to tester", r.stdout)
        self.store.post("proj", "user", "@tester ping")
        r = self.chat("wait", "--as", "alice")  # a wait started before the rename
        self.assertIn("You were renamed: you are tester now.", r.stdout)
        self.assertIn("(addressed to you: reply)", r.stdout)
        self.assertIn("wait --as tester --project proj", r.stdout)

    def test_wait_prints_removed_notice(self):
        self.chat("add", ".")
        Client(self.port).call("POST", "/api/projects/proj/agents", {"name": "alice", "kind": "claude"})
        self.store.remove("proj", "alice")
        r = self.chat("wait", "--as", "alice")
        self.assertIn("You were removed from this chat", r.stdout)
        self.assertIn("wait --as alice --project proj", r.stdout)

    def test_wait_starts_with_the_personality(self):
        self.chat("add", ".")
        Client(self.port).call("POST", "/api/projects/proj/agents", {"name": "alice", "kind": "claude"})
        self.store.set_personality("proj", "alice", "You review critically")
        threading.Timer(0.5, self.store.post, args=("proj", "user", "@alice hi")).start()
        r = self.chat("wait", "--as", "alice")
        self.assertEqual(r.stdout.splitlines()[0], "Your personality: You review critically")

    def test_not_in_project(self):
        r = self.chat("post", "--as", "user", "x", cwd=self.tmp)
        self.assertEqual(r.returncode, 1)
        self.assertIn("not in a registered project", r.stderr)

    def test_errors_from_service(self):
        self.chat("add", ".")
        r = self.chat("post", "--as", "ghost", "x")
        self.assertEqual(r.returncode, 1)
        self.assertIn("join the chat before posting", r.stderr)

    def test_service_down(self):
        stop(self.server)
        r = self.chat("add", ".")
        self.assertEqual((r.returncode, r.stderr.strip()), (1, DOWN))


if __name__ == "__main__":
    unittest.main()
