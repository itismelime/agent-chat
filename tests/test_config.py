import os
import subprocess
import tempfile
import unittest
from pathlib import Path

from agentchat import config

CHAT = str(Path(__file__).resolve().parent.parent / "bin" / "chat")


class ConfigTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        old = os.environ.get("XDG_CONFIG_HOME")
        os.environ["XDG_CONFIG_HOME"] = self.tmp
        self.addCleanup(lambda: os.environ.__setitem__("XDG_CONFIG_HOME", old) if old
                        else os.environ.pop("XDG_CONFIG_HOME", None))

    def test_default_is_own_ollama(self):
        self.assertEqual(config.ollama_url(), config.OWN_OLLAMA)
        self.assertTrue(config.OWN_OLLAMA.startswith("http://127.0.0.1:"))

    def test_set_external_and_back(self):
        config.set_ollama_url("http://localhost:11434/")
        self.assertEqual(config.ollama_url(), "http://localhost:11434")
        self.assertEqual(config.load(), {"ollama_url": "http://localhost:11434"})
        config.set_ollama_url("own")
        self.assertEqual(config.ollama_url(), config.OWN_OLLAMA)

    def test_refuses_non_local_urls(self):
        for bad in ("http://evil.example:11434", "https://127.0.0.1:1", "127.0.0.1:11434", ""):
            with self.assertRaises(ValueError):
                config.set_ollama_url(bad)

    def test_a_broken_file_means_defaults(self):
        config.config_path().parent.mkdir(parents=True)
        config.config_path().write_text("{not json")
        self.assertEqual(config.ollama_url(), config.OWN_OLLAMA)

    def test_cli(self):
        env = dict(os.environ, XDG_CONFIG_HOME=self.tmp)
        run = lambda *a: subprocess.run([CHAT, "config", "ollama-url", *a], env=env,
                                        capture_output=True, text=True)
        self.assertEqual(run("http://127.0.0.1:11434").returncode, 0)
        self.assertEqual(run().stdout.strip(), "http://127.0.0.1:11434")
        bad = run("http://evil.example:1")
        self.assertEqual(bad.returncode, 2)
        self.assertIn("must be", bad.stderr)


if __name__ == "__main__":
    unittest.main()
