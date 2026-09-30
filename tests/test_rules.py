import unittest

from bullpen import rules
from bullpen.client import ApiError, Client
from bullpen.mcp import Session, handle
from bullpen.spawn import format_message
from bullpen.store import StoreError
from bullpen.talk import system_prompt
from tests.helpers import start, stop


class RulesTest(unittest.TestCase):
    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        (self.tmp / "proj").mkdir()
        self.store.add_project(str(self.tmp / "proj"))
        self.c = Client(self.port)
        self.base = "/api/projects/proj/rules"

    def notices(self):
        return [m["text"] for m in self.store.messages("proj") if m["from"] == "board"]

    def test_add_edit_delete_and_announce(self):
        a = self.c.call("POST", self.base, {"text": "When you mention a PR, link to it"})[1]["rule"]
        self.c.call("POST", self.base, {"text": "  Keep   replies short "})
        self.c.call("POST", "%s/%d" % (self.base, a["id"]), {"text": "Link every PR you mention"})
        self.c.call("POST", "%s/%d/delete" % (self.base, a["id"]), {})
        self.assertEqual([r["text"] for r in self.c.call("GET", self.base)[1]["rules"]], ["Keep replies short"])
        self.assertEqual(self.notices(), ["Rule 1 added: When you mention a PR, link to it",
                                          "Rule 2 added: Keep replies short",
                                          "Rule 1 changed: Link every PR you mention",
                                          "Rule 1 removed: Link every PR you mention"])
        for path, body, code in ((self.base, {"text": ""}, 400), (self.base, {"text": "x" * 501}, 400),
                                 (self.base + "/99", {"text": "x"}, 404), ("/api/projects/nope/rules", {"text": "x"}, 404)):
            with self.assertRaises(ApiError, msg=path) as e:
                self.c.call("POST", path, body)
            self.assertEqual(e.exception.code, code)

    def test_at_most_fifty(self):
        for i in range(rules.MAX_RULES):
            rules.add(self.store, "proj", "rule %d" % i)
        with self.assertRaises(StoreError):
            rules.add(self.store, "proj", "one too many")

    def test_agents_get_them_everywhere(self):
        self.assertEqual(rules.summary([]), "")
        rules.add(self.store, "proj", "Link every PR")
        rules.add(self.store, "proj", "Keep replies short")
        want = "Project rules (follow them): 1. Link every PR 2. Keep replies short"
        self.assertEqual(rules.summary(rules.get(self.store, "proj")), want)
        # bullpen wait (Claude)
        self.store.join("proj", "alice", "claude")
        self.store.post("proj", "user", "@alice hi")
        self.assertEqual(self.c.call("GET", "/api/projects/proj/agents/alice/wait")[1]["rules"], want)
        # MCP instructions and the join reply
        s = Session(self.c, str(self.tmp / "proj"))
        init = handle(s, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                          "params": {"clientInfo": {"name": "claude-code"}}})
        self.assertIn(want, init["result"]["instructions"])
        text, err = s.call("chat_join", {"name": "bob"})
        self.assertIn(want, text)
        # OpenCode's typed line and a local model's prompt
        self.assertIn(want, format_message({"from": "user", "text": "hi", "n": 1}, "kit", None, want))
        self.assertIn(want, system_prompt("scout", "proj", [], None, want))


    def test_a_wake_carries_them_only_when_new(self):
        rules.add(self.store, "proj", "Link every PR")
        self.store.join("proj", "alice", "claude")
        wait = lambda: self.c.call("GET", "/api/projects/proj/agents/alice/wait")[1]
        self.store.post("proj", "user", "one")
        self.assertIn("Link every PR", wait()["rules"])
        self.store.post("proj", "user", "two")
        self.assertEqual(wait()["rules"], "")  # it was told already
        rules.add(self.store, "proj", "Keep replies short")
        self.store.post("proj", "user", "three")
        self.assertIn("Keep replies short", wait()["rules"])

if __name__ == "__main__":
    unittest.main()
