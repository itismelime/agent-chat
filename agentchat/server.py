"""agent-chat HTTP service: the JSON API and the page, on 127.0.0.1 only.

Requests must name this server in Host and carry no foreign Origin; API
requests must also send the X-Agent-Chat header and JSON bodies, which a
foreign page cannot do without a CORS preflight we never answer. So other
websites can neither post into the agents' sessions nor move their cursors
with a plain GET such as <img src=...>.
"""
import json
import os
import select
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import models as models_mod
from . import spawn
from .codex import Deliverer
from .ollama import OllamaError
from .store import Store, StoreError

PAGE = Path(__file__).with_name("page.html")
MODELS_JS = Path(__file__).with_name("models.js")
TCP_TABLE = "/proc/net/tcp"
MAX_BODY = 20000
WAIT_SECONDS = 300


def peer_uid(client_port, server_port, table=TCP_TABLE):
    """The uid owning the local TCP socket from client_port to server_port:
    None if the table cannot be read (not Linux), -1 if no such socket."""
    want = ("%04X" % client_port, "%04X" % server_port)
    try:
        lines = Path(table).read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        f = line.split()
        if len(f) > 7 and (f[1].rsplit(":", 1)[-1], f[2].rsplit(":", 1)[-1]) == want:
            return int(f[7])
    return -1


def make_handler(store, port, wait_seconds, spawner, owner, models):
    hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body=None, ctype="application/json"):
            if isinstance(body, bytes):
                data = body
            else:
                data = b"" if body is None else json.dumps(body).encode()
            try:
                self.send_response(code)
                if data:
                    self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.send_header("Cache-Control", "no-store")
                self.end_headers()
                self.wfile.write(data)
            except (BrokenPipeError, ConnectionResetError):
                pass  # the client went away, e.g. a killed chat wait

        def body(self):
            if self.headers.get("Content-Type", "").split(";")[0].strip() != "application/json":
                raise StoreError(415, "the body must be application/json")
            length = int(self.headers.get("Content-Length") or 0)
            if not 0 < length <= MAX_BODY:
                raise StoreError(413, "the body must be 1-%d bytes" % MAX_BODY)
            try:
                data = json.loads(self.rfile.read(length))
            except ValueError:
                raise StoreError(400, "bad json") from None
            if not isinstance(data, dict):
                raise StoreError(400, "bad json")
            return data

        def client_alive(self):
            """False once the client closed its connection."""
            try:
                ready, _, _ = select.select([self.connection], [], [], 0)
                return not ready or self.connection.recv(1, socket.MSG_PEEK) != b""
            except OSError:
                return False

        @staticmethod
        def field(data, key):
            value = data.get(key)
            if not isinstance(value, str):
                raise StoreError(400, "missing text field: %s" % key)
            return value

        def dispatch(self, method):
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            # typing into agents' terminals runs code as this user, so other
            # users on this machine may not use the service at all
            uid = peer_uid(self.client_address[1], port)
            if uid is not None and uid != owner:
                return self.send(403, {"error": "only this service's own user may use it"})
            if host not in hosts:
                return self.send(403, {"error": "bad host"})
            if origin is not None and origin != "http://" + host:
                return self.send(403, {"error": "cross-site request refused"})
            url = urlsplit(self.path)
            parts = [p for p in url.path.split("/") if p]
            if parts[:1] == ["api"] and self.headers.get("X-Agent-Chat") != "1":
                return self.send(403, {"error": "missing X-Agent-Chat header"})
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                self.send(*self.route(method, parts, query))
            except StoreError as e:
                self.send(e.code, {"error": str(e)})
            except OllamaError as e:
                self.send(502, {"error": str(e)})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def route(self, method, parts, query):
            if method == "GET" and not parts:
                return 200, PAGE.read_bytes(), "text/html; charset=utf-8"
            if method == "GET" and parts == ["models.js"]:
                return 200, MODELS_JS.read_bytes(), "text/javascript; charset=utf-8"
            if parts[:1] != ["api"]:
                raise StoreError(404, "not found")
            rest = parts[1:]
            if rest == ["projects"] and method == "GET":
                return 200, {"projects": [dict(p, missing=not Path(p["path"]).is_dir())
                                          for p in store.projects()]}
            if rest == ["projects"] and method == "POST":
                project, existing = store.add_project(self.field(self.body(), "path"))
                return (200 if existing else 201), {"project": project, "existing": existing}
            if rest == ["resolve"] and method == "GET":
                path = query.get("path", "")
                if not os.path.isabs(path):
                    raise StoreError(400, "path must be absolute")
                project = store.find(path)
                if project is None:
                    raise StoreError(404, "not in a registered project")
                return 200, {"project": project}
            if rest == ["tools"] and method == "GET":
                return 200, spawn.available()
            if rest[:1] == ["models"]:
                return self.model_route(method, rest[1:], query)
            if len(rest) >= 3 and rest[0] == "projects":
                pid, what = rest[1], rest[2:]
                if what == ["messages"] and method == "GET":
                    try:
                        after = int(query.get("after", "0"))
                    except ValueError:
                        raise StoreError(400, "after must be a number") from None
                    return 200, {"messages": store.messages(pid, after)}
                if what == ["messages"] and method == "POST":
                    data = self.body()
                    m = store.post(pid, self.field(data, "from"), self.field(data, "text"))
                    return 201, {"message": m}
                if what == ["agents"] and method == "GET":
                    return 200, {"agents": store.status(pid)}
                if what == ["agents"] and method == "POST":
                    data = self.body()
                    agent = store.join(pid, self.field(data, "name"), self.field(data, "kind"),
                                       data.get("thread"), data.get("spawn"))
                    if agent["spawn"]:
                        spawner.linked(pid, agent["spawn"], agent["name"])
                    return 201, {"agent": agent, "recent": store.messages(pid)[-20:]}
                if what == ["spawned"] and method == "GET":
                    return 200, {"spawned": store.spawned_list(pid)}
                if what == ["spawned"] and method == "POST":
                    return 201, {"spawned": spawner.start(pid, self.field(self.body(), "tool"))}
                if len(what) == 3 and what[0] == "spawned":
                    token, action = what[1], what[2]
                    if action == "screen" and method == "GET":
                        return 200, {"screen": spawn.screen(spawner.record(pid, token)["session"])}
                    if action == "keys" and method == "POST":
                        data = self.body()
                        session = spawner.record(pid, token)["session"]
                        if "text" in data:
                            spawn.send_text(session, self.field(data, "text"))
                        else:
                            spawn.send_key(session, self.field(data, "key"))
                        return 200, {"ok": True}
                    if action == "stop" and method == "POST":
                        self.body()
                        spawner.stop(pid, token)
                        return 200, {"spawned": store.spawned_list(pid)}
                if len(what) == 3 and what[0] == "agents" and method == "GET":
                    if what[2] == "read":
                        return 200, {"messages": store.read(pid, what[1])}
                    if what[2] == "wait":
                        result = store.wait(pid, what[1], wait_seconds, alive=self.client_alive)
                        return (204, None) if result is None else (200, result)
                if len(what) == 3 and what[0] == "agents" and method == "POST" \
                        and what[2] in ("remove", "readd"):
                    self.body()
                    (store.remove if what[2] == "remove" else store.readd)(pid, what[1])
                    return 200, {"agents": store.status(pid)}
            raise StoreError(404, "not found")

        def model_route(self, method, what, query):
            m = models
            if method == "GET" and what == ["status"]:
                return 200, m.status()
            if method == "GET" and what == []:
                return 200, {"models": models_mod.installed(m.ollama, m.tuning, models_mod.gpu())}
            if method == "GET" and what == ["search"]:
                return 200, {"results": m.hub.search(query.get("q", ""), m.gpu_total())}
            if method == "GET" and what == ["jobs"]:
                return 200, {"jobs": m.jobs.list()}
            if method != "POST":
                raise StoreError(404, "not found")
            data = self.body()
            if len(what) == 3 and what[0] == "jobs" and what[2] == "cancel":
                return 200, {"job": m.jobs.cancel(what[1])}
            if what == ["unload"]:
                return 200, {"unloaded": m.ollama.unload_all()}
            name = data.get("model")
            if what == ["import"]:
                url, filename = data.get("url"), data.get("filename")
                models_mod.check_import(url, filename, name)
                work = m.root / "imports"
                return 202, {"job": m.jobs.start("import", name, lambda job: models_mod.import_gguf(
                    job, m.ollama, url, filename, name, work))}
            models_mod.check_model_name(name)
            if what == ["pull"]:
                return 202, {"job": m.jobs.start("pull", name,
                                                 lambda job: models_mod.pull(job, m.ollama, name))}
            if what == ["benchmark"]:
                return 202, {"job": m.jobs.start("benchmark", name, lambda job: models_mod.benchmark(
                    job, m.ollama, name, m.tuning))}
            if what == ["delete"]:
                m.ollama.delete(name)
                m.tuning.forget(name)
                return 200, {"deleted": name}
            if what == ["tune"]:
                fields = {k: data[k] for k in ("num_ctx", "think") if k in data}
                return 200, {"model": m.tuning.set(name, m.ollama, m.gpu_total(), **fields)}
            raise StoreError(404, "not found")

        def log_message(self, *args):
            pass

    return Handler


def serve(port=None, store=None, wait_seconds=WAIT_SECONDS, deliver=True, owner=None,
          models=None):
    """Bind the service; the caller runs serve_forever(). deliver=False
    leaves Codex sessions and tmux alone (tests)."""
    if port is None:
        port = int(os.environ.get("AGENT_CHAT_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    server.daemon_threads = True
    store = store or Store()
    spawner = spawn.Spawner(store)
    if deliver:
        store.deliver = Deliverer(store)
        spawner.start_poller()
    server.RequestHandlerClass = make_handler(store, server.server_address[1],
                                              wait_seconds, spawner,
                                              os.getuid() if owner is None else owner,
                                              models or models_mod.Models(store.root))
    return server


def main():
    serve().serve_forever()
