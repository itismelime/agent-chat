"""MCP server (stdio) that gives one agent session its project's chat.

Outside a registered project it offers no tools and no instructions, so the
agent never notices it. Messages are newline-delimited JSON-RPC.
"""
import json
import os
import sys
from pathlib import Path

from .client import ApiError, Client, ServiceDown, fmt, label

CHAT = Path(__file__).resolve().parent.parent / "bin" / "chat"

TOOLS = [
    {"name": "chat_join",
     "description": "Join this project's chat under a short name you choose "
                    "(a-z, 0-9, '-'). Once per session.",
     "inputSchema": {"type": "object", "required": ["name"],
                     "properties": {"name": {"type": "string"}}}},
    {"name": "chat_post",
     "description": "Post to the project chat. Start with @name to address someone.",
     "inputSchema": {"type": "object", "required": ["text"],
                     "properties": {"text": {"type": "string"}}}},
    {"name": "chat_read",
     "description": "Chat messages you have not seen yet.",
     "inputSchema": {"type": "object", "properties": {}}},
]


CODEX_NOTE = ("\n\nIf the wait exits with 'service not running', your sandbox blocks "
              "127.0.0.1: tell the user to add `[sandbox_workspace_write]` "
              "`network_access = true` to ~/.codex/config.toml, and use chat_read until then.")


def kind_of(client_name):
    n = (client_name or "").lower()
    return "claude" if "claude" in n else "codex" if "codex" in n else "llm"


class Session:
    def __init__(self, client, cwd):
        self.client, self.name, self.kind = client, None, "llm"
        try:
            self.project, self.down = client.resolve(cwd), False
        except (ServiceDown, ApiError):
            self.project, self.down = None, True

    def wait_command(self):
        return "%s wait --as %s --project %s" % (CHAT, self.name, self.project["id"])

    def instructions(self):
        if self.down:
            return ("The agent-chat service is not running, so this project's chat is "
                    "unavailable (systemctl --user start agent-chat, then restart the session).")
        if not self.project:
            return None
        return ("This project (%s) has a shared chat with the user and other agents. "
                "Call chat_join with a short name you pick for yourself, then keep the wait "
                "command it gives you running as a background command. When the wait exits, "
                "a message arrived: read its output, reply with chat_post if it is for you, "
                "and start the wait again. A message without @ is for everyone; with @names "
                "only those reply. Keep replies short." % self.project["name"])

    def tools(self):
        return TOOLS if self.project else []

    def reminder(self):
        agents = self.client.call("GET", "/api/projects/%s/agents" % self.project["id"])[1]["agents"]
        if any(a["name"] == self.name and a["status"] == "waiting" for a in agents):
            return ""
        return ("\n\nReminder: your chat wait is not running. Start it as a background "
                "command: " + self.wait_command())

    def call(self, tool, args):
        """Run a tool. Returns (text, is_error)."""
        if not self.project:
            return "not in a registered project", True
        if not isinstance(args, dict):
            return "arguments must be an object", True
        pid = self.project["id"]
        try:
            if tool == "chat_join":
                if self.name:
                    return "already joined as %s; one session has one name" % self.name, True
                body = self.client.call("POST", "/api/projects/%s/agents" % pid,
                                        {"name": str(args.get("name", "")), "kind": self.kind})[1]
                self.name = body["agent"]["name"]
                recent = "\n".join(fmt(m) for m in body["recent"]) or "(no messages yet)"
                text = ("Joined %s as %s. Run this as a background command now, and again "
                        "each time it exits:\n%s\n\nRecent messages:\n%s"
                        % (self.project["name"], self.name, self.wait_command(), recent))
                if self.kind == "codex":
                    text += CODEX_NOTE
                return text, False
            if tool not in ("chat_post", "chat_read"):
                return "unknown tool: %s" % tool, True
            if not self.name:
                return "call chat_join first", True
            if tool == "chat_post":
                m = self.client.call("POST", "/api/projects/%s/messages" % pid,
                                     {"from": self.name, "text": str(args.get("text", ""))})[1]
                return "posted #%d%s" % (m["message"]["n"], self.reminder()), False
            msgs = self.client.call("GET", self.client.agent_path(pid, self.name, "read"))[1]
            text = "\n".join("%s  (%s)" % (fmt(m), label(m, self.name))
                             for m in msgs["messages"] if m["from"] != self.name)
            return (text or "no new messages") + self.reminder(), False
        except (ApiError, ServiceDown) as e:
            return str(e), True


def handle(session, req):
    """Answer one JSON-RPC request; None for notifications."""
    method, rid = req.get("method"), req.get("id")
    params = req.get("params") if isinstance(req.get("params"), dict) else {}
    if method == "initialize":
        info = params.get("clientInfo") if isinstance(params.get("clientInfo"), dict) else {}
        session.kind = kind_of(info.get("name"))
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}},
                  "serverInfo": {"name": "agent-chat", "version": "1"}}
        if session.instructions():
            result["instructions"] = session.instructions()
    elif method == "tools/list":
        result = {"tools": session.tools()}
    elif method == "tools/call":
        text, err = session.call(params.get("name"), params.get("arguments", {}))
        result = {"isError": err, "content": [{"type": "text", "text": text}]}
    elif method == "ping":
        result = {}
    elif rid is None:
        return None
    else:
        return {"jsonrpc": "2.0", "id": rid,
                "error": {"code": -32601, "message": "unknown method: %s" % method}}
    return {"jsonrpc": "2.0", "id": rid, "result": result}


def main():
    session = Session(Client(), os.getcwd())
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            req = json.loads(raw)
            reply = handle(session, req) if isinstance(req, dict) else None
        except ValueError as e:
            print("agent-chat mcp: %s" % e, file=sys.stderr)
            continue
        if reply:
            sys.stdout.write(json.dumps(reply) + "\n")
            sys.stdout.flush()
