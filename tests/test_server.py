import http.client
import json
import socket
import threading
import time
import unittest

from agentchat.client import ApiError, Client, ServiceDown, fmt, label
from tests.helpers import start, stop
from tests.test_spawn import SCREENS, stub_tools  # noqa: F401

API = {"X-Agent-Chat": "1"}
JSON = dict(API, **{"Content-Type": "application/json"})


class ServerTest(unittest.TestCase):
    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        self.c = Client(self.port)
        self.dir = self.tmp / "proj"
        (self.dir / "sub").mkdir(parents=True)

    def raw(self, method, path, body=b"", headers=None):
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
        conn.request(method, path, body, headers or {})
        try:
            r = conn.getresponse()
            return r.status, r.read()
        finally:
            conn.close()

    def add(self):
        return self.c.call("POST", "/api/projects", {"path": str(self.dir)})[1]["project"]["id"]

    def test_page(self):
        status, body = self.raw("GET", "/")
        self.assertEqual(status, 200)
        self.assertIn(b"agent-chat", body)

    def test_localhost_checks(self):
        self.assertEqual(self.raw("GET", "/", headers={"Host": "evil.example"})[0], 403)
        # a GET from another site (<img src=...>) cannot add a custom header
        self.assertEqual(self.raw("GET", "/api/projects")[0], 403)
        self.assertEqual(self.raw("GET", "/api/projects", headers=API)[0], 200)
        body = json.dumps({"path": str(self.dir)}).encode()
        foreign = dict(API, **{"Content-Type": "application/json", "Origin": "http://evil.example"})
        self.assertEqual(self.raw("POST", "/api/projects", body, foreign)[0], 403)
        own = dict(API, **{"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port})
        self.assertEqual(self.raw("POST", "/api/projects", body, own)[0], 201)
        self.assertEqual(self.raw("POST", "/api/projects", body, dict(API, **{"Content-Type": "text/plain"}))[0], 415)
        big = json.dumps({"path": "x" * 20001}).encode()
        self.assertEqual(self.raw("POST", "/api/projects", big, JSON)[0], 413)
        self.assertEqual(self.raw("POST", "/api/projects", b"[1]", JSON)[0], 400)

    def test_projects_existing_and_missing(self):
        status, body = self.c.call("POST", "/api/projects", {"path": str(self.dir)})
        self.assertEqual((status, body["existing"]), (201, False))
        status, body = self.c.call("POST", "/api/projects", {"path": str(self.dir / "sub")})
        self.assertEqual((status, body["existing"]), (200, True))
        (self.dir / "sub").rmdir()
        self.dir.rmdir()
        projects = self.c.call("GET", "/api/projects")[1]["projects"]
        self.assertEqual([(p["id"], p["missing"]) for p in projects], [("proj", True)])

    def test_resolve(self):
        self.add()
        self.assertEqual(self.c.resolve(self.dir / "sub")["id"], "proj")
        self.assertIsNone(self.c.resolve(self.tmp))
        with self.assertRaises(ApiError) as e:
            self.c.call("GET", "/api/resolve?path=relative")
        self.assertEqual(e.exception.code, 400)

    def test_messages_agents_read(self):
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/messages" % pid, {"from": "user", "text": "first"})
        status, body = self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        self.assertEqual(status, 201)
        self.assertEqual([m["text"] for m in body["recent"]], ["first"])
        with self.assertRaises(ApiError) as e:
            self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "codex"})
        self.assertEqual(e.exception.code, 409)
        self.c.call("POST", "/api/projects/%s/messages" % pid, {"from": "alice", "text": "@user hi"})
        msgs = self.c.call("GET", "/api/projects/%s/messages?after=1" % pid)[1]["messages"]
        self.assertEqual([(m["from"], m["kind"]) for m in msgs], [("alice", "claude")])
        self.assertEqual(self.c.call("GET", self.c.agent_path(pid, "alice", "read"))[1]["messages"][0]["text"], "@user hi")
        agents = self.c.call("GET", "/api/projects/%s/agents" % pid)[1]["agents"]
        self.assertEqual([(a["name"], a["status"]) for a in agents], [("alice", "busy")])
        for path in ("/api/projects/%s/messages?after=x" % pid, "/api/nope", "/api/projects/nope/messages"):
            with self.assertRaises(ApiError):
                self.c.call("GET", path)

    def test_wait(self):
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        path = self.c.agent_path(pid, "alice", "wait")
        self.assertEqual(self.c.call("GET", path, timeout=5), (204, None))
        post = lambda: self.c.call("POST", "/api/projects/%s/messages" % pid, {"from": "user", "text": "go"})
        threading.Timer(0.2, post).start()
        start = time.monotonic()
        status, body = self.c.call("GET", path, timeout=5)
        self.assertLess(time.monotonic() - start, 1.0)
        self.assertEqual((status, body["messages"][0]["text"]), (200, "go"))

    def test_abandoned_wait_keeps_messages(self):
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        sock = socket.create_connection(("127.0.0.1", self.port))
        sock.sendall(("GET %s HTTP/1.1\r\nHost: 127.0.0.1:%d\r\nX-Agent-Chat: 1\r\n\r\n"
                      % (self.c.agent_path(pid, "alice", "wait"), self.port)).encode())
        time.sleep(0.3)
        sock.close()
        self.c.call("POST", "/api/projects/%s/messages" % pid, {"from": "user", "text": "@alice important"})
        time.sleep(1.5)
        read = self.c.call("GET", self.c.agent_path(pid, "alice", "read"))[1]["messages"]
        self.assertEqual([m["text"] for m in read], ["@alice important"])
        agents = self.c.call("GET", "/api/projects/%s/agents" % pid)[1]["agents"]
        self.assertNotEqual(agents[0]["status"], "waiting")

    def test_join_with_codex_thread(self):
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/agents" % pid,
                    {"name": "cody", "kind": "codex", "thread": "01a0e3df-6b97-7233-b194-a7cb90765ce4"})
        self.assertEqual(self.store.agents(pid)["cody"]["thread"], "01a0e3df-6b97-7233-b194-a7cb90765ce4")
        with self.assertRaises(ApiError) as e:
            self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "x", "kind": "codex", "thread": 5})
        self.assertEqual(e.exception.code, 400)

    def test_spawn_routes(self):
        d = stub_tools(self)
        pid = self.add()
        tools = self.c.call("GET", "/api/tools")[1]
        self.assertEqual(tools, {"tmux": True, "claude": True, "codex": True})
        status, body = self.c.call("POST", "/api/projects/%s/spawned" % pid, {"tool": "claude"})
        self.assertEqual(status, 201)
        token = body["spawned"]["token"]
        (d / "screen").write_text("hello\n")
        base = "/api/projects/%s/spawned/%s" % (pid, token)
        self.assertEqual(self.c.call("GET", base + "/screen")[1], {"screen": "hello\n"})
        self.c.call("POST", base + "/keys", {"key": "enter"})
        self.c.call("POST", base + "/keys", {"text": "hi"})
        self.assertIn("send-keys -t =%s: Enter" % body["spawned"]["session"],
                      (d / "calls").read_text())
        joined = self.c.call("POST", "/api/projects/%s/agents" % pid,
                             {"name": "alice", "kind": "claude", "spawn": token})[1]
        self.assertEqual(joined["agent"]["spawn"], token)
        listed = self.c.call("GET", "/api/projects/%s/spawned" % pid)[1]["spawned"]
        self.assertEqual([(s["name"], s["state"], s["session"]) for s in listed],
                         [("alice", "joined", "agent-chat-%s-alice" % pid)])
        self.c.call("POST", base + "/stop", {})
        self.assertEqual(self.c.call("GET", "/api/projects/%s/spawned" % pid)[1]["spawned"], [])
        for method, path, data, code in (
                ("POST", base + "/keys", {"key": "enter"}, 404),
                ("POST", "/api/projects/%s/spawned" % pid, {"tool": "bash"}, 400)):
            with self.assertRaises(ApiError) as e:
                self.c.call(method, path, data)
            self.assertEqual(e.exception.code, code)
        (d / "claude").unlink()
        with self.assertRaises(ApiError) as e:
            self.c.call("POST", "/api/projects/%s/spawned" % pid, {"tool": "claude"})
        self.assertEqual((e.exception.code, str(e.exception)), (503, "claude is not installed"))

    def test_remove_and_readd_routes(self):
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        agents = self.c.call("POST", self.c.agent_path(pid, "alice", "remove"), {})[1]["agents"]
        self.assertEqual(agents[0]["status"], "removed")
        agents = self.c.call("POST", self.c.agent_path(pid, "alice", "readd"), {})[1]["agents"]
        self.assertEqual(agents[0]["status"], "busy")
        with self.assertRaises(ApiError) as e:
            self.c.call("POST", self.c.agent_path(pid, "ghost", "remove"), {})
        self.assertEqual(e.exception.code, 404)

    def test_service_down(self):
        stop(self.server)
        with self.assertRaises(ServiceDown):
            Client(self.port).call("GET", "/api/projects")

    def test_fmt_and_label(self):
        m = {"time": "2026-09-27T18:30:16+02:00", "from": "user", "text": "@bob hi"}
        self.assertEqual(fmt(m), "[18:30:16] user: @bob hi")
        self.assertEqual(label(m, "bob"), "addressed to you: reply")
        self.assertEqual(label(m, "alice"), "for others: read only")
        self.assertEqual(label(dict(m, text="hi"), "alice"), "for everyone: reply")


if __name__ == "__main__":
    unittest.main()
