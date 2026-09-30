"""MCP server (stdio) that gives one agent session its project's chat.

Outside a registered project it offers no tools and no instructions, so the
agent never notices it. Messages are newline-delimited JSON-RPC.

The process an agent runs is a Relay: the tools, instructions and tool logic
(Session) run in the service, so an updated service reaches sessions that are
already running; the relay announces it with tools/list_changed.
"""
import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

from .client import ApiError, Client, ServiceDown, fmt, label
from .codex import find_thread

CHAT = Path(__file__).resolve().parent.parent / "bin" / "bullpen"
# changes with this file: running relays then tell their agent to fetch the tools again
VERSION = hashlib.sha1(Path(__file__).read_bytes()).hexdigest()[:12]
WATCH_SECONDS = 30
# posted to every chat once when it changes (update it with what agents should learn)
NEWS = ("bullpen was updated. For agents: keep your bullpen wait running always. It costs no "
        "tokens, and the user is told when an agent stops listening. When a usage or budget warning "
        "says to wrap up, start no new work, but restart the wait and keep listening.")
DOWN = ("The bullpen service is not running, so this project's chat is unavailable "
        "(%s; the chat tools work once it runs)." % (
            "schtasks /run /tn bullpen" if os.name == "nt" else "systemctl --user start bullpen"))

TOOLS = [
    {"name": "chat_join",
     "description": "Join this project's chat under a short name you choose "
                    "(a-z, 0-9, '-') that says your role or persona, like architect, reviewer "
                    "or tester, not your model or tool. Once per session.",
     "inputSchema": {"type": "object", "required": ["name"],
                     "properties": {"name": {"type": "string"},
                                    "spawn": {"type": "string",
                                              "description": "the start code, if your first "
                                                             "prompt gave one: (start <code>)"}}}},
    {"name": "chat_post",
     "description": "Post to the project chat. Start with @name to address someone. private: true "
                    "sends it to the user alone (a direct message the other agents never see); use it "
                    "for questions or reports meant only for the user, and to answer their private messages. "
                    "ask: true when the user has to answer or decide something before you can go on; "
                    "leave it out for reports and updates, which the user reads without replying.",
     "inputSchema": {"type": "object", "required": ["text"],
                     "properties": {"text": {"type": "string"}, "private": {"type": "boolean"},
                                    "ask": {"type": "boolean"}}}},
    {"name": "chat_rename",
     "description": "Change your name in the chat. Your color, personality, board cards and unread "
                    "messages move with you; messages you already posted keep the old name.",
     "inputSchema": {"type": "object", "required": ["name"],
                     "properties": {"name": {"type": "string"}}}},
    {"name": "chat_react",
     "description": "React to message #n (the number shown before its sender) instead of replying, "
                    "when it only needs acknowledging: 👍 ✅ 👀 ❤️ 🎉 🙏 😄 👎. The same emoji again "
                    "takes it off. Reactions wake nobody.",
     "inputSchema": {"type": "object", "required": ["n", "emoji"],
                     "properties": {"n": {"type": "integer"},
                                    "emoji": {"type": "string",
                                              "enum": ["👍", "✅", "👀", "❤️", "🎉", "🙏", "😄", "👎"]}}}},
    {"name": "chat_avatar",
     "description": "Set your picture in the chat from an image file (PNG, JPEG or WebP, at most "
                    "1 MB; a square one looks best). path: absolute, or relative to the project folder.",
     "inputSchema": {"type": "object", "required": ["path"], "properties": {"path": {"type": "string"}}}},
    {"name": "chat_read",
     "description": "Chat messages you have not seen yet.",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "board_list",
     "description": "The project's kanban board: epics with their progress, then the work items by "
                    "column (To do, In progress, Review, Done).",
     "inputSchema": {"type": "object", "properties": {}}},
    {"name": "board_add",
     "description": "Add a card to the board (in To do). assignee: an agent's name, user, or leave out. "
                    "kind: epic for a big piece of work that groups items (default: item). "
                    "epic: the number of the epic this item belongs to.",
     "inputSchema": {"type": "object", "required": ["title"],
                     "properties": {"title": {"type": "string"}, "description": {"type": "string"},
                                    "assignee": {"type": "string"},
                                    "kind": {"type": "string", "enum": ["item", "epic"]},
                                    "epic": {"type": "integer"}}}},
    {"name": "board_update",
     "description": "Change a card: move it (column todo, doing, review or done), assign it, "
                    "edit its title or description, or put an item in an epic (epic: its number, "
                    "or 0 to take it out).",
     "inputSchema": {"type": "object", "required": ["id"],
                     "properties": {"id": {"type": "integer"},
                                    "column": {"type": "string",
                                               "enum": ["todo", "doing", "review", "done"]},
                                    "assignee": {"type": "string"}, "title": {"type": "string"},
                                    "description": {"type": "string"}, "epic": {"type": "integer"}}}},
]
BOARD_TOOLS = ("board_list", "board_add", "board_update")
# what the page does with a message, so agents write for it
FORMAT = (" The page renders messages as Markdown: put code, commands and logs in fenced code "
          "blocks (```lang), and use lists and tables where they help. A file path in the project "
          "opens in a viewer when clicked. Lines like \"A) …\" \"B) …\" (or \"Option 1: …\") "
          "become answer buttons for the user. To acknowledge a message that needs no reply, react "
          "with chat_react (e.g. 👍) instead of posting. Post with ask: true only when the user has to answer "
          "or decide; reports and updates go without it. Use private: true for things only the user "
          "should see.")


def kind_of(client_name):
    n = (client_name or "").lower()
    for kind in ("opencode", "claude", "codex"):
        if kind in n:
            return kind
    return "llm"


class Session:
    def __init__(self, client, cwd, find_thread=find_thread, spawn_token=None):
        self.client, self.name, self.kind = client, None, "llm"
        self.find_thread = find_thread
        self.spawn_token = spawn_token  # set when the page started this agent
        try:
            self.project, self.down = client.resolve(cwd), False
        except (ServiceDown, ApiError):
            self.project, self.down = None, True

    def wait_command(self):
        return "%s wait --as %s --project %s" % (CHAT, self.name, self.project["id"])

    def about(self):
        """The user's name and a few lines about them, as they wrote them ("" when none)."""
        try:
            line = self.client.call("GET", "/api/profile")[1].get("for_agents")
        except (ApiError, ServiceDown):
            return ""
        return " " + line if line else ""

    def standing(self, lead=" "):
        """The project's rules for agents ("" when none or unreadable)."""
        from .rules import summary
        try:
            body = self.client.call("GET", "/api/projects/%s/rules" % self.project["id"])[1]
        except (ApiError, ServiceDown):
            return ""
        standing = body.get("standing", summary(body["rules"]))  # rules, then pins
        return lead + standing if standing else ""

    def typed(self):
        """A Claude started from the page: bullpen types its messages into its terminal
        when it is idle (spawn.Spawner), so it needs no wait loop and costs nothing idle."""
        return self.kind == "claude" and bool(self.spawn_token)

    def instructions(self):
        if self.down:
            return DOWN
        if not self.project:
            return None
        if self.kind == "opencode" or self.typed():
            return ("This project (%s) has a shared chat with the user and other agents. "
                    "Call chat_join with a short name for your role or persona (architect, reviewer, tester…; not your model or tool); chat messages "
                    "for you are then typed into this session as they arrive. Reply with "
                    "chat_post if a message is for you. A message without @ is for everyone; "
                    "with @names only those reply. Keep replies short." % self.project["name"] + FORMAT + self.about() + self.standing())
        if self.kind == "codex":
            return ("This project (%s) has a shared chat with the user and other agents. "
                    "Call chat_join with a short name for your role or persona (architect, reviewer, tester…; not your model or tool) (and, if your first "
                    "prompt said \"(start <code>)\", that code as spawn); chat messages "
                    "for you are then delivered into this session as they arrive. Reply with "
                    "chat_post if a message is for you. A message without @ is for everyone; "
                    "with @names only those reply. Keep replies short." % self.project["name"] + FORMAT + self.about() + self.standing())
        return ("This project (%s) has a shared chat with the user and other agents. "
                "Call chat_join with a short name for your role or persona (architect, reviewer, tester…; not your model or tool), then keep the wait "
                "command it gives you running as a background command. When the wait exits, "
                "a message arrived: read its output, reply with chat_post if it is for you, "
                "and start the wait again. The wait costs no tokens: keep it running always, also "
                "when a usage or budget warning says to wrap up (then start no new work, but keep "
                "listening). A message without @ is for everyone; with @names "
                "only those reply. Keep replies short." % self.project["name"] + FORMAT + self.about() + self.standing())

    def tools(self):
        if not self.project:
            return []
        if self.kind == "codex":
            return TOOLS
        # only Codex needs the spawn argument; it confuses other models
        join = dict(TOOLS[0], inputSchema={"type": "object", "required": ["name"],
                                           "properties": {"name": {"type": "string"}}})
        return [join] + TOOLS[1:]

    def board(self, pid, tool, args):
        base = "/api/projects/%s/board" % pid
        if tool == "board_list":
            b = self.client.call("GET", base)[1]
            lines, cols = [], dict(b["columns"])
            epics = [c for c in b["cards"] if c.get("kind") == "epic"]
            items = [c for c in b["cards"] if c.get("kind") != "epic"]
            if epics:
                lines.append("Epics:")
                for e in epics:
                    mine = [c for c in items if c.get("epic") == e["id"]]
                    lines.append('  epic #%d "%s" (%s, %d/%d items done)%s' % (
                        e["id"], e["title"], cols[e["column"]],
                        sum(c["column"] == "done" for c in mine), len(mine),
                        " (%s)" % e["assignee"] if e["assignee"] else ""))
            for key, label in b["columns"]:
                cards = [c for c in items if c["column"] == key]
                lines.append("%s:%s" % (label, "" if cards else " (empty)"))
                for c in cards:
                    lines.append('  #%d "%s"%s%s' % (c["id"], c["title"],
                                                    " (%s)" % c["assignee"] if c["assignee"] else "",
                                                    " [epic #%d]" % c["epic"] if c.get("epic") else ""))
                    if c.get("description"):
                        d = " ".join(c["description"].split())
                        lines.append("      " + (d[:300] + "…" if len(d) > 300 else d))
            return "\n".join(lines)
        if tool == "board_add":
            card = self.client.call("POST", base + "/cards", {
                "by": self.name, "title": str(args.get("title", "")),
                "description": str(args.get("description") or ""),
                "assignee": args.get("assignee"), "kind": args.get("kind"),
                "epic": args.get("epic")})[1]["card"]
            return 'added %s#%d "%s"' % ("epic " if card["kind"] == "epic" else "", card["id"], card["title"])
        n = args.get("id")
        if not isinstance(n, int) or isinstance(n, bool):
            raise ApiError(400, "id must be a card number")
        data = {k: args[k] for k in ("column", "assignee", "title", "description", "epic") if k in args}
        if data.get("epic") == 0:
            data["epic"] = None  # out of its epic
        card = self.client.call("POST", "%s/cards/%d" % (base, n), dict(data, by=self.name))[1]["card"]
        return '#%d "%s" is now in %s%s' % (card["id"], card["title"], card["column"],
                                             ", assigned to %s" % card["assignee"] if card["assignee"] else "")

    def reminder(self):
        if self.kind in ("codex", "opencode"):
            return ""  # Codex is woken by codex queue, not by a wait
        agents = self.client.call("GET", "/api/projects/%s/agents" % self.project["id"])[1]["agents"]
        if any(a["name"] == self.name and a["status"] == "waiting" for a in agents):
            return ""
        return ("\n\nReminder: your bullpen wait is not running. Start it as a background "
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
                    return "already joined as %s; use chat_rename to change your name" % self.name, True
                join = {"name": str(args.get("name", "")), "kind": self.kind}
                if self.kind == "codex":
                    join["thread"] = self.find_thread()
                spawn = self.spawn_token or args.get("spawn")
                if isinstance(spawn, str) and spawn:
                    join["spawn"] = spawn
                body = self.client.call("POST", "/api/projects/%s/agents" % pid, join)[1]
                self.name = body["agent"]["name"]
                recent = "\n".join(fmt(m) for m in body["recent"]) or "(no messages yet)"
                joined = "Joined %s as %s. " % (self.project["name"], self.name)
                if self.kind == "opencode" or self.typed():
                    how = ("Chat messages for you are typed into this session as they arrive; "
                           "reply with chat_post. Run no wait command: there is nothing to keep running.")
                elif self.kind != "codex":
                    how = ("Run this as a background command now (with a timeout of at least "
                           "30 minutes, the default: it ends by itself before that), and again "
                           "each time it exits:\n%s" % self.wait_command())
                elif body["agent"].get("thread"):
                    how = ("Chat messages for you are delivered into this session as they "
                           "arrive; reply with chat_post.")
                else:
                    how = ("This Codex session could not be linked to the chat (for example "
                           "a resumed session), so messages are not delivered to you; call "
                           "chat_read to see new ones.")
                standing = self.standing(lead="")
                if standing:
                    joined += standing + " "
                if body["agent"].get("personality"):
                    joined += "Your personality: %s. " % body["agent"]["personality"].rstrip(".")
                # told once here, not with every wake: bullpen.plan wakes it after the reset
                joined += ("Near a usage limit, leave your card In progress with a line in its description "
                           "on where you stopped; bullpen wakes you when the window resets. ")
                return "%s%s\n\nRecent messages:\n%s" % (joined, how, recent), False
            if tool not in ("chat_post", "chat_read", "chat_rename", "chat_react", "chat_avatar") + BOARD_TOOLS:
                return "unknown tool: %s" % tool, True
            if not self.name:
                return "call chat_join first", True
            if tool in BOARD_TOOLS:
                return self.board(pid, tool, args), False
            if tool == "chat_rename":
                self.client.call("POST", self.client.agent_path(pid, self.name, "rename"),
                                 {"name": str(args.get("name", ""))})
                old, self.name = self.name, str(args.get("name", "")).strip().lower()  # as the store has it
                text = "You are now %s (was %s)." % (self.name, old)
                if self.kind not in ("codex", "opencode"):
                    text += (" A bullpen wait still running as %s keeps working; start the next one "
                             "as:\n%s" % (old, self.wait_command()))
                return text, False
            if tool == "chat_avatar":
                import base64
                f = Path(self.project["path"]) / str(args.get("path", ""))  # an absolute path stays as it is
                try:
                    data = f.read_bytes() if f.is_file() and f.stat().st_size <= 1_000_000 else None
                except OSError:
                    data = None
                if data is None:
                    return "no image file of at most 1 MB at %s" % f, True
                self.client.call("POST", "/api/projects/%s/avatars/%s" % (pid, self.name),
                                 {"data": base64.b64encode(data).decode()})
                return "your picture is set", False
            if tool == "chat_react":
                n = args.get("n")
                if not isinstance(n, int) or isinstance(n, bool):
                    return "n must be a message number", True
                self.client.call("POST", "/api/projects/%s/messages/%d/react" % (pid, n),
                                 {"from": self.name, "emoji": args.get("emoji")})
                return "reacted %s to #%d" % (args.get("emoji"), n), False
            if tool == "chat_post":
                m = self.client.call("POST", "/api/projects/%s/messages" % pid,
                                     {"from": self.name, "text": str(args.get("text", "")),
                                      "private": args.get("private") is True,
                                      "ask": args.get("ask") is True})[1]
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
        if session.kind == "opencode" and not session.spawn_token:
            session.kind = "llm"  # started by hand, not by the page: nothing types into it
        result = {"protocolVersion": params.get("protocolVersion", "2025-06-18"),
                  "capabilities": {"tools": {"listChanged": True}},
                  "serverInfo": {"name": "bullpen", "version": "1"}}
        instructions = session.instructions()
        if instructions:
            result["instructions"] = instructions
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


def serve_request(client, data):
    """The service's side of a Relay: one op of one agent session, rebuilt from
    what the relay keeps (so a service restart loses nothing)."""
    from .store import StoreError
    if not isinstance(data.get("cwd"), str):
        raise StoreError(400, "cwd must be a path")
    spawn = data.get("spawn") if isinstance(data.get("spawn"), str) else None
    thread = data.get("thread") if isinstance(data.get("thread"), str) else None
    s = Session(client, data["cwd"], find_thread=lambda: thread, spawn_token=spawn)
    s.kind = data.get("kind") if data.get("kind") in ("claude", "codex", "opencode", "llm") else "llm"
    s.name = data.get("name") if isinstance(data.get("name"), str) else None
    op = data.get("op")
    if op == "init":
        return {"instructions": s.instructions(), "version": VERSION}
    if op == "tools":
        return {"tools": s.tools(), "version": VERSION}
    if op == "call":
        text, err = s.call(data.get("tool"), data.get("args", {}))
        return {"text": text, "isError": err, "name": s.name, "version": VERSION}
    raise StoreError(400, "op must be init, tools or call")


class Relay:
    """The session in the agent's MCP process: it keeps the agent's name (and a
    Codex thread) and asks the service (serve_request) for everything else."""

    def __init__(self, client, cwd, find_thread=find_thread, spawn_token=None):
        self.client, self.cwd, self.find_thread = client, cwd, find_thread
        self.spawn_token, self.kind, self.name, self.thread = spawn_token, "llm", None, None
        self.version = None  # of the tools the agent last listed; "" when that failed

    def _ask(self, op, **data):
        return self.client.call("POST", "/api/mcp", dict(
            data, op=op, cwd=self.cwd, kind=self.kind, name=self.name,
            spawn=self.spawn_token, thread=self.thread))[1]

    def instructions(self):
        try:
            return self._ask("init")["instructions"]
        except ServiceDown:
            return DOWN
        except ApiError:
            return None

    def tools(self):
        try:
            body = self._ask("tools")
        except (ApiError, ServiceDown):
            self.version = ""  # listed nothing: announce the tools once the service answers
            return []
        self.version = body["version"]
        return body["tools"]

    def call(self, tool, args):
        if tool == "chat_join" and self.kind == "codex" and not self.thread:
            self.thread = self.find_thread()  # this process's parent, so found here
        try:
            body = self._ask("call", tool=tool, args=args if isinstance(args, dict) else None)
        except (ApiError, ServiceDown) as e:
            return str(e), True
        self.name = body["name"]
        return body["text"], body["isError"]

    def changed(self):
        """Whether the service's tools differ from those the agent last listed.
        After a True, stays False until the agent lists them again."""
        if self.version is None:
            return False
        try:
            now = self.client.call("GET", "/api/mcp/version", timeout=5)[1]["version"]
        except (ApiError, ServiceDown, KeyError, TypeError):
            return False
        if now == self.version:
            return False
        self.version = None
        return True


def watch(session, write, every=WATCH_SECONDS):
    while True:
        time.sleep(every)
        if session.changed():
            write({"jsonrpc": "2.0", "method": "notifications/tools/list_changed"})


def main():
    session = Relay(Client(), os.getcwd(), spawn_token=os.environ.get("BULLPEN_SPAWN"))
    lock = threading.Lock()

    def write(msg):
        with lock:
            sys.stdout.write(json.dumps(msg) + "\n")
            sys.stdout.flush()

    threading.Thread(target=watch, args=(session, write), daemon=True).start()
    for raw in sys.stdin:
        if not raw.strip():
            continue
        try:
            req = json.loads(raw)
            reply = handle(session, req) if isinstance(req, dict) else None
        except ValueError as e:
            print("bullpen mcp: %s" % e, file=sys.stderr)
            continue
        if reply:
            write(reply)
