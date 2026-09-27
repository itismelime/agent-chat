import os
import tempfile
import unittest
from pathlib import Path

from agentchat import models
from agentchat.ollama import Ollama, OllamaError
from agentchat.store import StoreError
from tests.fake_ollama import GIB, FakeOllama

NVIDIA = """#!/bin/sh
echo "NVIDIA GeForce RTX 5070 Ti, 16303, 1200"
"""


class OllamaSetup(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.ollama = Ollama(self.fake.url)
        self.root = Path(tempfile.mkdtemp())
        self.tuning = models.Tuning(self.root)
        self.fake.add("qwen3.5:9b", size=6 * GIB, capabilities=["completion", "thinking"],
                      arch="qwen35", ctx=262144)
        self.fake.add("coder:14b", size=9 * GIB, capabilities=["completion"], arch="qwen2", ctx=32768)
        self.fake.add("gpt-oss:20b", size=13 * GIB, capabilities=["completion", "thinking"],
                      arch="gptoss", ctx=131072)


class ClientTest(OllamaSetup):
    def test_basic_calls(self):
        self.assertEqual(self.ollama.version(), "0.34.2")
        self.assertEqual(len(self.ollama.tags()), 3)
        self.ollama.chat("coder:14b", [{"role": "user", "content": "hi"}])
        self.assertEqual(self.ollama.loaded(), ["coder:14b"])
        self.assertEqual(self.ollama.unload_all(), ["coder:14b"])
        self.assertEqual(self.ollama.loaded(), [])
        self.ollama.delete("coder:14b")
        self.assertNotIn("coder:14b", self.fake.models)

    def test_errors(self):
        with self.assertRaises(OllamaError) as e:
            self.ollama.show("nope")
        self.assertIn("not found", str(e.exception))
        with self.assertRaises(OllamaError) as e:
            Ollama("http://127.0.0.1:1").version()
        self.assertIn("not reachable", str(e.exception))
        with self.assertRaises(OllamaError):
            list(self.ollama.stream("/api/pull", {"model": "bad"}))


class GpuTest(unittest.TestCase):
    def test_nvidia_smi(self):
        d = Path(tempfile.mkdtemp())
        (d / "nvidia-smi").write_text(NVIDIA)
        (d / "nvidia-smi").chmod(0o755)
        old = os.environ["PATH"]
        os.environ["PATH"] = "%s:%s" % (d, old)
        self.addCleanup(os.environ.__setitem__, "PATH", old)
        g = models.gpu()
        self.assertEqual(g, {"name": "NVIDIA GeForce RTX 5070 Ti", "total": 16303 * 1024 ** 2,
                             "used": 1200 * 1024 ** 2})
        os.environ["PATH"] = "/nonexistent"
        self.assertIsNone(models.gpu())


class ContextTest(unittest.TestCase):
    def test_recommend_ctx(self):
        self.assertEqual(models.recommend_ctx(16 * GIB), 32768)
        self.assertEqual(models.recommend_ctx(12 * GIB), 32768)
        self.assertEqual(models.recommend_ctx(12 * GIB - 1), 16384)
        self.assertEqual(models.recommend_ctx(8 * GIB), 16384)
        self.assertEqual(models.recommend_ctx(8 * GIB - 1), 8192)
        self.assertEqual(models.recommend_ctx(0), 8192)
        self.assertEqual(models.recommend_ctx(16 * GIB, 4000), 3968)
        self.assertEqual(models.recommend_ctx(16 * GIB, 100), 512)


class TuningTest(OllamaSetup):
    def test_facts_and_defaults(self):
        e = self.tuning.get("qwen3.5:9b", self.ollama, 16 * GIB)
        self.assertEqual(e, {"num_ctx": 32768, "override": None, "think": None,
                             "can_think": True, "levels": None})
        self.assertEqual(self.tuning.get("gpt-oss:20b", self.ollama, 16 * GIB)["levels"],
                         ["low", "medium", "high"])
        self.assertFalse(self.tuning.get("coder:14b", self.ollama, 16 * GIB)["can_think"])
        self.assertTrue((self.root / "models.json").exists())

    def test_request_values_per_use(self):
        req = lambda m, use: self.tuning.request(m, self.ollama, 16 * GIB, use)
        self.assertEqual(req("qwen3.5:9b", "talk"), (32768, False))
        self.assertEqual(req("qwen3.5:9b", "agent"), (32768, True))
        self.assertEqual(req("gpt-oss:20b", "agent"), (32768, "medium"))
        self.assertEqual(req("coder:14b", "agent"), (32768, False))
        self.tuning.set("qwen3.5:9b", self.ollama, 16 * GIB, num_ctx=8192, think="on")
        self.assertEqual(req("qwen3.5:9b", "talk"), (8192, True))
        self.tuning.set("gpt-oss:20b", self.ollama, 16 * GIB, think="high")
        self.assertEqual(req("gpt-oss:20b", "talk"), (32768, "high"))
        self.tuning.set("qwen3.5:9b", self.ollama, 16 * GIB, num_ctx=None, think=None)
        self.assertEqual(req("qwen3.5:9b", "talk"), (32768, False))

    def test_refuses_bad_settings(self):
        for model, fields in (("qwen3.5:9b", {"num_ctx": 100}), ("qwen3.5:9b", {"num_ctx": "big"}),
                              ("qwen3.5:9b", {"think": "high"}), ("coder:14b", {"think": "on"}),
                              ("gpt-oss:20b", {"think": "on"})):
            with self.assertRaises(StoreError) as e:
                self.tuning.set(model, self.ollama, 16 * GIB, **fields)
            self.assertEqual(e.exception.code, 400, (model, fields))

    def test_installed_list(self):
        self.ollama.chat("coder:14b", [{"role": "user", "content": "hi"}])
        rows = {r["name"]: r for r in models.installed(self.ollama, self.tuning,
                                                       {"total": 16 * GIB, "used": 0, "name": "x"})}
        self.assertTrue(rows["coder:14b"]["loaded"])
        self.assertEqual(rows["qwen3.5:9b"]["verdict"], "Plenty of room")  # 6 GiB × 1.2 / 16 GiB
        self.assertEqual(rows["gpt-oss:20b"]["levels"], ["low", "medium", "high"])

    def test_installed_survives_a_failing_show(self):
        real_show = self.ollama.show
        self.ollama.show = lambda m: (_ for _ in ()).throw(OllamaError("gone")) if m == "coder:14b" \
            else real_show(m)
        rows = {r["name"]: r for r in models.installed(self.ollama, self.tuning, None)}
        self.assertIsNone(rows["coder:14b"]["num_ctx"])
        self.assertEqual(rows["coder:14b"]["verdict"], "Unknown fit")
        self.assertEqual(rows["qwen3.5:9b"]["num_ctx"], 8192)  # no GPU known


if __name__ == "__main__":
    unittest.main()
