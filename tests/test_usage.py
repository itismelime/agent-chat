import json
import os
import re
import tempfile
import unittest
from pathlib import Path

from bullpen import usage


def line(**usage_):
    return json.dumps({"type": "assistant", "message": {"model": "claude-x", "usage": usage_}}) + "\n"


class UsageTest(unittest.TestCase):
    def setUp(self):
        self.home = Path(tempfile.mkdtemp())
        old, os.environ["HOME"] = os.environ["HOME"], str(self.home)
        self.addCleanup(os.environ.__setitem__, "HOME", old)

    def test_claude_log_read_incrementally(self):
        proj = "/work/proj"
        d = self.home / ".claude" / "projects" / re.sub(r"[^A-Za-z0-9]", "-", proj)
        d.mkdir(parents=True)
        f = d / "s1.jsonl"
        f.write_text('{"name":"mcp__bullpen__chat_join","input":{"name":"alice"}}\n'
                     + line(input_tokens=2, cache_read_input_tokens=1000, cache_creation_input_tokens=500,
                            output_tokens=40))
        self.assertEqual(usage.claude(proj, "alice"), {"ctx": 1502, "out": 40, "model": "claude-x"})
        with open(f, "a") as fh:  # a new turn, and half a line still being written
            fh.write(line(input_tokens=3, cache_read_input_tokens=2000, output_tokens=60) + '{"type": "assis')
        self.assertEqual(usage.claude(proj, "alice"), {"ctx": 2003, "out": 100, "model": "claude-x"})
        self.assertIsNone(usage.claude(proj, "bob"))

    def test_codex_rollout(self):
        d = self.home / ".codex" / "sessions" / "2026" / "09" / "30"
        d.mkdir(parents=True)
        ev = {"type": "event_msg", "payload": {"type": "token_count", "info": {
            "total_token_usage": {"total_tokens": 900000}, "last_token_usage": {"input_tokens": 117000},
            "model_context_window": 258400},
            "rate_limits": {"primary": {"used_percent": 96.0}, "secondary": {"used_percent": 25.0}}}}
        (d / "rollout-2026-09-30T08-00-00-t-1.jsonl").write_text('{"type":"x"}\n' + json.dumps(ev) + "\n")
        self.assertEqual(usage.codex("t-1"), {"ctx": 117000, "window": 258400, "total": 900000,
                                             "limits": {"primary": 96.0, "secondary": 25.0}})
        self.assertIsNone(usage.codex("nope"))

    def test_local_model(self):
        class Ollama:
            def json(self, method, path):
                return {"models": [{"name": "gpt-oss:20b", "context_length": 32768, "size_vram": 14 * 2**30}]}
        self.assertEqual(usage.local(Ollama(), "gpt-oss:20b"),
                         {"model": "gpt-oss:20b", "loaded": True, "ctx": 32768, "vram": 14 * 2**30})
        self.assertEqual(usage.local(Ollama(), "qwen3:0.6b"), {"model": "qwen3:0.6b", "loaded": False})


if __name__ == "__main__":
    unittest.main()
