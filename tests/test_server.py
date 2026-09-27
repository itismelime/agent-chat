import http.client
import json
import threading
import time
import unittest

from agentchat.client import ApiError, Client, ServiceDown, fmt, label
from tests.helpers import start, stop


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
        body = json.dumps({"path": str(self.dir)}).encode()
        foreign = {"Content-Type": "application/json", "Origin": "http://evil.example"}
        self.assertEqual(self.raw("POST", "/api/projects", body, foreign)[0], 403)
        own = {"Content-Type": "application/json", "Origin": "http://127.0.0.1:%d" % self.port}
        self.assertEqual(self.raw("POST", "/api/projects", body, own)[0], 201)
        self.assertEqual(self.raw("POST", "/api/projects", body, {"Content-Type": "text/plain"})[0], 415)
        big = json.dumps({"path": "x" * 20001}).encode()
        self.assertEqual(self.raw("POST", "/api/projects", big, {"Content-Type": "application/json"})[0], 413)
        self.assertEqual(self.raw("POST", "/api/projects", b"[1]", {"Content-Type": "application/json"})[0], 400)

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
