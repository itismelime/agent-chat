import hashlib
import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from tests.fake_ollama import FakeOllama
from tests.helpers import start, stop

ROOT = Path(__file__).resolve().parent.parent
STUB = """#!/bin/sh
echo "{tool} $*" >> {calls}
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
        # a fake Ollama release and a fake running Ollama, so nothing is downloaded
        # and the real <clone>/runtime is never touched
        tmp = self.home.parent
        (tmp / "rel" / "bin").mkdir(parents=True)
        (tmp / "rel" / "bin" / "ollama").write_text("#!/bin/sh\nexit 0\n")
        (tmp / "rel" / "bin" / "ollama").chmod(0o755)
        self.tarball = tmp / "ollama.tar.zst"
        subprocess.run(["tar", "--zstd", "-cf", str(self.tarball), "-C", str(tmp / "rel"), "."],
                       check=True)
        self.ollama = FakeOllama()
        self.addCleanup(self.ollama.close)
        self.runtime = tmp / "runtime"

    def install(self, *args):
        env = {"HOME": str(self.home), "PATH": "%s:/usr/bin:/bin" % self.stubs,
               "AGENT_CHAT_PORT": str(self.port),
               "AGENT_CHAT_RUNTIME": str(self.runtime),
               "AGENT_CHAT_OLLAMA_DOWNLOAD": "file://%s" % self.tarball,
               "AGENT_CHAT_OLLAMA_SHA256": hashlib.sha256(self.tarball.read_bytes()).hexdigest(),
               "AGENT_CHAT_OLLAMA_PORT": self.ollama.url.rsplit(":", 1)[1]}
        env.update(getattr(self, "extra_env", {}))
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
        # tmux servers the service starts must outlive a service restart
        self.assertIn("KillMode=process", unit.read_text())
        # the service runs `codex queue`, so it needs the PATH the tools were found on
        self.assertIn("Environment=PATH=%s:/usr/bin:/bin" % self.stubs, unit.read_text())
        self.assertIn("running on http://127.0.0.1:%d" % self.port, r.stdout)
        calls = self.calls_made()
        self.assertIn("systemctl --user restart agent-chat", calls)
        self.assertIn("claude mcp add --scope user agent-chat -- %s/bin/chat mcp" % ROOT, calls)
        self.assertIn("codex mcp add agent-chat -- %s/bin/chat mcp" % ROOT, calls)
        # an old registration (a moved clone) is replaced, not kept
        self.assertLess(calls.index("claude mcp remove --scope user agent-chat"),
                        calls.index("claude mcp add --scope user agent-chat -- %s/bin/chat mcp" % ROOT))
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

    def test_installs_its_own_ollama_once(self):
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.runtime / "ollama" / "bin" / "ollama").exists())
        self.assertEqual((self.runtime / "ollama" / ".version").read_text().strip(), "v0.34.2")
        unit = (self.home / ".config/systemd/user/agent-chat-ollama.service").read_text()
        for line in ("OLLAMA_HOST=127.0.0.1:%s" % self.ollama.url.rsplit(":", 1)[1],
                     "OLLAMA_FLASH_ATTENTION=1", "OLLAMA_KV_CACHE_TYPE=q8_0",
                     "OLLAMA_NUM_PARALLEL=1", "OLLAMA_MAX_LOADED_MODELS=1",
                     "OLLAMA_CONTEXT_LENGTH=32768", "OLLAMA_KEEP_ALIVE=5m",
                     "agent-chat/ollama-models"):
            self.assertIn(line, unit)
        self.assertIn("systemctl --user restart agent-chat-ollama", self.calls_made())
        again = self.install()
        self.assertIn("already installed", again.stdout)

    def test_a_bad_checksum_installs_nothing(self):
        self.extra_env = {"AGENT_CHAT_OLLAMA_SHA256": "0" * 64}
        r = self.install()
        self.assertEqual(r.returncode, 1)
        self.assertIn("checksum", r.stderr)
        self.assertFalse((self.runtime / "ollama").exists())

    def test_external_ollama_and_back(self):
        r = self.install("--ollama-url", "http://127.0.0.1:11434")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("using http://127.0.0.1:11434", r.stdout)
        self.assertFalse((self.runtime / "ollama").exists())
        self.assertNotIn("systemctl --user restart agent-chat-ollama", self.calls_made())
        cfg = self.home / ".config/agent-chat/config.json"
        self.assertIn("11434", cfg.read_text())
        r = self.install("--ollama-url", "own")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue((self.runtime / "ollama").exists())
        self.assertEqual(self.install("--ollama-url", "http://evil.example:1").returncode, 2)

    def test_uninstall_keeps_the_models(self):
        self.install()
        models = self.home / ".local/share/agent-chat/ollama-models"
        self.assertTrue(models.is_dir())
        r = self.install("--uninstall")
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertFalse((self.runtime / "ollama").exists())
        self.assertFalse((self.home / ".config/systemd/user/agent-chat-ollama.service").exists())
        self.assertTrue(models.is_dir())

    def test_tests_leave_the_real_runtime_alone(self):
        before = (ROOT / "runtime").exists()
        self.install()
        self.install("--uninstall")
        self.assertEqual((ROOT / "runtime").exists(), before)

    def test_missing_tool_is_skipped(self):
        (self.stubs / "codex").unlink()
        r = self.install()
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertIn("codex not found: skipped", r.stdout)

    def test_bad_argument(self):
        self.assertEqual(self.install("--nope").returncode, 2)


if __name__ == "__main__":
    unittest.main()
