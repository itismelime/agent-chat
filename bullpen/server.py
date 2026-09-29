"""bullpen HTTP service: the JSON API and the page, on 127.0.0.1 only.

Requests must name this server in Host and carry no foreign Origin; API
requests must also send the X-Bullpen header and JSON bodies, which a
foreign page cannot do without a CORS preflight we never answer. So other
websites can neither post into the agents' sessions nor move their cursors
with a plain GET such as <img src=...>.
"""
import json
import mimetypes
import os
import re
import select
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from . import models as models_mod
from . import mcp, reactions, rules, spawn, talk
from .board import Board
from .client import Client
from .codex import Deliverer
from .ollama import OllamaError
from .store import Store, StoreError, sees

PAGE = Path(__file__).with_name("page.html")
# the logo mark (docs/assets), our own file with no script in it
FAVICON = Path(__file__).resolve().parent.parent / "docs" / "assets" / "mark.svg"
ASSETS = {"page.css": "text/css", "page.js": "text/javascript", "models.js": "text/javascript",
          "board.js": "text/javascript", "marked.js": "text/javascript",
          "markdown.js": "text/javascript", "answers.js": "text/javascript",
          "format.js": "text/javascript", "rules.js": "text/javascript",
          "reactions.js": "text/javascript"}
TCP_TABLE = "/proc/net/tcp"
MAX_BODY = 20000
MAX_FILE = 5_000_000
# raw images for the file viewer; never SVG: the page shows them from blob: URLs of its own origin,
# so a scripted SVG opened in a tab would run with the page's API access
RAW_TYPES = {"image/png", "image/jpeg", "image/gif", "image/webp", "image/avif", "image/bmp"}
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


def project_file(root, name):
    """The file name (relative to root or absolute) if it lies inside the
    project folder root, symlinks resolved, so agents' links cannot open ~/.ssh."""
    root = Path(root).resolve()
    path = (root / Path(name).expanduser()).resolve()
    if root not in path.parents and path != root:
        raise StoreError(403, "only files inside the project folder open here")
    if not path.is_file():
        raise StoreError(404, "no such file: %s" % name)
    if path.stat().st_size > MAX_FILE:
        raise StoreError(413, "the file is over %d MB" % (MAX_FILE // 1_000_000))
    return root, path


def make_handler(store, port, wait_seconds, spawner, owner, models):
    hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}
    board = Board(store)

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
                pass  # the client went away, e.g. a killed bullpen wait

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
            # X-Agent-Chat: relays and waits started before the rename to bullpen
            if parts[:1] == ["api"] and "1" not in (self.headers.get("X-Bullpen"), self.headers.get("X-Agent-Chat")):
                return self.send(403, {"error": "missing X-Bullpen header"})
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
            if method == "GET" and parts == ["favicon.svg"]:
                return 200, FAVICON.read_bytes(), "image/svg+xml"
            if method == "GET" and len(parts) == 1 and parts[0] in ASSETS:
                return 200, PAGE.with_name(parts[0]).read_bytes(), ASSETS[parts[0]] + "; charset=utf-8"
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
            if rest == ["mcp", "version"] and method == "GET":
                return 200, {"version": mcp.VERSION}
            if rest == ["mcp"] and method == "POST":  # an agent session's relay
                return 200, mcp.serve_request(Client(port), self.body())
            if rest == ["opencode", "models"] and method == "GET":
                from . import opencode
                names = opencode.tool_models(models.ollama, models.gpu_total())
                return 200, {"models": names, "default": names[0] if names else None}
            if rest[:1] == ["models"]:
                return self.model_route(method, rest[1:], query)
            if len(rest) >= 3 and rest[0] == "projects":
                pid, what = rest[1], rest[2:]
                if what == ["messages"] and method == "GET":
                    try:
                        after = int(query.get("after", "0"))
                    except ValueError:
                        raise StoreError(400, "after must be a number") from None
                    return 200, {"messages": store.messages(pid, after),
                                 "reactions": reactions.get(store, pid)}
                if len(what) == 3 and what[0] == "messages" and what[2] == "react" and method == "POST":
                    if not re.fullmatch(r"[0-9]+", what[1]):
                        raise StoreError(404, "no message %s" % what[1])
                    data = self.body()
                    return 200, {"reactions": reactions.toggle(
                        store, pid, int(what[1]), store.resolve(pid, self.field(data, "from")),
                        data.get("emoji"))}
                if what == ["messages"] and method == "POST":
                    data = self.body()
                    sender = store.resolve(pid, self.field(data, "from"))
                    dm = data.get("dm") if sender == "user" else sender if data.get("private") is True else None
                    m = store.post(pid, sender, self.field(data, "text"), reply=data.get("reply"), dm=dm,
                                   ask=data.get("ask") is True)
                    return 201, {"message": m}
                if what == ["agents"] and method == "GET":
                    return 200, {"agents": store.status(pid), "renames": store.renames(pid)}
                if what == ["agents"] and method == "POST":
                    data = self.body()
                    agent = store.join(pid, self.field(data, "name"), self.field(data, "kind"),
                                       data.get("thread"), data.get("spawn"))
                    if agent["spawn"]:
                        spawner.linked(pid, agent["spawn"], agent["name"])
                    agent["personality"] = store.agents(pid)[agent["name"]].get("role")
                    recent = [m for m in store.messages(pid) if sees(m, agent["name"])][-20:]
                    return 201, {"agent": agent, "recent": recent}
                if what == ["locals"] and method == "POST":
                    data = self.body()
                    model = self.field(data, "model")
                    if model not in [m["name"] for m in models.ollama.tags()]:
                        raise StoreError(400, "%s is not installed in the Ollama in use" % model)
                    agent = store.add_local(pid, self.field(data, "name"), model, data.get("role"))
                    return 201, {"agent": agent}
                if what == ["file"] and method == "GET":
                    root, path = project_file(store.project(pid)["path"], query.get("path", ""))
                    if query.get("raw"):  # images a Markdown file shows
                        ctype = mimetypes.guess_type(path.name)[0] or ""
                        if ctype not in RAW_TYPES:
                            raise StoreError(415, "only PNG, JPEG, GIF, WebP, AVIF and BMP images are sent raw")
                        return 200, path.read_bytes(), ctype
                    try:
                        text = path.read_text(encoding="utf-8")
                    except UnicodeDecodeError:
                        raise StoreError(415, "not a text file") from None
                    return 200, {"path": path.relative_to(root).as_posix(), "text": text}
                if what[:1] == ["board"]:
                    return self.board_route(method, pid, what[1:])
                if what[:1] == ["rules"]:  # changed from the page only; agents read them
                    if what == ["rules"] and method == "GET":
                        return 200, {"rules": rules.get(store, pid)}
                    if method == "POST" and what == ["rules"]:
                        return 201, {"rule": rules.add(store, pid, self.body().get("text"))}
                    if method == "POST" and len(what) >= 2 and re.fullmatch(r"[0-9]+", what[1]):
                        if what[2:] == ["delete"]:
                            rules.delete(store, pid, int(what[1]))
                            return 200, {"rules": rules.get(store, pid)}
                        if len(what) == 2:
                            return 200, {"rule": rules.edit(store, pid, int(what[1]), self.body().get("text"))}
                    raise StoreError(404, "not found")
                if what == ["spawned"] and method == "GET":
                    return 200, {"spawned": store.spawned_list(pid)}
                if what == ["spawned"] and method == "POST":
                    data = self.body()
                    return 201, {"spawned": spawner.start(pid, self.field(data, "tool"),
                                                          data.get("model"),
                                                          data.get("personality"))}
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
                if len(what) == 3 and what[0] == "agents":  # an old name still works after a rename
                    what = [what[0], store.resolve(pid, what[1]), what[2]]
                if len(what) == 3 and what[0] == "agents" and method == "GET":
                    if what[2] == "read":
                        return 200, {"messages": store.read(pid, what[1])}
                    if what[2] == "wait":
                        result = store.wait(pid, what[1], wait_seconds, alive=self.client_alive)
                        if result is None:
                            return 204, None
                        role = store.agents(pid).get(what[1], {}).get("role")
                        return 200, dict(result, personality=role, name=what[1],
                                         rules=rules.summary(rules.get(store, pid)))
                if len(what) == 3 and what[0] == "agents" and method == "POST" and what[2] == "resume":
                    return 201, {"spawned": spawner.resume(pid, what[1])}
                if len(what) == 3 and what[0] == "agents" and method == "POST" \
                        and what[2] in ("remove", "readd", "role", "personality", "forget", "rename"):
                    data = self.body()
                    if what[2] in ("role", "personality"):
                        store.set_personality(pid, what[1], data.get(what[2]))
                    elif what[2] == "rename":
                        board.rename(pid, what[1], store.rename(pid, what[1], data.get("name")))
                    elif what[2] == "forget":
                        store.forget(pid, what[1])
                        board.unassign(pid, what[1])
                    elif what[2] == "remove":
                        store.remove(pid, what[1])
                        # an agent started from the page leaves with its tmux session
                        token = next((a["spawn"] for a in store.status(pid)
                                      if a["name"] == what[1] and a["spawn"]), None)
                        if token:
                            spawner.stop(pid, token)
                    else:
                        store.readd(pid, what[1])
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

        def board_route(self, method, pid, what):
            if method == "GET" and not what:
                return 200, board.get(pid)
            if method != "POST" or what[:1] != ["cards"]:
                raise StoreError(404, "not found")
            data = self.body()
            by = store.resolve(pid, self.field(data, "by"))  # an agent's old name after a rename
            if isinstance(data.get("assignee"), str):
                data["assignee"] = store.resolve(pid, data["assignee"])
            if len(what) == 1:
                card = board.add(pid, by, data.get("title"), data.get("description", ""),
                                 data.get("column") or "todo", data.get("assignee"),
                                 data.get("kind"), data.get("epic"))
                return 201, {"card": card}
            if not re.fullmatch(r"[0-9]+", what[1]):
                raise StoreError(404, "no card %s" % what[1])
            n = int(what[1])
            if what[2:] == ["delete"]:
                board.delete(pid, n, by)
                return 200, {}
            if len(what) == 2:
                fields = {k: data[k] for k in ("title", "description", "column", "assignee", "epic")
                          if k in data}
                return 200, {"card": board.update(pid, n, by, **fields)}
            raise StoreError(404, "not found")

        def log_message(self, *args):
            pass

    return Handler


def announce_update(store):
    """Once per new mcp.NEWS: tell every chat what agents can do now (a fresh
    install has no chats yet, so it posts nothing)."""
    f = store.root / "news-seen"
    news = mcp.hashlib.sha1(mcp.NEWS.encode()).hexdigest()[:12]
    try:
        seen = f.read_text().strip()
    except OSError:
        seen = None
    if seen != news:
        for p in store.projects():
            store.notice(p["id"], mcp.NEWS)
        f.write_text(news)


def serve(port=None, store=None, wait_seconds=WAIT_SECONDS, deliver=True, owner=None,
          models=None):
    """Bind the service; the caller runs serve_forever(). deliver=False
    leaves Codex sessions and tmux alone (tests)."""
    if port is None:
        port = int(os.environ.get("BULLPEN_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    server.daemon_threads = True
    store = store or Store()
    models = models or models_mod.Models(store.root)
    spawner = spawn.Spawner(store, models, server.server_address[1])
    if deliver:
        announce_update(store)
        store.deliver = Deliverer(store)
        spawner.start_poller()
        talker = talk.Talker(store, models)
        store.talk = talker
        talker.start()
    server.RequestHandlerClass = make_handler(store, server.server_address[1],
                                              wait_seconds, spawner,
                                              # no uids on Windows: peer_uid finds no table there either
                                              getattr(os, "getuid", lambda: None)() if owner is None
                                              else owner, models)
    return server


def main():
    serve().serve_forever()
