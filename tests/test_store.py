import json
import os
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentchat.store import Store, StoreError, addressed, slug, wakes


class StoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "data")
        self.dir = self.tmp / "OpenVIBES"
        (self.dir / "sub").mkdir(parents=True)
        self.p, _ = self.store.add_project(str(self.dir))
        self.pid = self.p["id"]

    def test_add_project_new_existing_and_child(self):
        self.assertEqual(self.p["id"], "openvibes")
        self.assertEqual(self.p["name"], "OpenVIBES")
        again, existing = self.store.add_project(str(self.dir))
        self.assertTrue(existing)
        child, existing = self.store.add_project(str(self.dir / "sub"))
        self.assertTrue(existing)
        self.assertEqual(child["id"], "openvibes")

    def test_add_project_refuses_relative_and_missing(self):
        for bad in ("relative/path", str(self.tmp / "nope")):
            with self.assertRaises(StoreError) as e:
                self.store.add_project(bad)
            self.assertEqual(e.exception.code, 400)

    def test_same_folder_name_gets_numbered_id(self):
        other = self.tmp / "b" / "OpenVIBES"
        other.mkdir(parents=True)
        p, existing = self.store.add_project(str(other))
        self.assertFalse(existing)
        self.assertEqual(p["id"], "openvibes-2")

    def test_find_through_symlink_and_trailing_slash(self):
        link = self.tmp / "link"
        link.symlink_to(self.dir)
        self.assertEqual(self.store.find(str(link / "sub"))["id"], self.pid)
        self.assertEqual(self.store.find(str(self.dir) + "/")["id"], self.pid)
        self.assertIsNone(self.store.find(str(self.tmp)))
        _, existing = self.store.add_project(str(link))
        self.assertTrue(existing)

    def test_slug(self):
        self.assertEqual(slug("My Project!"), "my-project")
        self.assertEqual(slug("!!!"), "project")

    def test_post_rules(self):
        m = self.store.post(self.pid, "user", "  hi  ")
        self.assertEqual((m["n"], m["from"], m["kind"], m["text"]), (1, "user", "user", "hi"))
        for sender, text, code in (("ghost", "x", 403), ("user", "   ", 400)):
            with self.assertRaises(StoreError) as e:
                self.store.post(self.pid, sender, text)
            self.assertEqual(e.exception.code, code)

    def test_multiline_unicode_roundtrip(self):
        self.store.post(self.pid, "user", "line one\nline två ✓")
        raw = (self.tmp / "data/projects/openvibes/messages.jsonl").read_text()
        self.assertEqual(len(raw.splitlines()), 1)
        self.assertEqual(self.store.messages(self.pid)[0]["text"], "line one\nline två ✓")

    def test_concurrent_posts_get_unique_numbers(self):
        threads = [threading.Thread(target=self.store.post, args=(self.pid, "user", str(i))) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertEqual(sorted(m["n"] for m in self.store.messages(self.pid)), list(range(1, 21)))

    def test_join_rules(self):
        self.store.post(self.pid, "user", "before")
        a = self.store.join(self.pid, " Alice ", "claude")
        self.assertEqual((a["name"], a["cursor"]), ("alice", 1))
        cases = (("alice", "claude", 409), ("user", "claude", 400), ("-x", "claude", 400),
                 ("x" * 33, "claude", 400), ("bob", "robot", 400))
        for name, kind, code in cases:
            with self.assertRaises(StoreError) as e:
                self.store.join(self.pid, name, kind)
            self.assertEqual(e.exception.code, code, name)

    def test_read_advances_cursor(self):
        self.store.join(self.pid, "alice", "claude")
        self.store.post(self.pid, "user", "one")
        self.assertEqual([m["text"] for m in self.store.read(self.pid, "alice")], ["one"])
        self.assertEqual(self.store.read(self.pid, "alice"), [])

    def test_addressed_and_wakes(self):
        self.assertEqual(addressed("@a, @B: hi @c"), ["a", "b"])
        self.assertEqual(addressed("hi @a"), [])
        user_all = {"from": "user", "text": "hi"}
        user_to_b = {"from": "user", "text": "@b hi"}
        agent_all = {"from": "alice", "text": "hi"}
        agent_to_b = {"from": "alice", "text": "@b hi"}
        self.assertTrue(wakes(user_all, "b"))
        self.assertTrue(wakes(user_to_b, "c"))  # user messages wake everyone
        self.assertFalse(wakes(agent_all, "b"))
        self.assertTrue(wakes(agent_to_b, "b"))
        self.assertFalse(wakes(agent_to_b, "alice"))

    def test_wait_returns_soon_after_post(self):
        self.store.join(self.pid, "alice", "claude")
        threading.Timer(0.2, self.store.post, args=(self.pid, "user", "wake up")).start()
        start = time.monotonic()
        got = self.store.wait(self.pid, "alice", 5)
        self.assertLess(time.monotonic() - start, 1.2)
        self.assertEqual((got["notice"], [m["text"] for m in got["messages"]]), (None, ["wake up"]))
        self.assertIsNone(self.store.wait(self.pid, "alice", 0.2))

    def test_wait_skips_messages_that_do_not_wake(self):
        self.store.join(self.pid, "alice", "claude")
        self.store.join(self.pid, "bob", "codex")
        self.store.post(self.pid, "bob", "@carol not you")
        self.assertIsNone(self.store.wait(self.pid, "alice", 0.2))
        self.store.post(self.pid, "bob", "@alice you")
        self.assertEqual([m["text"] for m in self.store.wait(self.pid, "alice", 1)["messages"]],
                         ["@carol not you", "@alice you"])

    def test_wait_for_a_gone_client_keeps_messages(self):
        self.store.join(self.pid, "alice", "claude")
        threading.Timer(0.2, self.store.post, args=(self.pid, "user", "@alice important")).start()
        self.assertIsNone(self.store.wait(self.pid, "alice", 2, alive=lambda: False))
        time.sleep(0.3)
        self.assertEqual([m["text"] for m in self.store.read(self.pid, "alice")], ["@alice important"])

    def test_codex_with_thread_gets_deliveries(self):
        sent = []
        self.store.deliver = lambda pid, name, thread, m: sent.append((name, thread, m["text"]))
        self.store.join(self.pid, "alice", "claude")
        self.store.join(self.pid, "cody", "codex", thread="t-1")
        self.store.join(self.pid, "old", "codex")  # no thread: nothing to deliver to
        self.store.post(self.pid, "user", "hi all")
        self.store.post(self.pid, "alice", "@bob not cody")
        self.store.post(self.pid, "alice", "@cody you")
        self.store.post(self.pid, "cody", "@cody myself")
        self.assertEqual(sent, [("cody", "t-1", "hi all"), ("cody", "t-1", "@cody you")])
        status = {a["name"]: a["status"] for a in self.store.status(self.pid)}
        self.assertEqual(status["cody"], "waiting")
        self.assertEqual(status["old"], "busy")
        self.store.remove(self.pid, "cody")
        self.store.post(self.pid, "user", "after removal")
        self.assertEqual(len(sent), 2)
        with self.assertRaises(StoreError) as e:
            self.store.join(self.pid, "x", "codex", thread="not a thread!")
        self.assertEqual(e.exception.code, 400)

    def test_delivered_moves_the_cursor_forward_only(self):
        self.store.join(self.pid, "cody", "codex", thread="t-1")
        one = self.store.post(self.pid, "user", "one")
        self.store.post(self.pid, "user", "two")
        self.store.delivered(self.pid, "cody", one["n"])
        self.assertEqual([m["text"] for m in self.store.read(self.pid, "cody")], ["two"])
        self.store.delivered(self.pid, "cody", one["n"])
        self.assertEqual(self.store.read(self.pid, "cody"), [])

    def test_spawned_records_and_join_by_token(self):
        self.store.add_spawned(self.pid, "tok1", "claude", "agent-chat-openvibes-tok1")
        self.store.add_spawned(self.pid, "tok2", "claude", "agent-chat-openvibes-tok2")
        self.assertEqual([(s["token"], s["state"]) for s in self.store.spawned_list(self.pid)],
                         [("tok1", "starting"), ("tok2", "starting")])
        a = self.store.join(self.pid, "alice", "claude", spawn="tok2")
        self.assertEqual(a["spawn"], "tok2")
        self.assertEqual(self.store.spawned(self.pid)["tok2"]["name"], "alice")
        b = self.store.join(self.pid, "bob", "claude", spawn="nope")  # unknown token: joins anyway
        self.assertIsNone(b["spawn"])  # no guessing for Claude: a hand-started one must not take tok1
        status = {s["name"]: s["spawn"] for s in self.store.status(self.pid)}
        self.assertEqual(status, {"alice": "tok2", "bob": None})

    def test_a_known_token_always_wins(self):
        self.store.add_spawned(self.pid, "tok1", "claude", "s1")
        self.store.add_spawned(self.pid, "tok2", "claude", "s2")
        self.store.join(self.pid, "alice", "claude", spawn="tok1")
        # the same session joins again under a new name (e.g. its MCP server restarted)
        self.assertEqual(self.store.join(self.pid, "alice2", "claude", spawn="tok1")["spawn"], "tok1")
        self.assertEqual(self.store.spawned(self.pid)["tok1"]["name"], "alice2")
        self.assertIsNone(self.store.spawned(self.pid)["tok2"]["name"])

    def test_join_fallback_needs_exactly_one_recent_start(self):
        self.store.add_spawned(self.pid, "c1", "codex", "s1")
        self.store.add_spawned(self.pid, "c2", "codex", "s2")
        self.assertIsNone(self.store.join(self.pid, "cody", "codex")["spawn"])  # two: ambiguous
        self.store.drop_spawned(self.pid, "c2")
        self.store.update_spawned(self.pid, "c1", started="2020-01-01T00:00:00+00:00")
        self.assertIsNone(self.store.join(self.pid, "cody2", "codex")["spawn"])  # too old
        self.store.add_spawned(self.pid, "c3", "codex", "s3")
        self.assertIsNone(self.store.join(self.pid, "clara", "claude")["spawn"])  # other tool
        self.assertEqual(self.store.join(self.pid, "cody3", "codex")["spawn"], "c3")

    def test_needs_you_overrides_status_until_cleared(self):
        self.store.add_spawned(self.pid, "tok", "codex", "s")
        self.store.join(self.pid, "cody", "codex", thread="t-1", spawn="tok")
        self.store.set_needs(self.pid, "tok", True)
        self.assertEqual(self.store.status(self.pid)[0]["status"], "needs_you")
        self.assertEqual(self.store.spawned_list(self.pid)[0]["state"], "needs_you")
        self.store.set_needs(self.pid, "tok", False)
        self.assertEqual(self.store.status(self.pid)[0]["status"], "waiting")
        self.store.set_needs(self.pid, "tok", True)
        self.store.drop_spawned(self.pid, "tok")
        self.assertEqual(self.store.status(self.pid)[0]["status"], "offline")  # its terminal ended
        self.assertEqual(self.store.spawned(self.pid), {})
        with self.assertRaises(StoreError) as e:
            self.store.update_spawned(self.pid, "tok", name="x")
        self.assertEqual(e.exception.code, 404)

    def test_an_ended_start_leaves_its_agent_offline(self):
        sent = []
        self.store.deliver = lambda pid, name, thread, m: sent.append(name)
        self.store.add_spawned(self.pid, "k", "claude", "s1")
        self.store.add_spawned(self.pid, "l", "codex", "s2")
        self.store.join(self.pid, "kit", "claude", spawn="k")
        self.store.join(self.pid, "lime", "codex", thread="t-1", spawn="l")
        self.store.drop_spawned(self.pid, "k")
        self.store.drop_spawned(self.pid, "l")
        status = {a["name"]: a["status"] for a in self.store.status(self.pid)}
        self.assertEqual(status, {"kit": "offline", "lime": "offline"})
        self.store.post(self.pid, "user", "anyone there?")
        self.assertEqual(sent, [])  # nothing queued into a session that is gone

    def test_local_members(self):
        woken = []
        self.store.talk = lambda pid, name, m: woken.append((name, m["text"]))
        a = self.store.add_local(self.pid, " Qwen ", "qwen3:0.6b", role="You review plans")
        self.assertEqual((a["name"], a["kind"], a["model"], a["role"]),
                         ("qwen", "llm", "qwen3:0.6b", "You review plans"))
        self.store.add_local(self.pid, "tiny", "qwen3:0.6b")
        self.store.join(self.pid, "alice", "claude")
        self.store.post(self.pid, "user", "hello all")          # wakes both
        self.store.post(self.pid, "alice", "@qwen please look")  # wakes qwen only
        self.store.post(self.pid, "qwen", "@qwen talking to myself")  # wakes nobody
        self.store.remove(self.pid, "tiny")
        self.store.post(self.pid, "user", "after removal")      # tiny is removed
        self.assertEqual(sorted(woken), sorted([
            ("qwen", "hello all"), ("tiny", "hello all"), ("qwen", "@qwen please look"),
            ("qwen", "after removal")]))

    def test_local_status_and_role(self):
        self.store.add_local(self.pid, "qwen", "qwen3:0.6b")
        row = lambda: next(a for a in self.store.status(self.pid) if a["name"] == "qwen")
        self.assertEqual((row()["status"], row()["model"], row()["error"]), ("waiting", "qwen3:0.6b", None))
        self.store.set_local(self.pid, "qwen", busy=True)
        self.assertEqual(row()["status"], "busy")
        self.store.set_local(self.pid, "qwen", busy=False, error="model not found")
        self.assertEqual((row()["status"], row()["error"]), ("offline", "model not found"))
        self.store.set_local(self.pid, "qwen", error=None)
        self.assertEqual(row()["status"], "waiting")
        self.store.set_role(self.pid, "qwen", "Be terse")
        self.assertEqual(row()["role"], "Be terse")
        self.store.set_role(self.pid, "qwen", "")
        self.assertIsNone(row()["role"])
        for bad in (lambda: self.store.set_role(self.pid, "qwen", "x" * 501),
                    lambda: self.store.add_local(self.pid, "big", "m", role="x" * 501),
                    lambda: self.store.set_role(self.pid, "nobody", "r")):
            with self.assertRaises(StoreError):
                bad()
        self.store.join(self.pid, "alice", "claude")
        self.assertIsNone(next(a for a in self.store.status(self.pid) if a["name"] == "alice")["model"])

    def test_remove_and_readd(self):
        self.store.join(self.pid, "alice", "claude")
        self.store.remove(self.pid, "alice")
        self.assertEqual(self.store.status(self.pid)[0]["status"], "removed")
        for call in (lambda: self.store.post(self.pid, "alice", "x"),
                     lambda: self.store.read(self.pid, "alice")):
            with self.assertRaises(StoreError) as e:
                call()
            self.assertEqual((e.exception.code, str(e.exception)),
                             (403, "you were removed from this chat"))
        with self.assertRaises(StoreError) as e:
            self.store.join(self.pid, "alice", "codex")  # the name stays reserved
        self.assertEqual(e.exception.code, 409)
        with self.assertRaises(StoreError) as e:
            self.store.remove(self.pid, "alice")
        self.assertEqual(e.exception.code, 400)
        self.assertEqual(self.store.wait(self.pid, "alice", 1), {"notice": "removed", "messages": []})
        self.store.post(self.pid, "user", "while removed")
        self.assertIsNone(self.store.wait(self.pid, "alice", 0.2))
        threading.Timer(0.2, self.store.readd, args=(self.pid, "alice")).start()
        self.assertEqual(self.store.wait(self.pid, "alice", 2), {"notice": "added back", "messages": []})
        self.store.post(self.pid, "user", "welcome back")
        got = self.store.wait(self.pid, "alice", 1)["messages"]
        self.assertEqual([m["text"] for m in got], ["welcome back"])  # not "while removed"
        with self.assertRaises(StoreError) as e:
            self.store.readd(self.pid, "alice")
        self.assertEqual(e.exception.code, 400)

    def test_status(self):
        self.store.join(self.pid, "alice", "claude")
        self.store.join(self.pid, "old", "codex")
        f = self.tmp / "data/projects/openvibes/agents.json"
        agents = json.loads(f.read_text())
        agents["old"]["last_seen"] = "2020-01-01T00:00:00+00:00"
        f.write_text(json.dumps(agents))
        t = threading.Thread(target=self.store.wait, args=(self.pid, "alice", 1))
        t.start()
        time.sleep(0.2)
        status = {a["name"]: a["status"] for a in self.store.status(self.pid)}
        self.assertEqual(status, {"alice": "waiting", "old": "offline"})
        t.join()
        self.assertEqual(self.store.status(self.pid)[0]["status"], "busy")

    def test_corrupt_line_is_skipped(self):
        self.store.post(self.pid, "user", "good")
        with open(self.tmp / "data/projects/openvibes/messages.jsonl", "a") as f:
            f.write("not json\n5\n")
        self.assertEqual([m["text"] for m in self.store.messages(self.pid)], ["good"])
        self.assertEqual(self.store.post(self.pid, "user", "next")["n"], 2)

    def test_no_temp_files_left(self):
        self.store.join(self.pid, "alice", "claude")
        self.assertEqual(list((self.tmp / "data").rglob("*.tmp")), [])

    def test_unknown_project(self):
        with self.assertRaises(StoreError) as e:
            self.store.messages("nope")
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
