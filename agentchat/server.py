"""agent-chat HTTP service: the JSON API and the page, on 127.0.0.1 only.

Requests must name this server in Host, carry no foreign Origin, and send
JSON bodies (which a foreign page cannot do without a CORS preflight we
never answer), so other websites cannot post into the agents' sessions.
"""
import json
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .store import Store, StoreError

PAGE = Path(__file__).with_name("page.html")
MAX_BODY = 20000
WAIT_SECONDS = 300


def make_handler(store, port, wait_seconds):
    hosts = {"127.0.0.1:%d" % port, "localhost:%d" % port}

    class Handler(BaseHTTPRequestHandler):
        def send(self, code, body=None, ctype="application/json"):
            if isinstance(body, bytes):
                data = body
            else:
                data = b"" if body is None else json.dumps(body).encode()
            self.send_response(code)
            if data:
                self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

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

        @staticmethod
        def field(data, key):
            value = data.get(key)
            if not isinstance(value, str):
                raise StoreError(400, "missing text field: %s" % key)
            return value

        def dispatch(self, method):
            host, origin = self.headers.get("Host"), self.headers.get("Origin")
            if host not in hosts:
                return self.send(403, {"error": "bad host"})
            if origin is not None and origin != "http://" + host:
                return self.send(403, {"error": "cross-site request refused"})
            url = urlsplit(self.path)
            parts = [p for p in url.path.split("/") if p]
            query = {k: v[0] for k, v in parse_qs(url.query).items()}
            try:
                self.send(*self.route(method, parts, query))
            except StoreError as e:
                self.send(e.code, {"error": str(e)})

        def do_GET(self):
            self.dispatch("GET")

        def do_POST(self):
            self.dispatch("POST")

        def route(self, method, parts, query):
            if method == "GET" and not parts:
                return 200, PAGE.read_bytes(), "text/html; charset=utf-8"
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
                    agent = store.join(pid, self.field(data, "name"), self.field(data, "kind"))
                    return 201, {"agent": agent, "recent": store.messages(pid)[-20:]}
                if len(what) == 3 and what[0] == "agents" and method == "GET":
                    if what[2] == "read":
                        return 200, {"messages": store.read(pid, what[1])}
                    if what[2] == "wait":
                        result = store.wait(pid, what[1], wait_seconds)
                        return (204, None) if result is None else (200, result)
                if len(what) == 3 and what[0] == "agents" and method == "POST" \
                        and what[2] in ("remove", "readd"):
                    self.body()
                    (store.remove if what[2] == "remove" else store.readd)(pid, what[1])
                    return 200, {"agents": store.status(pid)}
            raise StoreError(404, "not found")

        def log_message(self, *args):
            pass

    return Handler


def serve(port=None, store=None, wait_seconds=WAIT_SECONDS):
    """Bind the service; the caller runs serve_forever()."""
    if port is None:
        port = int(os.environ.get("AGENT_CHAT_PORT", "8765"))
    server = ThreadingHTTPServer(("127.0.0.1", port), BaseHTTPRequestHandler)
    server.daemon_threads = True
    server.RequestHandlerClass = make_handler(store or Store(), server.server_address[1],
                                              wait_seconds)
    return server


def main():
    serve().serve_forever()
