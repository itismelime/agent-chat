import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.helpers import start, stop

ROOT = Path(__file__).resolve().parent.parent
STUB = """#!/bin/sh
echo "{tool} $*" >> {calls}
[ "$2" = get ] && exit 1
exit 0
"""


class InstallTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        self.home, self.stubs, self.calls = tmp / "home", tmp / "stubs", tmp / "calls"
        self.home.mkdir()
        self.stubs.mkdir()
        for tool in ("systemctl", "claude", "codex"):
            s = self.stubs / tool
            s.write_text(STUB.format(tool=tool, calls=self.calls))
            s.chmod(0o755)
        # a running service stands in for the one systemctl would start
        _, self.server, self.port, _ = start()
        self.addCleanup(stop, self.server)

    def install(self, *args):
        env = {"HOME": str(self.home), "PATH": "%s:/usr/bin:/bin" % self.stubs,
               "AGENT_CHAT_PORT": str(self.port)}
        return subprocess.run([str(ROOT / "install.sh"), *args], env=env,
                              capture_output=True, text=True, timeout=60)

    def calls_made(self):
        return self.calls.read_text().splitlines() if self.calls.exists() else []

    def test_install_twice_then_uninstall(self):
        for _ in range(2):
            r = self.install()
            self.assertEqual(r.returncode, 0, r.stderr)
        link = self.home / ".local/bin/chat"
        unit = self.home / ".config/systemd/user/agent-chat.service"
        self.assertEqual(os.readlink(link), str(ROOT / "bin/chat"))
        self.assertIn('ExecStart=/usr/bin/env python3 "%s/bin/chat" serve' % ROOT, unit.read_text())
        self.assertIn("Environment=AGENT_CHAT_PORT=%d" % self.port, unit.read_text())
        self.assertIn("running on http://127.0.0.1:%d" % self.port, r.stdout)
        self.assertIn("network_access = true", r.stdout)  # Codex sandbox note
        calls = self.calls_made()
        self.assertIn("systemctl --user restart agent-chat", calls)
        self.assertIn("claude mcp add --scope user agent-chat -- %s/bin/chat mcp" % ROOT, calls)
        self.assertIn("codex mcp add agent-chat -- %s/bin/chat mcp" % ROOT, calls)
        self.assertIn("is not on your PATH", r.stdout)

        r = self.install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse(link.exists() or link.is_symlink())
        self.assertFalse(unit.exists())
        self.assertIn("claude mcp remove --scope user agent-chat", self.calls_made())
        self.assertIn("codex mcp remove agent-chat", self.calls_made())

    def test_fails_when_the_service_does_not_answer(self):
        stop(self.server)
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("did not start", r.stderr)

    def test_keeps_a_foreign_chat_command(self):
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "chat").write_text("someone else's program\n")
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertEqual((bin_dir / "chat").read_text(), "someone else's program\n")
        self.assertIn("left it alone", r.stdout)

    def test_replaces_a_dangling_old_link(self):
        bin_dir = self.home / ".local/bin"
        bin_dir.mkdir(parents=True)
        (bin_dir / "chat").symlink_to("/nonexistent/agent-chat/chat")
        self.assertEqual(self.install().returncode, 0)
        self.assertEqual(os.readlink(bin_dir / "chat"), str(ROOT / "bin/chat"))

    def test_missing_tool_is_skipped(self):
        (self.stubs / "codex").unlink()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("codex not found: skipped", r.stdout)

    def test_bad_argument(self):
        self.assertEqual(self.install("--nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
