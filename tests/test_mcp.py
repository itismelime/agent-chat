import unittest

from agentchat.client import Client
from agentchat.mcp import Session, handle, kind_of
from tests.helpers import start, stop


def rpc(session, method, params=None, rid=1):
    return handle(session, {"jsonrpc": "2.0", "id": rid, "method": method, "params": params or {}})


def tool(session, name, args):
    r = rpc(session, "tools/call", {"name": name, "arguments": args})["result"]
    return r["content"][0]["text"], r["isError"]


class McpTest(unittest.TestCase):
    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        self.dir = self.tmp / "proj"
        (self.dir / "sub").mkdir(parents=True)
        self.store.add_project(str(self.dir))
        self.s = Session(Client(self.port), str(self.dir / "sub"))
        init = rpc(self.s, "initialize", {"protocolVersion": "2025-06-18",
                                          "clientInfo": {"name": "claude-code"}})
        self.instructions = init["result"].get("instructions", "")

    def test_kind_of(self):
        self.assertEqual([kind_of(n) for n in ("claude-code", "codex-mcp-client", "x", None)],
                         ["claude", "codex", "llm", "llm"])

    def test_in_project(self):
        self.assertIn("This project (proj) has a shared chat", self.instructions)
        names = [t["name"] for t in rpc(self.s, "tools/list")["result"]["tools"]]
        self.assertEqual(names, ["chat_join", "chat_post", "chat_read"])

    def test_outside_project(self):
        s = Session(Client(self.port), str(self.tmp))
        self.assertNotIn("instructions", rpc(s, "initialize", {})["result"])
        self.assertEqual(rpc(s, "tools/list")["result"]["tools"], [])

    def test_service_down(self):
        s = Session(Client(1), str(self.dir))
        self.assertIn("service is not running", rpc(s, "initialize", {})["result"]["instructions"])

    def test_join_post_read(self):
        self.assertEqual(tool(self.s, "chat_post", {"text": "x"}), ("call chat_join first", True))
        self.store.post("proj", "user", "welcome")
        text, err = tool(self.s, "chat_join", {"name": "Alice"})
        self.assertFalse(err, text)
        self.assertIn("Joined proj as alice", text)
        self.assertIn("wait --as alice --project proj", text)
        self.assertIn("user: welcome", text)
        self.assertEqual(self.store.agents("proj")["alice"]["kind"], "claude")
        text, err = tool(self.s, "chat_join", {"name": "bob"})
        self.assertTrue(err)
        self.assertIn("already joined as alice", text)
        text, err = tool(self.s, "chat_post", {"text": "hello"})
        self.assertFalse(err)
        self.assertIn("posted #2", text)
        self.assertIn("Reminder: your chat wait is not running", text)
        self.store.post("proj", "user", "@alice ok")
        text, _ = tool(self.s, "chat_read", {})
        self.assertIn("user: @alice ok  (addressed to you: reply)", text)
        self.assertNotIn("hello", text)
        self.store.remove("proj", "alice")
        self.assertEqual(tool(self.s, "chat_post", {"text": "x"}),
                         ("you were removed from this chat", True))

    def test_name_taken(self):
        self.store.join("proj", "alice", "codex")
        text, err = tool(self.s, "chat_join", {"name": "alice"})
        self.assertTrue(err)
        self.assertIn("taken", text)
        self.assertIsNone(self.s.name)

    def test_bad_requests_do_not_crash(self):
        r = rpc(self.s, "tools/call", {"name": "chat_join", "arguments": "oops"})["result"]
        self.assertTrue(r["isError"])
        self.assertTrue(tool(self.s, "nope", {})[1])
        self.assertEqual(rpc(self.s, "bogus")["error"]["code"], -32601)
        self.assertIsNone(handle(self.s, {"jsonrpc": "2.0", "method": "notifications/initialized"}))


if __name__ == "__main__":
    unittest.main()
