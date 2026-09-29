import unittest

from bullpen.client import Client
from bullpen import mcp
from bullpen.client import ApiError
from bullpen.mcp import Relay, Session, handle, kind_of
from bullpen.server import announce_update
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
        self.assertEqual(names, ["chat_join", "chat_post", "chat_rename", "chat_react", "chat_read",
                                 "board_list", "board_add", "board_update"])

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
        self.assertIn("Reminder: your bullpen wait is not running", text)
        self.assertIn("posted #3", tool(self.s, "chat_post", {"text": "@user psst", "private": True})[0])
        self.assertEqual(self.store.messages("proj")[-1]["dm"], "alice")
        self.store.post("proj", "user", "@alice ok")
        text, _ = tool(self.s, "chat_read", {})
        self.assertIn("user: @alice ok  (addressed to you: reply)", text)
        self.assertNotIn("hello", text)
        text, err = tool(self.s, "chat_rename", {"name": "tester"})
        self.assertFalse(err, text)
        self.assertIn("You are now tester", text)
        self.assertIn("wait --as tester --project proj", text)
        self.assertIn("posted #", tool(self.s, "chat_post", {"text": "as tester"})[0])
        self.assertEqual(self.store.messages("proj")[-1]["from"], "tester")
        self.assertTrue(tool(self.s, "chat_rename", {"name": "user"})[1])
        self.store.remove("proj", "tester")
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
        self.assertNotIn("bullpen wait", text)
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
        self.store.add_spawned("proj", "tok", "claude", "bullpen-test-no-such-session")
        s = Session(Client(self.port), str(self.dir), spawn_token="tok")
        rpc(s, "initialize", {"clientInfo": {"name": "claude-code"}})
        text, err = tool(s, "chat_join", {"name": "alice"})
        self.assertFalse(err, text)
        self.assertEqual(self.store.spawned("proj")["tok"]["name"], "alice")

    def test_join_takes_a_start_token_argument(self):
        self.store.add_spawned("proj", "tok", "codex", "bullpen-test-no-such-session")
        self.store.add_spawned("proj", "other", "codex", "bullpen-test-no-such-session-2")
        s, _ = self.codex("01a0e3df-6b97-7233-b194-a7cb90765ce4")
        text, err = tool(s, "chat_join", {"name": "cody", "spawn": "tok"})
        self.assertFalse(err, text)
        self.assertEqual(self.store.spawned("proj")["tok"]["name"], "cody")

    def test_a_hand_started_opencode_is_not_typed_to(self):
        s = Session(Client(self.port), str(self.dir))   # no BULLPEN_SPAWN
        init = rpc(s, "initialize", {"clientInfo": {"name": "opencode"}})
        self.assertEqual(s.kind, "llm")
        self.assertIn("wait", tool(s, "chat_join", {"name": "hand"})[0])

    def test_opencode_session(self):
        s = Session(Client(self.port), str(self.dir), spawn_token="tok")
        self.store.add_spawned("proj", "tok", "opencode", "bullpen-test-no-such-session")
        init = rpc(s, "initialize", {"clientInfo": {"name": "opencode", "version": "1.18.32"}})
        self.assertEqual(s.kind, "opencode")
        self.assertNotIn("bullpen wait", init["result"]["instructions"])
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
        self.store.add_spawned("proj", "tok", "claude", "bullpen-test-no-such-session", personality="You test")
        s = Session(Client(self.port), str(self.dir), spawn_token="tok")
        rpc(s, "initialize", {"clientInfo": {"name": "claude-code"}})
        text, err = tool(s, "chat_join", {"name": "kit"})
        self.assertFalse(err, text)
        self.assertIn("Your personality: You test", text)

    def test_board_tools(self):
        names = [t["name"] for t in rpc(self.s, "tools/list")["result"]["tools"]]
        self.assertEqual(names[-3:], ["board_list", "board_add", "board_update"])
        self.assertTrue(tool(self.s, "board_list", {})[1])  # join first
        tool(self.s, "chat_join", {"name": "alice"})
        text, err = tool(self.s, "board_add", {"title": "Fix login", "assignee": "alice"})
        self.assertFalse(err, text)
        self.assertIn("#1", text)
        text, err = tool(self.s, "board_update", {"id": 1, "column": "review"})
        self.assertFalse(err, text)
        text, _ = tool(self.s, "board_list", {})
        self.assertIn("Review", text)
        self.assertIn('#1 "Fix login" (alice)', text)
        tool(self.s, "board_update", {"id": 1, "description": "Use the new token API"})
        self.assertIn("Use the new token API", tool(self.s, "board_list", {})[0])
        text, err = tool(self.s, "board_add", {"title": "Login rework", "kind": "epic"})
        self.assertIn('added epic #2 "Login rework"', text)
        tool(self.s, "board_update", {"id": 1, "epic": 2, "column": "done"})
        text = tool(self.s, "board_list", {})[0]
        self.assertIn('epic #2 "Login rework" (To do, 1/1 items done)', text)
        self.assertIn('#1 "Fix login" (alice) [epic #2]', text)
        tool(self.s, "board_update", {"id": 1, "epic": 0})
        self.assertNotIn("[epic #2]", tool(self.s, "board_list", {})[0])
        self.assertTrue(tool(self.s, "board_update", {"id": 1, "column": "later"})[1])
        self.assertTrue(tool(self.s, "board_update", {"id": "x"})[1])

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


class RelayTest(unittest.TestCase):
    """The agent's MCP process relays to the service, which runs the Session."""

    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        self.dir = self.tmp / "proj"
        self.dir.mkdir()
        self.store.add_project(str(self.dir))
        self.r = Relay(Client(self.port), str(self.dir))
        self.init = rpc(self.r, "initialize", {"clientInfo": {"name": "claude-code"}})["result"]

    def test_everything_comes_from_the_service(self):
        self.assertEqual(self.init["capabilities"], {"tools": {"listChanged": True}})
        self.assertIn("This project (proj) has a shared chat", self.init["instructions"])
        names = [t["name"] for t in rpc(self.r, "tools/list")["result"]["tools"]]
        self.assertEqual(names[:2], ["chat_join", "chat_post"])
        self.assertEqual(tool(self.r, "chat_post", {"text": "x"}), ("call chat_join first", True))
        text, err = tool(self.r, "chat_join", {"name": "alice"})
        self.assertFalse(err, text)
        self.assertEqual(self.r.name, "alice")  # kept here, sent with every call
        self.assertFalse(tool(self.r, "chat_post", {"text": "hi"})[1])
        self.assertEqual(self.store.messages("proj")[-1]["from"], "alice")
        self.assertIn("added epic #1", tool(self.r, "board_add", {"title": "E", "kind": "epic"})[0])

    def test_announces_new_tools_once(self):
        self.assertFalse(self.r.changed())  # nothing listed yet
        rpc(self.r, "tools/list")
        self.assertFalse(self.r.changed())
        old, mcp.VERSION = mcp.VERSION, "newer"
        self.addCleanup(setattr, mcp, "VERSION", old)
        self.assertTrue(self.r.changed())
        self.assertFalse(self.r.changed())  # until the agent lists them again
        rpc(self.r, "tools/list")
        self.assertEqual(self.r.version, "newer")

    def test_service_down(self):
        r = Relay(Client(1), str(self.dir))
        self.assertEqual(r.instructions(), mcp.DOWN)
        self.assertEqual(r.tools(), [])
        self.assertEqual(r.version, "")  # so the tools are announced once it runs
        self.assertTrue(tool(r, "chat_join", {"name": "a"})[1])

    def test_codex_thread_is_found_by_the_relay(self):
        r = Relay(Client(self.port), str(self.dir), find_thread=lambda: "t-42")
        rpc(r, "initialize", {"clientInfo": {"name": "codex-mcp-client"}})
        self.assertFalse(tool(r, "chat_join", {"name": "cody"})[1])
        self.assertEqual(self.store.agents("proj")["cody"]["thread"], "t-42")

    def test_bad_requests(self):
        c = Client(self.port)
        for body in ({"op": "init"}, {"op": "x", "cwd": str(self.dir)}):
            with self.assertRaises(ApiError) as e:
                c.call("POST", "/api/mcp", body)
            self.assertEqual(e.exception.code, 400)

    def test_update_note_once_per_news(self):
        announce_update(self.store)
        announce_update(self.store)
        notes = [m["text"] for m in self.store.messages("proj") if m["from"] == "board"]
        self.assertEqual(notes, [mcp.NEWS])
        old, mcp.NEWS = mcp.NEWS, "bullpen was updated: something else."
        self.addCleanup(setattr, mcp, "NEWS", old)
        announce_update(self.store)
        self.assertEqual(len([m for m in self.store.messages("proj") if m["from"] == "board"]), 2)
