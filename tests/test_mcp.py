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
        self.assertEqual([kind_of(n) for n in ("claude-code", "codex-mcp-client", "opencode", "x", None)],
                         ["claude", "codex", "opencode", "llm", "llm"])

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

    def codex(self, thread):
        s = Session(Client(self.port), str(self.dir), find_thread=lambda: thread)
        init = rpc(s, "initialize", {"clientInfo": {"name": "codex-mcp-client"}})
        return s, init["result"]["instructions"]

    def test_codex_join_with_thread(self):
        s, instructions = self.codex("01a0e3df-6b97-7233-b194-a7cb90765ce4")
        self.assertIn("delivered into this session", instructions)
        text, err = tool(s, "chat_join", {"name": "cody"})
        self.assertFalse(err, text)
        self.assertIn("delivered into this session", text)
        self.assertNotIn("chat wait", text)
        self.assertNotIn(" wait --as", text)
        self.assertEqual(self.store.agents("proj")["cody"]["thread"], "01a0e3df-6b97-7233-b194-a7cb90765ce4")
        self.assertNotIn("Reminder", tool(s, "chat_post", {"text": "hi"})[0])

    def test_codex_join_without_thread(self):
        s, _ = self.codex(None)
        text, err = tool(s, "chat_join", {"name": "cody"})
        self.assertFalse(err, text)
        self.assertIn("call chat_read", text)
        self.assertIsNone(self.store.agents("proj")["cody"].get("thread"))

    def test_join_sends_the_start_token(self):
        # a session name no real tmux has: linking tries to rename it and must fail harmlessly
        self.store.add_spawned("proj", "tok", "claude", "agent-chat-test-no-such-session")
        s = Session(Client(self.port), str(self.dir), spawn_token="tok")
        rpc(s, "initialize", {"clientInfo": {"name": "claude-code"}})
        text, err = tool(s, "chat_join", {"name": "alice"})
        self.assertFalse(err, text)
        self.assertEqual(self.store.spawned("proj")["tok"]["name"], "alice")

    def test_join_takes_a_start_token_argument(self):
        self.store.add_spawned("proj", "tok", "codex", "agent-chat-test-no-such-session")
        self.store.add_spawned("proj", "other", "codex", "agent-chat-test-no-such-session-2")
        s, _ = self.codex("01a0e3df-6b97-7233-b194-a7cb90765ce4")
        text, err = tool(s, "chat_join", {"name": "cody", "spawn": "tok"})
        self.assertFalse(err, text)
        self.assertEqual(self.store.spawned("proj")["tok"]["name"], "cody")

    def test_a_hand_started_opencode_is_not_typed_to(self):
        s = Session(Client(self.port), str(self.dir))   # no AGENT_CHAT_SPAWN
        init = rpc(s, "initialize", {"clientInfo": {"name": "opencode"}})
        self.assertEqual(s.kind, "llm")
        self.assertIn("wait", tool(s, "chat_join", {"name": "hand"})[0])

    def test_opencode_session(self):
        s = Session(Client(self.port), str(self.dir), spawn_token="tok")
        self.store.add_spawned("proj", "tok", "opencode", "agent-chat-test-no-such-session")
        init = rpc(s, "initialize", {"clientInfo": {"name": "opencode", "version": "1.18.32"}})
        self.assertEqual(s.kind, "opencode")
        self.assertNotIn("chat wait", init["result"]["instructions"])
        self.assertNotIn("spawn", init["result"]["instructions"])
        join = next(t for t in rpc(s, "tools/list")["result"]["tools"] if t["name"] == "chat_join")
        self.assertNotIn("spawn", join["inputSchema"]["properties"])
        text, err = tool(s, "chat_join", {"name": "kit"})
        self.assertFalse(err, text)
        self.assertIn("typed into this session", text)
        self.assertEqual(self.store.agents("proj")["kit"]["kind"], "opencode")
        self.assertNotIn("Reminder", tool(s, "chat_post", {"text": "hi"})[0])
        codex, _ = self.codex(None)
        cjoin = next(t for t in rpc(codex, "tools/list")["result"]["tools"] if t["name"] == "chat_join")
        self.assertIn("spawn", cjoin["inputSchema"]["properties"])

    def test_join_tells_the_personality(self):
        self.store.add_spawned("proj", "tok", "claude", "agent-chat-test-no-such-session", personality="You test")
        s = Session(Client(self.port), str(self.dir), spawn_token="tok")
        rpc(s, "initialize", {"clientInfo": {"name": "claude-code"}})
        text, err = tool(s, "chat_join", {"name": "kit"})
        self.assertFalse(err, text)
        self.assertIn("Your personality: You test", text)

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
