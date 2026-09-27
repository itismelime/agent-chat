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
