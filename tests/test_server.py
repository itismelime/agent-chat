import http.client
import json
import os
import socket
import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentchat import models as models_mod
from agentchat.client import ApiError, Client, ServiceDown, fmt, label
from tests.fake_ollama import GIB, FakeOllama
from agentchat.server import peer_uid, serve
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
        # private: an agent's to the user, the user's to one agent; others never see them
        post = lambda body: self.c.call("POST", "/api/projects/%s/messages" % pid, body)[1]["message"]
        self.assertEqual(post({"from": "alice", "text": "psst", "private": True})["dm"], "alice")
        self.assertEqual(post({"from": "user", "text": "ok", "dm": "alice"})["dm"], "alice")
        bob = self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "bob", "kind": "claude"})[1]
        self.assertEqual([m["text"] for m in bob["recent"]], ["first", "@user hi"])
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
        self.assertEqual(tools, {"tmux": True, "claude": True, "codex": True, "opencode": False})
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

    def test_peer_uid(self):
        table = self.tmp / "tcp"
        table.write_text(
            "  sl  local_address rem_address   st tx_queue rx_queue tr tm->when retrnsmt   uid\n"
            "   0: 0100007F:223D 00000000:0000 0A 00000000:00000000 00:00000000 00000000  1000\n"
            "   1: 0100007F:D431 0100007F:223D 01 00000000:00000000 00:00000000 00000000  1001\n")
        self.assertEqual(peer_uid(0xD431, 0x223D, table), 1001)
        self.assertEqual(peer_uid(0xD432, 0x223D, table), -1)
        self.assertIsNone(peer_uid(1, 2, self.tmp / "missing"))

    @unittest.skipIf(os.name == "nt", "no Unix users on Windows")
    def test_other_users_are_refused(self):
        _, server, port, _ = start()
        self.addCleanup(stop, server)
        other = serve(port=0, store=self.store, deliver=False, owner=os.getuid() + 1)
        threading.Thread(target=other.serve_forever, daemon=True).start()
        self.addCleanup(stop, other)
        with self.assertRaises(ApiError) as e:
            Client(other.server_address[1]).call("GET", "/api/projects")
        self.assertEqual((e.exception.code, str(e.exception)),
                         (403, "only this service's own user may use it"))
        self.assertEqual(Client(port).call("GET", "/api/projects")[0], 200)

    def test_remove_also_stops_a_started_agent(self):
        d = stub_tools(self)
        pid = self.add()
        token = self.c.call("POST", "/api/projects/%s/spawned" % pid, {"tool": "claude"})[1]["spawned"]["token"]
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "kit", "kind": "claude", "spawn": token})
        self.c.call("POST", self.c.agent_path(pid, "kit", "remove"), {})
        self.assertEqual(self.c.call("GET", "/api/projects/%s/spawned" % pid)[1]["spawned"], [])
        self.assertIn("kill-session -t =agent-chat-%s-kit" % pid, (d / "calls").read_text())
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        self.c.call("POST", self.c.agent_path(pid, "alice", "remove"), {})  # not started: nothing to stop

    def test_forget_and_personality_routes(self):
        d = stub_tools(self)
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        agents = self.c.call("POST", self.c.agent_path(pid, "alice", "personality"),
                             {"personality": "You review"})[1]["agents"]
        self.assertEqual(agents[0]["personality"], "You review")
        self.c.call("POST", self.c.agent_path(pid, "alice", "remove"), {})
        agents = self.c.call("POST", self.c.agent_path(pid, "alice", "forget"), {})[1]["agents"]
        self.assertEqual(agents, [])
        r = self.c.call("POST", "/api/projects/%s/spawned" % pid,
                        {"tool": "claude", "personality": "You test"})[1]["spawned"]
        self.assertEqual(r["personality"], "You test")

    def test_rename_route_moves_board_cards(self):
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        self.c.call("POST", "/api/projects/%s/board/cards" % pid, {"by": "user", "title": "t", "assignee": "alice"})
        agents = self.c.call("POST", self.c.agent_path(pid, "alice", "rename"), {"name": "tester"})[1]["agents"]
        self.assertEqual([a["name"] for a in agents], ["tester"])
        cards = self.c.call("GET", "/api/projects/%s/board" % pid)[1]["cards"]
        self.assertEqual([c["assignee"] for c in cards], ["tester"])
        # a session still using the old name posts, reads and waits as the new one
        m = self.c.call("POST", "/api/projects/%s/messages" % pid, {"from": "alice", "text": "hi"})[1]["message"]
        self.assertEqual(m["from"], "tester")
        card = self.c.call("POST", "/api/projects/%s/board/cards" % pid,
                           {"by": "alice", "title": "u", "assignee": "alice"})[1]["card"]
        self.assertEqual(card["assignee"], "tester")
        self.c.call("POST", "/api/projects/%s/messages" % pid, {"from": "user", "text": "@tester go"})
        got = self.c.call("GET", self.c.agent_path(pid, "alice", "wait"))[1]
        self.assertEqual((got["name"], got["messages"][-1]["text"]), ("tester", "@tester go"))
        with self.assertRaises(ApiError) as e:
            self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "alice", "kind": "claude"})
        self.assertEqual(e.exception.code, 409)

    def test_board_routes(self):
        pid = self.add()
        base = "/api/projects/%s/board" % pid
        self.assertEqual(self.c.call("GET", base)[1]["cards"], [])
        status, body = self.c.call("POST", base + "/cards", {"by": "user", "title": "Fix login"})
        self.assertEqual((status, body["card"]["id"]), (201, 1))
        card = self.c.call("POST", base + "/cards/1", {"by": "user", "column": "review"})[1]["card"]
        self.assertEqual(card["column"], "review")
        self.c.call("POST", base + "/cards/1", {"by": "user", "assignee": None})
        self.c.call("POST", base + "/cards/1/delete", {"by": "user"})
        self.assertEqual(self.c.call("GET", base)[1]["cards"], [])
        for path, data, code in ((base + "/cards", {"by": "user", "title": ""}, 400),
                                 (base + "/cards/7", {"by": "user", "column": "done"}, 404),
                                 (base + "/cards/x", {"by": "user"}, 404),
                                 (base + "/cards", {"by": "ghost", "title": "t"}, 403),
                                 (base + "/cards", {"by": "user", "title": "t", "column": ["x"]}, 400),
                                 (base + "/cards", {"by": "user", "title": "t", "assignee": {"a": 1}}, 400),
                                 (base + "/cards/%C2%B2", {"by": "user"}, 404)):
            with self.assertRaises(ApiError, msg=path) as e:
                self.c.call("POST", path, data)
            self.assertEqual(e.exception.code, code, path)

    def test_forget_unassigns_the_agents_cards(self):
        pid = self.add()
        self.c.call("POST", "/api/projects/%s/agents" % pid, {"name": "kit", "kind": "claude"})
        base = "/api/projects/%s/board" % pid
        self.c.call("POST", base + "/cards", {"by": "user", "title": "Fix login", "assignee": "kit"})
        self.c.call("POST", base + "/cards", {"by": "user", "title": "Other", "assignee": "user"})
        self.c.call("POST", self.c.agent_path(pid, "kit", "remove"), {})
        self.c.call("POST", self.c.agent_path(pid, "kit", "forget"), {})
        cards = self.c.call("GET", base)[1]["cards"]
        self.assertEqual([c["assignee"] for c in cards], [None, "user"])
        texts = [m["text"] for m in self.store.messages(pid) if m["from"] == "board"]
        self.assertEqual(texts[-1], '#1 "Fix login" is unassigned (kit was forgotten)')

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

    def test_board_notices_need_no_reply(self):
        m = {"time": "2026-09-27T18:30:16+02:00", "from": "board", "text": 'kit moved #1 "x" to Done'}
        self.assertEqual(label(m, "cody"), "board notice: no reply needed")
        self.assertEqual(label(dict(m, text="@cody you were assigned #1"), "cody"), "addressed to you: reply")

    def test_fmt_and_label(self):
        m = {"time": "2026-09-27T18:30:16+02:00", "from": "user", "text": "@bob hi"}
        self.assertEqual(fmt(m), "[18:30:16] user: @bob hi")
        self.assertEqual(label(m, "bob"), "addressed to you: reply")
        self.assertEqual(label(m, "alice"), "for others: read only")
        self.assertEqual(label(dict(m, text="hi"), "alice"), "for everyone: reply")
        self.assertIn("private", label(dict(m, dm="bob"), "bob"))
        m["reply"] = {"n": 3, "from": "bob", "text": "Pick A or B?\nMore"}
        self.assertEqual(fmt(m), '[18:30:16] user (replying to bob #3 "Pick A or B? More"): @bob hi')

    def test_project_files(self):
        pid = self.add()
        (self.dir / "sub" / "notes.md").write_text("# Notes\n")
        (self.dir / "pic.png").write_bytes(b"\x89PNG")
        (self.dir / "blob.bin").write_bytes(b"\xff\xfe\x00")
        (self.dir / "evil.svg").write_text('<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>')
        (self.tmp / "secret.txt").write_text("no")
        (self.dir / "out").symlink_to(self.tmp / "secret.txt")
        get = lambda q: self.raw("GET", "/api/projects/%s/file?%s" % (pid, q), headers=API)
        status, body = get("path=sub/notes.md")
        self.assertEqual((status, json.loads(body)), (200, {"path": "sub/notes.md", "text": "# Notes\n"}))
        self.assertEqual(get("path=" + str(self.dir / "sub/notes.md"))[0], 200)  # absolute inside
        self.assertEqual(get("path=pic.png&raw=1"), (200, b"\x89PNG"))
        for q, code in [("path=../secret.txt", 403), ("path=" + str(self.tmp / "secret.txt"), 403),
                        ("path=out", 403), ("path=nope.md", 404), ("path=sub", 404),
                        ("path=blob.bin", 415), ("path=sub/notes.md&raw=1", 415), ("path=evil.svg&raw=1", 415), ("path=", 404)]:
            self.assertEqual(get(q)[0], code, q)
        # like every API route, a plain GET from another site is refused
        self.assertEqual(self.raw("GET", "/api/projects/%s/file?path=pic.png&raw=1" % pid)[0], 403)


class ModelRoutesTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.fake.add("qwen3.5:9b", size=6 * GIB, capabilities=["completion", "thinking"],
                      arch="qwen35", ctx=262144)
        hub = models_mod.Hub(fetch=lambda url: [] if "/api/models?" in url else {"siblings": []})
        m = models_mod.Models(root=tempfile.mkdtemp(), ollama_url=self.fake.url, hub=hub)
        _, self.server, self.port, _ = start(models=m)
        self.addCleanup(stop, self.server)
        self.c = Client(self.port)

    def test_routes(self):
        c = self.c
        self.assertTrue(c.call("GET", "/api/models/status")[1]["reachable"])
        rows = c.call("GET", "/api/models")[1]["models"]
        self.assertEqual([r["name"] for r in rows], ["qwen3.5:9b"])
        tuned = c.call("POST", "/api/models/tune", {"model": "qwen3.5:9b", "num_ctx": 8192,
                                                     "think": "on"})[1]["model"]
        self.assertEqual((tuned["override"], tuned["think"]), (8192, "on"))
        self.assertEqual(c.call("GET", "/api/models/search?q=x")[1]["results"], [])
        job = c.call("POST", "/api/models/pull", {"model": "tiny"})[1]["job"]
        for _ in range(100):
            jobs = c.call("GET", "/api/models/jobs")[1]["jobs"]
            if jobs[0]["state"] != "running":
                break
            time.sleep(0.05)
        self.assertEqual((jobs[0]["id"], jobs[0]["state"]), (job["id"], "done"))
        self.assertEqual(c.call("POST", "/api/models/unload", {})[1], {"unloaded": []})
        c.call("POST", "/api/models/delete", {"model": "tiny"})
        self.assertNotIn("tiny", self.fake.models)
        for path, body, code in (("/api/models/tune", {"model": "qwen3.5:9b", "num_ctx": 5}, 400),
                                 ("/api/models/import", {"url": "http://x/y.gguf",
                                                         "filename": "y.gguf", "model": "y"}, 400),
                                 ("/api/models/pull", {"model": "has space"}, 400),
                                 ("/api/models/jobs/nope/cancel", {}, 404),
                                 ("/api/models/delete", {"model": "ghost"}, 502)):
            with self.assertRaises(ApiError, msg=path) as e:
                c.call("POST", path, body)
            self.assertEqual(e.exception.code, code, path)

    def test_local_member_routes(self):
        c = self.c
        pdir = Path(tempfile.mkdtemp())
        pid = c.call("POST", "/api/projects", {"path": str(pdir)})[1]["project"]["id"]
        base = "/api/projects/%s" % pid
        status, body = c.call("POST", base + "/locals", {"model": "qwen3.5:9b", "name": "qwen",
                                                         "role": "Be terse"})
        self.assertEqual((status, body["agent"]["model"]), (201, "qwen3.5:9b"))
        for data, code in (({"model": "ghost", "name": "g"}, 400),
                           ({"model": "qwen3.5:9b", "name": "qwen"}, 409)):
            with self.assertRaises(ApiError) as e:
                c.call("POST", base + "/locals", data)
            self.assertEqual(e.exception.code, code)
        agents = c.call("POST", base + "/agents/qwen/role", {"role": "Be kind"})[1]["agents"]
        row = next(a for a in agents if a["name"] == "qwen")
        self.assertEqual((row["role"], row["model"], row["kind"]), ("Be kind", "qwen3.5:9b", "llm"))

    def test_opencode_routes(self):
        d = stub_tools(self)
        (d / "opencode").write_text("#!/bin/sh\nexit 0\n")
        (d / "opencode").chmod(0o755)
        self.fake.add("coder:30b", size=18 * GIB, capabilities=["tools"])
        body = self.c.call("GET", "/api/opencode/models")[1]
        self.assertEqual((body["models"], body["default"]), (["coder:30b"], "coder:30b"))
        pdir = Path(tempfile.mkdtemp())
        pid = self.c.call("POST", "/api/projects", {"path": str(pdir)})[1]["project"]["id"]
        for data, code in (({"tool": "opencode"}, 400), ({"tool": "opencode", "model": "qwen3.5:9b"}, 400)):
            with self.assertRaises(ApiError) as e:
                self.c.call("POST", "/api/projects/%s/spawned" % pid, data)
            self.assertEqual(e.exception.code, code)
        status, r = self.c.call("POST", "/api/projects/%s/spawned" % pid,
                                {"tool": "opencode", "model": "coder:30b"})
        self.assertEqual((status, r["spawned"]["model"]), (201, "coder:30b"))

    def test_page_assets_are_served(self):
        for name, marker, ctype in [("models.js", b"openModels", "text/javascript"),
                                    ("board.js", b"drawBoard", "text/javascript"),
                                    ("page.js", b"function refresh", "text/javascript"),
                                    ("page.css", b"--paper", "text/css"),
                                    ("marked.js", b"marked", "text/javascript"),
                                    ("markdown.js", b"function renderText", "text/javascript"),
                                    ("answers.js", b"function drawAnswers", "text/javascript"),
                                    ("format.js", b"function wrap", "text/javascript"),
                                    ("rules.js", b"function loadRules", "text/javascript")]:
            conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=5)
            conn.request("GET", "/" + name)
            r = conn.getresponse()
            self.assertEqual(r.status, 200, name)
            self.assertTrue(r.getheader("Content-Type").startswith(ctype), name)
            self.assertIn(marker, r.read())
            conn.close()


if __name__ == "__main__":
    unittest.main()
