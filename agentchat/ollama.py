"""A small client for Ollama's native API (urllib, no dependencies)."""
import json
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class OllamaError(Exception):
    pass


class Ollama:
    def __init__(self, url):
        self.url = url.rstrip("/")

    def request(self, method, path, body=None, timeout=30, raw=None, headers=None):
        data = raw if raw is not None else (None if body is None else json.dumps(body).encode())
        h = dict(headers or {})
        if body is not None:
            h["Content-Type"] = "application/json"
        try:
            return urlopen(Request(self.url + path, data, h, method=method), timeout=timeout)
        except HTTPError as e:
            text = e.read().decode(errors="replace")
            e.close()
            try:
                text = json.loads(text).get("error", text)
            except (ValueError, AttributeError):
                pass
            raise OllamaError(text or str(e)) from None
        except (URLError, OSError) as e:
            raise OllamaError("Ollama at %s is not reachable (%s)"
                              % (self.url, getattr(e, "reason", e))) from None

    def json(self, method, path, body=None, timeout=30):
        with self.request(method, path, body, timeout) as r:
            raw = r.read()
        return json.loads(raw) if raw else {}

    def stream(self, path, body, timeout=3600):
        """The NDJSON objects of a streaming call such as /api/pull."""
        with self.request("POST", path, body, timeout) as r:
            for line in r:
                if line.strip():
                    obj = json.loads(line)
                    if "error" in obj:
                        raise OllamaError(obj["error"])
                    yield obj

    def version(self):
        return self.json("GET", "/api/version").get("version")

    def tags(self):
        return self.json("GET", "/api/tags").get("models", [])

    def loaded(self):
        return [m["name"] for m in self.json("GET", "/api/ps").get("models", [])]

    def show(self, model):
        return self.json("POST", "/api/show", {"model": model})

    def delete(self, model):
        self.json("DELETE", "/api/delete", {"model": model})

    def unload_all(self):
        names = self.loaded()
        for name in names:
            self.json("POST", "/api/generate", {"model": name, "keep_alive": 0}, timeout=120)
        return names

    def has_blob(self, digest):
        try:
            with self.request("HEAD", "/api/blobs/" + digest):
                return True
        except OllamaError:
            return False

    def push_blob(self, digest, path, size):
        with open(path, "rb") as f:
            with self.request("POST", "/api/blobs/" + digest, raw=f, timeout=3600,
                              headers={"Content-Type": "application/octet-stream",
                                       "Content-Length": str(size)}):
                pass

    def chat(self, model, messages, num_ctx=None, think=False, timeout=600):
        body = {"model": model, "messages": messages, "stream": False, "think": think}
        if num_ctx:
            body["options"] = {"num_ctx": num_ctx}
        return self.json("POST", "/api/chat", body, timeout)
