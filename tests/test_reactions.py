import unittest

from bullpen.client import ApiError, Client
from bullpen.mcp import Session, handle
from tests.helpers import start, stop


class ReactionsTest(unittest.TestCase):
    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        (self.tmp / "proj").mkdir()
        self.store.add_project(str(self.tmp / "proj"))
        self.store.join("proj", "alice", "claude")
        self.c = Client(self.port)

    def react(self, n, who, emoji):
        return self.c.call("POST", "/api/projects/proj/messages/%d/react" % n, {"from": who, "emoji": emoji})[1]

    def test_toggle_show_and_wake_nobody(self):
        self.store.post("proj", "alice", "@user heads-up: do not run deploy disable")
        self.store.post("proj", "user", "@alice thanks")
        self.assertEqual(self.react(1, "user", "👍"), {"reactions": {"👍": ["user"]}})
        self.react(2, "alice", "👍")
        self.react(2, "alice", "✅")
        self.react(2, "alice", "👍")  # again: off
        got = self.c.call("GET", "/api/projects/proj/messages")[1]["reactions"]
        self.assertEqual(got, {"1": {"👍": ["user"]}, "2": {"✅": ["alice"]}})  # before the extra ones below
        self.react(1, "user", "🔥")  # any emoji, joined ones too
        self.assertEqual(self.react(1, "user", "👩\u200d💻")["reactions"]["👩\u200d💻"], ["user"])
        self.assertEqual(len(self.store.messages("proj")), 2)  # no message, so nobody is woken
        for n, who, emoji, code in ((9, "user", "👍", 404), (1, "user", "ok", 400), (1, "user", "👍 ", 400), (1, "mallory", "👍", 403)):
            with self.assertRaises(ApiError, msg=(n, who, emoji)) as e:
                self.react(n, who, emoji)
            self.assertEqual(e.exception.code, code)

    def test_agents_react_with_a_tool(self):
        self.store.post("proj", "user", "@alice FYI the build is green")
        s = Session(self.c, str(self.tmp / "proj"))
        handle(s, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "claude-code"}}})
        s.call("chat_join", {"name": "bob"})
        text, err = s.call("chat_react", {"n": 1, "emoji": "👍"})
        self.assertEqual((text, err), ("reacted 👍 to #1", False))
        self.assertTrue(s.call("chat_react", {"n": "1", "emoji": "👍"})[1])
        self.assertEqual(self.c.call("GET", "/api/projects/proj/messages")[1]["reactions"], {"1": {"👍": ["bob"]}})


if __name__ == "__main__":
    unittest.main()
