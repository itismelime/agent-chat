"""A fake Ollama and a fake file host for tests (local http.server)."""
import json
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

GIB = 1024 ** 3


def _serve(handler):
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


class FakeOllama:
    def __init__(self):
        self.models, self.loaded, self.blobs, self.calls = {}, set(), set(), []
        self.fail_chat = None
        self.reply, self.chat_delay = None, 0
        self.server = _serve(self._handler())
        self.url = "http://127.0.0.1:%d" % self.server.server_address[1]

    def close(self):
        self.server.shutdown()
        self.server.server_close()

    def add(self, name, size=4 * GIB, capabilities=("completion",), arch="llama", ctx=131072):
        self.models[name] = {"size": size, "capabilities": list(capabilities),
                             "arch": arch, "ctx": ctx}

    def _handler(fake):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def reply(self, code, body=None, lines=None):
                if lines is not None:
                    data = b"".join(json.dumps(x).encode() + b"\n" for x in lines)
                else:
                    data = b"" if body is None else json.dumps(body).encode()
                self.send_response(code)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def body(self):
                n = int(self.headers.get("Content-Length") or 0)
                return self.rfile.read(n) if n else b""

            def do_HEAD(self):
                fake.calls.append(("HEAD", self.path))
                self.send_response(200 if self.path.rsplit("/", 1)[-1] in fake.blobs else 404)
                self.send_header("Content-Length", "0")
                self.end_headers()

            def do_GET(self):
                fake.calls.append(("GET", self.path))
                if self.path == "/api/version":
                    return self.reply(200, {"version": "0.34.2"})
                if self.path == "/api/tags":
                    return self.reply(200, {"models": [{"name": n, "size": m["size"]}
                                                       for n, m in fake.models.items()]})
                if self.path == "/api/ps":
                    return self.reply(200, {"models": [{"name": n} for n in sorted(fake.loaded)]})
                self.reply(404, {"error": "not found"})

            def do_DELETE(self):
                data = json.loads(self.body() or b"{}")
                fake.calls.append(("DELETE", self.path, data))
                if fake.models.pop(data.get("model"), None) is None:
                    return self.reply(404, {"error": "model not found"})
                self.reply(200)

            def do_POST(self):
                raw = self.body()
                if self.path.startswith("/api/blobs/"):
                    fake.calls.append(("POST", self.path, len(raw)))
                    fake.blobs.add(self.path.rsplit("/", 1)[-1])
                    return self.reply(201)
                data = json.loads(raw or b"{}")
                fake.calls.append(("POST", self.path, data))
                name = data.get("model")
                if self.path == "/api/show":
                    m = fake.models.get(name)
                    if not m:
                        return self.reply(404, {"error": "model '%s' not found" % name})
                    info = {"general.architecture": m["arch"],
                            "%s.context_length" % m["arch"]: m["ctx"]}
                    return self.reply(200, {"capabilities": m["capabilities"], "model_info": info})
                if self.path == "/api/generate":
                    if data.get("keep_alive") == 0:
                        fake.loaded.discard(name)
                    return self.reply(200, {"done": True})
                if self.path == "/api/chat":
                    if fake.fail_chat:
                        return self.reply(500, {"error": fake.fail_chat})
                    fake.loaded.add(name)
                    think = data.get("think")
                    time.sleep(fake.chat_delay)
                    content = fake.reply if fake.reply is not None else (
                        "391" if "17" in data["messages"][-1]["content"] else "benchmark ok")
                    message = {"role": "assistant", "content": content}
                    if think:
                        message["thinking"] = "x" * 300
                    return self.reply(200, {"message": message, "eval_count": 40 if think else 4,
                                            "eval_duration": 2 * 10 ** 8})
                if self.path == "/api/create":
                    fake.add(name)
                    return self.reply(200, lines=[{"status": "creating"}, {"status": "success"}])
                if self.path == "/api/pull":
                    if name == "bad":
                        return self.reply(200, lines=[{"error": "file does not exist"}])
                    fake.add(name)
                    return self.reply(200, lines=[{"status": "pulling", "completed": 5, "total": 10},
                                                  {"status": "success"}])
                self.reply(404, {"error": "not found"})
        return Handler


class FileHost:
    """Serves `data` at any path, in 64 KiB chunks, chunk_delay seconds apart."""

    def __init__(self, data, chunk_delay=0):
        host = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_GET(self):
                self.send_response(200)
                self.send_header("Content-Length", str(len(host.data)))
                self.end_headers()
                for i in range(0, len(host.data), 65536):
                    try:
                        self.wfile.write(host.data[i:i + 65536])
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    time.sleep(host.chunk_delay)

        self.data, self.chunk_delay = data, chunk_delay
        self.server = _serve(Handler)

    def url_for(self, path):
        return "http://127.0.0.1:%d/%s" % (self.server.server_address[1], path.lstrip("/"))

    def close(self):
        self.server.shutdown()
        self.server.server_close()
