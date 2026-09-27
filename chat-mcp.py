#!/usr/bin/env python3
"""MCP server (stdio) that wakes Claude Code for shared-chat messages.

A Claude Code channel (research preview): new lines in chat/log.md that are
not from Claude and not addressed only to @codex are pushed into the session
as <channel source="<server name>" sender=... time=...> events, and the
chat_post tool writes Claude's replies through ./chat. Start Claude Code
with `--dangerously-load-development-channels server:<server name>`.
Standard library only; MCP messages are newline-delimited JSON-RPC.
"""
import json
import os
import re
import subprocess
import sys
import threading
import time
from pathlib import Path

CHAT = Path(__file__).resolve().parent / "chat"
LOG = Path(subprocess.run([str(CHAT), "--where"], capture_output=True, text=True,
                          check=True).stdout.strip())
ENV = {**os.environ, "AGENT_CHAT_LOG": str(LOG)}  # posts go to the same log
LINE = re.compile(r"^\[([^\]]*)\] ([\w-]+): (.*)$")
# Several Claude sessions can share the chat; each posts under its own name.
NAME = os.environ.get("AGENT_CHAT_NAME", "claude")
if not re.fullmatch(r"claude(-\w+)*", NAME):
    NAME = "claude"
INSTRUCTIONS = (
    "Messages from the shared project chat (the user and Codex) arrive as "
    '<channel sender="..." time="..."> events. The user reads '
    "the chat in a web page, so answer chat messages with the chat_post tool, "
    "not in the terminal. Start with @user or @codex to address one. Keep "
    "replies short; the chat is for hand-offs and questions. "
    f"You post as {NAME}."
)
out_lock = threading.Lock()


def send(message):
    with out_lock:
        sys.stdout.write(json.dumps(message) + "\n")
        sys.stdout.flush()


def push(sender, stamp, text):
    # other Claude sessions only wake us when they name us, so we cannot loop
    if sender == NAME or text.startswith("@codex") or (
            sender.startswith("claude") and not text.startswith(f"@{NAME}")):
        return
    send({"jsonrpc": "2.0", "method": "notifications/claude/channel",
          "params": {"content": text, "meta": {"sender": sender, "time": stamp}}})


def follow():
    """Push each complete message appended after start-up."""
    LOG.parent.mkdir(exist_ok=True)
    LOG.touch()
    offset = LOG.stat().st_size
    pending = None  # [sender, time, text] until the next header line
    while True:
        time.sleep(1)
        size = LOG.stat().st_size if LOG.exists() else 0
        if size < offset:  # truncated or replaced
            offset = 0
        if size > offset:
            with LOG.open("rb") as f:
                f.seek(offset)
                chunk = f.read(size - offset)
            end = chunk.rfind(b"\n") + 1  # leave a half-written line for later
            offset += end
            for line in chunk[:end].decode(errors="replace").splitlines():
                m = LINE.match(line)
                if m:
                    if pending:
                        push(*pending)
                    pending = [m[2], m[1], m[3]]
                elif pending and line.startswith("    "):
                    pending[2] += "\n" + line[4:]
        # ./chat writes a message in one append, so flush it once idle
        if pending:
            push(*pending)
            pending = None


TOOLS = [{
    "name": "chat_post",
    "description": f"Post a message to the shared chat as {NAME} (the user and Codex read it).",
    "inputSchema": {"type": "object", "required": ["text"],
                    "properties": {"text": {"type": "string",
                                            "description": "Message; start with @user or @codex to address one"}}},
}]


def handle(req):
    method, rid = req.get("method"), req.get("id")
    if method == "initialize":
        result = {"protocolVersion": req["params"].get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {}, "experimental": {"claude/channel": {}}},
                  "serverInfo": {"name": "agent-chat", "version": "1"},
                  "instructions": INSTRUCTIONS}
    elif method == "tools/list":
        result = {"tools": TOOLS}
    elif method == "tools/call" and req["params"].get("name") == "chat_post":
        text = str(req["params"].get("arguments", {}).get("text", "")).strip()
        if not text:
            result = {"isError": True, "content": [{"type": "text", "text": "empty message"}]}
        else:
            r = subprocess.run([str(CHAT), NAME, text], env=ENV,
                               capture_output=True, text=True, timeout=90)
            note = r.stderr.strip() or "posted"
            result = {"isError": r.returncode != 0, "content": [{"type": "text", "text": note}]}
    elif method == "ping":
        result = {}
    elif rid is None:  # a notification, e.g. notifications/initialized
        return
    else:
        send({"jsonrpc": "2.0", "id": rid, "error": {"code": -32601, "message": f"unknown method {method}"}})
        return
    send({"jsonrpc": "2.0", "id": rid, "result": result})


if __name__ == "__main__":
    threading.Thread(target=follow, daemon=True).start()
    for raw in sys.stdin:
        if raw.strip():
            try:
                handle(json.loads(raw))
            except (ValueError, KeyError, TypeError, subprocess.SubprocessError) as e:
                print(f"chat-mcp: {e}", file=sys.stderr)
