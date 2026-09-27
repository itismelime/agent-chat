"""Talks to the agent-chat service, for the CLI and the MCP server."""
import json
import os
from urllib.error import HTTPError
from urllib.parse import quote
from urllib.request import Request, urlopen

from .store import addressed

DOWN = "agent-chat service not running (systemctl --user start agent-chat)"


class ServiceDown(Exception):
    pass


class ApiError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


class Client:
    def __init__(self, port=None):
        port = port or os.environ.get("AGENT_CHAT_PORT", "8765")
        self.base = "http://127.0.0.1:%s" % port

    def call(self, method, path, body=None, timeout=10):
        data = None if body is None else json.dumps(body).encode()
        headers = {"Content-Type": "application/json"} if data else {}
        try:
            with urlopen(Request(self.base + path, data, headers, method=method),
                         timeout=timeout) as r:
                raw = r.read()
                return r.status, (json.loads(raw) if raw else None)
        except HTTPError as e:
            try:
                message = json.loads(e.read())["error"]
            except (ValueError, KeyError, TypeError):
                message = str(e.reason)
            finally:
                e.close()
            raise ApiError(e.code, message) from None
        except OSError:
            raise ServiceDown(DOWN) from None

    def resolve(self, path):
        """The project containing path, or None."""
        try:
            return self.call("GET", "/api/resolve?path=" + quote(str(path)))[1]["project"]
        except ApiError as e:
            if e.code == 404:
                return None
            raise

    @staticmethod
    def agent_path(pid, name, action):
        return "/api/projects/%s/agents/%s/%s" % (quote(pid), quote(name), action)


def fmt(m):
    return "[%s] %s: %s" % (m["time"][11:19], m["from"], m["text"])


def label(m, name):
    to = addressed(m["text"])
    if not to:
        return "for everyone: reply"
    return "addressed to you: reply" if name in to else "for others: read only"
