import json
import tempfile
import unittest
from pathlib import Path

from bullpen import opencode
from bullpen.ollama import Ollama
from tests.fake_ollama import GIB, FakeOllama

CLONE = Path(__file__).resolve().parent.parent


class OpenCodeTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.ollama = Ollama(self.fake.url)

    def test_tool_models_default_first(self):
        self.fake.add("small:9b", size=6 * GIB, capabilities=["completion", "tools"])
        self.fake.add("talker:1b", size=GIB, capabilities=["completion"])
        self.fake.add("gpt-oss:20b", size=18 * GIB, capabilities=["completion", "tools"])
        self.assertEqual(opencode.tool_models(self.ollama, 16 * GIB), ["gpt-oss:20b", "small:9b"])

    def test_without_the_default_the_largest_that_fits(self):
        self.fake.add("small:9b", size=6 * GIB, capabilities=["tools"])
        self.fake.add("mid:20b", size=11 * GIB, capabilities=["tools"])
        self.fake.add("huge:70b", size=40 * GIB, capabilities=["tools"])
        self.assertEqual(opencode.tool_models(self.ollama, 16 * GIB)[0], "mid:20b")
        self.assertEqual(opencode.tool_models(self.ollama, 0)[0], "huge:70b")

    def test_write_config(self):
        root = Path(tempfile.mkdtemp())
        path = opencode.write_config(root, "http://127.0.0.1:11436", ["a:1", "b:2"], 8765)
        self.assertEqual(path, root / "opencode-config" / "opencode" / "opencode.json")
        c = json.loads(path.read_text())
        self.assertEqual(c["provider"]["ac"]["options"]["baseURL"], "http://127.0.0.1:11436/v1")
        self.assertEqual(c["provider"]["ac"]["npm"], "@ai-sdk/openai-compatible")
        self.assertEqual(sorted(c["provider"]["ac"]["models"]), ["a:1", "b:2"])
        self.assertTrue(all(m["tools"] for m in c["provider"]["ac"]["models"].values()))
        self.assertEqual(c["mcp"]["bullpen"]["command"], [str(CLONE / "bin" / "chat"), "mcp"])
        self.assertEqual(c["mcp"]["bullpen"]["environment"], {"BULLPEN_PORT": "8765"})
        self.assertEqual(c["tools"], {"skill": False})
        self.assertEqual(list(c["mcp"]), ["bullpen"])  # nothing of the user's own config
        self.assertEqual(sorted(c["provider"]), ["ac"])
