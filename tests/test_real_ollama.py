"""Against a real Ollama: AGENT_CHAT_TEST_OLLAMA=<url> AGENT_CHAT_TEST_MODEL=<small model>."""
import os
import tempfile
import unittest

from agentchat import models
from agentchat.ollama import Ollama

URL, MODEL = os.environ.get("AGENT_CHAT_TEST_OLLAMA"), os.environ.get("AGENT_CHAT_TEST_MODEL")


@unittest.skipUnless(URL and MODEL, "set AGENT_CHAT_TEST_OLLAMA and AGENT_CHAT_TEST_MODEL")
class RealOllamaTest(unittest.TestCase):
    def test_benchmark_and_unload(self):
        ollama, tuning, jobs = Ollama(URL), models.Tuning(tempfile.mkdtemp()), models.Jobs()
        self.assertIn(MODEL, [m["name"] for m in ollama.tags()])
        v = jobs.start("benchmark", MODEL, lambda j: models.benchmark(j, ollama, MODEL, tuning))
        import time
        for _ in range(600):
            j = next(x for x in jobs.list() if x["id"] == v["id"])
            if j["state"] != "running":
                break
            time.sleep(0.5)
        self.assertEqual(j["state"], "done", j["message"])
        self.assertIsNotNone(j["result"]["tokens_per_second"])
        self.assertIn(MODEL, ollama.unload_all())
        self.assertEqual(ollama.loaded(), [])
