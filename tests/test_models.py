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
    @unittest.skipIf(os.name == "nt", "the nvidia-smi stub is a shell script")
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


import threading  # noqa: E402
import time  # noqa: E402

from tests.fake_ollama import FileHost  # noqa: E402


def wait_done(jobs, job_id, seconds=10):
    for _ in range(seconds * 20):
        j = next(j for j in jobs.list() if j["id"] == job_id)
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("job still running")


class HubTest(unittest.TestCase):
    def test_search_rates_and_caches(self):
        calls = []
        listing = [{"id": "a/good-GGUF", "downloads": 10 ** 5, "likes": 100, "trendingScore": 10,
                    "lastModified": "2026-09-01T00:00:00Z", "siblings": []},
                   {"id": "b/bad", "downloads": 1, "siblings": []}]
        details = {"a/good-GGUF": [{"rfilename": "good-Q4_K_M.gguf", "size": 5 * GIB}],
                   "b/bad": [{"rfilename": "README.md", "size": 1}]}

        def fetch(url):
            calls.append(url)
            if "/api/models?" in url:
                return listing
            return {"siblings": details[url.split("/api/models/")[1].split("?")[0]]}
        hub = models.Hub(fetch=fetch)
        rated = hub.search("good", 16 * GIB)
        self.assertEqual([r["id"] for r in rated], ["a/good-GGUF", "b/bad"])
        self.assertEqual(rated[0]["file"], "good-Q4_K_M.gguf")
        self.assertIn("filter=gguf", calls[0])
        self.assertIn("search=good", calls[0])
        n = len(calls)
        hub.search("good", 16 * GIB)
        self.assertEqual(len(calls), n)  # cached

    def test_results_with_failed_sizes_are_not_cached(self):
        calls = []

        def fetch(url):
            calls.append(url)
            if "/api/models?" in url:
                return [{"id": "a/x", "siblings": []}]
            raise OSError("rate limited")
        hub = models.Hub(fetch=fetch)
        hub.search("x", 0)
        n = len(calls)
        hub.search("x", 0)
        self.assertGreater(len(calls), n)

    def test_search_error_keeps_old_results(self):
        state = {"fail": False}

        def fetch(url):
            if state["fail"]:
                raise OSError("rate limited")
            return [] if "/api/models?" in url else {"siblings": []}
        hub = models.Hub(fetch=fetch)
        hub.search("x", 0)
        hub.cache["x"] = (0, hub.cache["x"][1])  # expired
        state["fail"] = True
        self.assertEqual(hub.search("x", 0), [])
        with self.assertRaises(StoreError) as e:
            hub.search("never searched", 0)
        self.assertEqual(e.exception.code, 502)


class JobsTest(unittest.TestCase):
    def test_duplicate_refused_and_cancel_rules(self):
        jobs, gate = models.Jobs(), threading.Event()
        v = jobs.start("import", "m", lambda job: gate.wait(5))
        with self.assertRaises(StoreError) as e:
            jobs.start("import", "m", lambda job: None)
        self.assertEqual(e.exception.code, 409)
        b = jobs.start("benchmark", "m", lambda job: {"ok": 1})
        self.assertEqual(wait_done(jobs, b["id"])["result"], {"ok": 1})
        with self.assertRaises(StoreError) as e:
            jobs.cancel(b["id"])
        self.assertEqual(e.exception.code, 400)
        with self.assertRaises(StoreError) as e:
            jobs.cancel("nope")
        self.assertEqual(e.exception.code, 404)
        gate.set()
        self.assertEqual(wait_done(jobs, v["id"])["state"], "done")

    def test_an_unexpected_error_still_ends_the_job(self):
        import http.client
        jobs = models.Jobs()
        j = jobs.start("import", "m", lambda job: (_ for _ in ()).throw(http.client.IncompleteRead(b"x", 10)))
        done = wait_done(jobs, j["id"])
        self.assertEqual(done["state"], "failed")
        self.assertIn("IncompleteRead", done["message"])
        jobs.start("import", "m", lambda job: None)  # a retry is not refused

    def test_failures_become_messages(self):
        jobs = models.Jobs()
        j = jobs.start("pull", "m", lambda job: (_ for _ in ()).throw(OllamaError("boom")))
        done = wait_done(jobs, j["id"])
        self.assertEqual((done["state"], done["message"]), ("failed", "boom"))


class ImportTest(OllamaSetup):
    def run_import(self, data, delay=0, free=lambda p: 10 ** 15, cancel_after=None):
        host = FileHost(data, delay)
        self.addCleanup(host.close)
        jobs = models.Jobs()
        work = self.root / "imports"
        v = jobs.start("import", "new:q4", lambda job: models.import_gguf(
            job, self.ollama, host.url_for("r/resolve/main/new-Q4_K_M.gguf"),
            "new-Q4_K_M.gguf", "new:q4", work, free=free))
        if cancel_after:
            time.sleep(cancel_after)
            jobs.cancel(v["id"])
        return wait_done(jobs, v["id"]), work

    def test_import_hashes_uploads_and_creates(self):
        import hashlib
        data = os.urandom(300 * 1024)
        done, work = self.run_import(data)
        self.assertEqual(done["state"], "done", done["message"])
        digest = "sha256:" + hashlib.sha256(data).hexdigest()
        self.assertIn(digest, self.fake.blobs)
        create = [c for c in self.fake.calls if c[:2] == ("POST", "/api/create")][0][2]
        self.assertEqual(create["files"], {"new-Q4_K_M.gguf": digest})
        self.assertIn("new:q4", self.fake.models)
        self.assertEqual(list(work.iterdir()), [])  # temporary file gone

    def test_known_blob_is_not_uploaded_again(self):
        import hashlib
        data = os.urandom(70 * 1024)
        self.fake.blobs.add("sha256:" + hashlib.sha256(data).hexdigest())
        done, _ = self.run_import(data)
        self.assertEqual(done["state"], "done")
        self.assertFalse([c for c in self.fake.calls if c[0] == "POST" and "/api/blobs/" in c[1]])

    def test_not_enough_disk(self):
        done, work = self.run_import(os.urandom(1000), free=lambda p: 3000)
        self.assertEqual(done["state"], "failed")
        self.assertIn("not enough disk space", done["message"])
        self.assertEqual(list(work.iterdir()), [])

    def test_cancel_deletes_the_partial_file(self):
        done, work = self.run_import(os.urandom(64 * 1024 * 40), delay=0.05, cancel_after=0.3)
        self.assertEqual(done["state"], "cancelled")
        self.assertEqual(list(work.iterdir()), [])

    def test_unreachable_ollama_fails_before_downloading(self):
        host = FileHost(os.urandom(64 * 1024 * 20), chunk_delay=0.05)
        self.addCleanup(host.close)
        jobs, work = models.Jobs(), self.root / "imports"
        start = time.monotonic()
        v = jobs.start("import", "x", lambda job: models.import_gguf(
            job, Ollama("http://127.0.0.1:1"), host.url_for("r/resolve/main/x.gguf"), "x.gguf",
            "x", work))
        done = wait_done(jobs, v["id"])
        self.assertEqual(done["state"], "failed")
        self.assertIn("not reachable", done["message"])
        # the 1.3 MB file at 20 chunks/s was not fetched (Windows retries a refused connect for ~2 s)
        self.assertLess(time.monotonic() - start, 3.5 if os.name == "nt" else 0.9)

    def test_check_import(self):
        ok = ("https://huggingface.co/a/b-GGUF/resolve/main/b-Q4_K_M.gguf", "b-Q4_K_M.gguf", "b:q4_k_m")
        models.check_import(*ok)
        bad = [("http://huggingface.co/a/b/resolve/main/x.gguf", "x.gguf", "x"),
               ("https://evil.example/a/b/resolve/main/x.gguf", "x.gguf", "x"),
               ("https://huggingface.co/a/b/blob/main/x.gguf", "x.gguf", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "../x.gguf", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.bin", "x.bin", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "y.gguf", "x"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "x.gguf", "has space"),
               ("https://huggingface.co/a/b/resolve/main/x.gguf", "x.gguf", ".hidden")]
        for case in bad:
            with self.assertRaises(StoreError, msg=case) as e:
                models.check_import(*case)
            self.assertEqual(e.exception.code, 400)


class PullBenchTest(OllamaSetup):
    def test_pull_progress_and_error(self):
        jobs = models.Jobs()
        ok = wait_done(jobs, jobs.start("pull", "tiny", lambda j: models.pull(j, self.ollama, "tiny"))["id"])
        self.assertEqual((ok["state"], ok["completed"], ok["total"]), ("done", 5, 10))
        bad = wait_done(jobs, jobs.start("pull", "bad", lambda j: models.pull(j, self.ollama, "bad"))["id"])
        self.assertEqual(bad["state"], "failed")

    def test_benchmark_with_and_without_thinking(self):
        jobs, g = models.Jobs(), (lambda: {"name": "x", "total": 16 * GIB, "used": GIB})
        run = lambda m: wait_done(jobs, jobs.start("benchmark", m, lambda j: models.benchmark(
            j, self.ollama, m, self.tuning, gpu_fn=g))["id"])
        plain = run("coder:14b")["result"]
        self.assertEqual(plain["tokens_per_second"], 20.0)  # 4 tokens in 0.2 s
        self.assertEqual(plain["reply"], "benchmark ok")
        self.assertNotIn("thinking", plain)
        think = run("qwen3.5:9b")["result"]["thinking"]
        self.assertEqual(think["off"]["thinking_tokens"], 0)
        self.assertEqual(think["on"]["tokens"], 40)
        self.assertEqual(think["on"]["thinking_tokens"], 40)  # 300 thinking chars, 3 answer chars → ~40
        level = [c[2] for c in self.fake.calls if c[:2] == ("POST", "/api/chat")]
        run("gpt-oss:20b")
        level = [c[2]["think"] for c in self.fake.calls if c[:2] == ("POST", "/api/chat")][len(level):]
        self.assertEqual(level, [False, False, "low"])

    def test_benchmark_failure_has_the_hint(self):
        self.fake.fail_chat = "model requires more system memory"
        jobs = models.Jobs()
        j = wait_done(jobs, jobs.start("benchmark", "coder:14b", lambda job: models.benchmark(
            job, self.ollama, "coder:14b", self.tuning, gpu_fn=lambda: None))["id"])
        self.assertEqual(j["state"], "failed")
        self.assertIn("more system memory", j["message"])
        self.assertIn("free video memory", j["message"])


class FacadeTest(unittest.TestCase):
    def test_status(self):
        fake = FakeOllama()
        self.addCleanup(fake.close)
        m = models.Models(root=tempfile.mkdtemp(), ollama_url=fake.url)
        s = m.status()
        self.assertEqual((s["reachable"], s["version"], s["own"]), (True, "0.34.2", False))
        down = models.Models(root=tempfile.mkdtemp(), ollama_url="http://127.0.0.1:1").status()
        self.assertFalse(down["reachable"])
        self.assertIn("not reachable", down["error"])

if __name__ == "__main__":
    unittest.main()
