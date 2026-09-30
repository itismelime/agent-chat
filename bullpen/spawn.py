"""Agents started from the page, each in its own detached tmux session.

tmux is always called with an argument list, never through a shell, so a
project path or typed text cannot run anything.
"""
import json
import re
import secrets
import shlex
import shutil
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

from .store import StoreError, addressed, wakes

TOOLS = ("claude", "codex", "opencode")
PROMPT = "join the chat"
BACK = "You are back in the chat: call chat_join with the name %s, then carry on as before."
KEYS = {"1": "1", "2": "2", "3": "3", "up": "Up", "down": "Down",
        "enter": "Enter", "esc": "Escape"}
MAX_TEXT = 2000
# Codex treats fast input as a paste and swallows an Enter sent right after it;
# half a second apart it submits (checked with Codex 0.157 and Claude Code 2.1).
PASTE_PAUSE = 0.5
SCREEN_LINES = 25
ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")
# Claude Code's spinner, just above its prompt box: "✶ Metamorphosing… (4s · ↓ 86 tokens · thinking)",
# "✻ Compacting conversation… (3s)"; its verb is random, "…" and the glyph are not
SPINNER = re.compile(r"^\s*[^\w\s\u276f\u2500\u2502]\s+([A-Z][^\u2026]*)\u2026(?:.*\((.*)\))?\s*$")
JOIN_GRACE = 60   # seconds a start may take to join before it needs the user
POLL_SECONDS = 2
# A screen that asks something: menu footers and question lines from real
# Claude Code 2.1 and Codex 0.157 prompts (tests/data/screens), and a numbered
# menu with its cursor on an option.
# ponytail: text matching, so a new wording can slip past; add it here and a
# screen to tests/data/screens.
QUESTION = re.compile(r"enter to confirm|enter confirm|enter continue|esc to cancel|do you want to|"
                      r"would you like to|permission required|^\s*[❯›]\s*\d+\.", re.I | re.M)
# OpenCode's footer while it works (tests/data/screens/working-*)
WORKING = re.compile(r"esc interrupt", re.I)


def tmux(*args):
    try:
        return subprocess.run(["tmux", *args], capture_output=True, text=True, timeout=10)
    except OSError as e:  # no tmux (e.g. Windows): the call fails like one tmux refused
        return subprocess.CompletedProcess(["tmux", *args], 1, "", str(e))


# tmux treats a target it cannot find as a prefix ("x" would hit "x2"); "="
# makes it match the session name exactly, and ":" names its current pane.
def exact(session):
    return "=" + session


def pane(session):
    return "=" + session + ":"


def available():
    return {t: shutil.which(t) is not None for t in ("tmux",) + TOOLS}


def claude_session(path, name, home=None):
    """The id of the Claude Code session in this project that took the chat
    name: the newest session log under ~/.claude/projects where a chat_join or
    chat_rename gave it that name (the reply, not the call: a join refused because
    the name was taken tried it too). None if there is none."""
    # ponytail: a name set with `bullpen rename` from a shell is not found; the
    # MCP tools are what agents use.
    root = Path(home or Path.home()) / ".claude" / "projects"
    slug = re.sub(r"[^A-Za-z0-9]", "-", str(path))
    took = re.compile(r"Joined [^\n]{1,200}? as %s\. |You are now %s \(was " % (re.escape(name), re.escape(name)))
    best = None
    for d in root.glob(slug + "*"):  # the folder and its subfolders
        for f in d.glob("*.jsonl"):
            try:
                mtime = f.stat().st_mtime
                if (best is None or mtime > best[0]) and took.search(f.read_text(errors="replace")):
                    best = (mtime, f.stem)
            except OSError:
                continue
    return best[1] if best else None


def start(tool, path, session, token, model=None, config_home=None, data_home=None,
          resume=None, name=None, personality=None):
    """Start the tool with the prompt "join the chat" in a detached tmux session;
    with resume (a session id), continue that session and rejoin as name."""
    if tool not in TOOLS:
        raise StoreError(400, "tool must be one of: " + ", ".join(TOOLS))
    if tool == "opencode" and not model:
        raise StoreError(400, "OpenCode needs a model")
    missing = [t for t in ("tmux", tool) if shutil.which(t) is None]
    if missing:
        raise StoreError(503, "%s is not installed" % " and ".join(missing))
    env = ["-e", "BULLPEN_SPAWN=" + token]
    if tool == "opencode":
        env += ["-e", "XDG_CONFIG_HOME=" + config_home] if config_home else []
        env += ["-e", "XDG_DATA_HOME=" + data_home] if data_home else []
        # none of the user's own OpenCode or Claude Code setup: no CLAUDE.md, no
        # config from a tmux server's environment
        env += ["-e", "OPENCODE_DISABLE_CLAUDE_CODE=1", "-e", "OPENCODE_CONFIG=",
                "-e", "OPENCODE_CONFIG_DIR=", "-e", "OPENCODE_CONFIG_CONTENT="]
        # OpenCode drops --prompt when it continues a session: the poller types BACK once it is idle
        command = (["opencode", "-s", resume, "-m", "ac/" + model] if resume
                   else ["opencode", "-m", "ac/" + model, "--prompt", PROMPT])
    elif resume:
        command = (["claude", "--resume", resume, BACK % name] if tool == "claude"
                   else ["codex", "resume", resume, "%s (start %s)" % (BACK % name, token)])
    else:
        # Codex's MCP servers are started by its app-server daemon and do not see
        # BULLPEN_SPAWN, so Codex gets the token in its prompt for chat_join.
        prompt = PROMPT + (" with a name that fits your personality: %s" % personality
                           if personality else "")
        command = [tool, prompt if tool == "claude" else "%s (start %s)" % (prompt, token)]
    if tool == "claude":  # its statusline keeps your plan's limits for the page (plan.py)
        command[1:1] = ["--settings", claude_settings()]
    elif tool == "codex":  # joins and chats without asking you each time: bullpen's tools are approved
        command[1:1] = codex_approvals()
    r = tmux("new-session", "-d", "-s", session, "-c", path, *env, "--", *command)
    if r.returncode:
        raise StoreError(503, "tmux could not start it: %s" % r.stderr.strip())


def claude_settings():
    """Settings added to a Claude started here: bullpen's statusline, which shows yours."""
    chat = Path(__file__).resolve().parent.parent / "bin" / "bullpen"
    cmd = "%s %s statusline" % (shlex.quote(sys.executable), shlex.quote(str(chat)))
    return json.dumps({"statusLine": {"type": "command", "command": cmd, "refreshInterval": 60}})


def codex_approvals():
    """Codex -c overrides that approve every bullpen tool for a Codex started here, so it
    never stops at "Allow the bullpen MCP server to run tool ...?"; your own Codex
    sessions and ~/.codex/config.toml are left as they are."""
    from .mcp import TOOLS
    return [x for t in TOOLS for x in ("-c", 'mcp_servers.bullpen.tools.%s.approval_mode="approve"' % t["name"])]


def alive(session):
    return tmux("has-session", "-t", exact(session)).returncode == 0


def screen(session, colors=False):
    r = tmux("capture-pane", "-p", *(["-e"] if colors else []), "-t", pane(session))
    if r.returncode:
        raise StoreError(404, "the agent's terminal has ended")
    return r.stdout


def send_key(session, key):
    if key not in KEYS:
        raise StoreError(400, "key must be one of: " + " ".join(KEYS))
    tmux("send-keys", "-t", pane(session), KEYS[key])


def send_text(session, text):
    if not 0 < len(text) <= MAX_TEXT:
        raise StoreError(400, "text must be 1-%d characters" % MAX_TEXT)
    tmux("send-keys", "-t", pane(session), "-l", "--", text)
    time.sleep(PASTE_PAUSE)
    tmux("send-keys", "-t", pane(session), "Enter")


def rename(session, new):
    return tmux("rename-session", "-t", exact(session), new).returncode == 0


def stop(session):
    tmux("kill-session", "-t", exact(session))  # already gone is fine


def needs_you(text):
    """Whether the bottom of a terminal screen asks the user something."""
    return bool(QUESTION.search("\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])))


def opencode_state(text):
    """OpenCode's state from its footer only (the conversation above it may
    contain any words): working, question, idle, or unknown (a dialog, starting)."""
    foot = "\n".join([l for l in text.rstrip().splitlines() if l.strip()][-3:]).lower()
    if "esc interrupt" in foot:
        return "working"
    if "enter confirm" in foot:
        return "question"
    return "idle" if "ctrl+p" in foot else "unknown"


def claude_activity(text):
    """What Claude is doing from the spinner above its prompt box: compacting,
    thinking, working, or None when there is no spinner (idle, or not drawn yet)."""
    lines = [ANSI.sub("", l) for l in text.rstrip().splitlines()[-SCREEN_LINES:]]
    at = next((i for i in range(len(lines) - 1, -1, -1) if "\u276f" in lines[i]), None)
    if at is None:
        return None
    above = [l for l in lines[max(0, at - 6):at] if l.strip() and not set(l.strip()) <= {"\u2500"}][-3:]
    for line in reversed(above):
        m = SPINNER.match(line)
        if m:
            if m.group(1).startswith("Compacting"):
                return "compacting"
            return "thinking" if "thinking" in (m.group(2) or "") else "working"
    return None


def codex_activity(text):
    """Codex shows one state while at work, "• Working (15s • esc to interrupt)", no
    thinking of its own: working, or None."""
    bottom = "\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])
    return "working" if re.search(r"^\s*\u2022 \w[^\n]*\(\d+[smh][^\n]*esc to interrupt\)", bottom, re.M) else None


def claude_state(text):
    """Claude Code's state from a screen captured with colors (capture-pane -e): idle
    when its prompt line is empty or shows only the dim placeholder, never with a
    draft typed in it. Working, question, idle, or unknown."""
    lines = text.rstrip().splitlines()[-SCREEN_LINES:]
    plain = "\n".join(ANSI.sub("", l) for l in lines)
    if needs_you(plain):
        return "question"
    if "esc to interrupt" in plain.lower() or claude_activity(text):
        return "working"
    prompt = next((l for l in reversed(lines) if "\u276f" in l), None)  # ❯
    if prompt is None:
        return "unknown"
    rest = prompt.split("\u276f", 1)[1].replace("\xa0", " ")
    rest = re.sub(r"^(\x1b\[(?:0|39|49|22)?m|\s)+", "", rest)
    return "idle" if not ANSI.sub("", rest).strip() or rest.startswith("\x1b[2m") else "unknown"


def working(text):
    """Whether the bottom of the screen shows OpenCode working."""
    return bool(WORKING.search("\n".join(text.rstrip().splitlines()[-SCREEN_LINES:])))


def format_message(m, name, personality=None, rules=""):
    """One line to type into an OpenCode agent's terminal."""
    from .client import label
    who = (" Your personality: %s." % re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", personality).rstrip(".")
           if personality else "")
    tail = " (%s)%s%s Reply with chat_post." % (
        label(m, name), who, " " + re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", rules) if rules else "")
    # typed as keystrokes into a coding agent: no control characters, which
    # could erase the "[chat]" prefix, interrupt it, or start a "!" shell line
    text = re.sub(r"[\x00-\x1f\x7f-\x9f]", " ", m["text"].replace("\r", "").replace("\n", " / "))
    line = "[chat] %s: %s%s" % (m["from"], text, tail)
    if len(line) > MAX_TEXT:
        cut = " (… cut; chat_read has the whole message)"
        room = MAX_TEXT - len("[chat] %s: " % m["from"]) - len(cut) - len(tail)
        line = "[chat] %s: %s%s%s" % (m["from"], text[:room], cut, tail)
    return line


class Spawner:
    """Starts, links, watches and stops the agents started from the page."""

    def __init__(self, store, models=None, port=8765):
        self.store, self.models, self.port = store, models, port
        self.lock = threading.Lock()  # a rename on join vs the poller's liveness check

    def start(self, pid, tool, model=None, personality=None):
        self.store._check_role(personality)  # before tmux starts anything
        project = self.store.project(pid)
        token = secrets.token_hex(16)
        session = "bullpen-%s-%s" % (pid, token[:6])
        home, data = self._opencode(model) if tool == "opencode" else (None, None)
        start(tool, project["path"], session, token, model=model, config_home=home, data_home=data,
              personality=personality)
        record = self.store.add_spawned(pid, token, tool, session, personality)
        if model:
            record = self.store.update_spawned(pid, token, model=model)
        return record

    def _opencode(self, model):
        """Check OpenCode can use model and write its config; its (config, data) homes."""
        from . import opencode
        usable = opencode.tool_models(self.models.ollama, self.models.gpu_total())
        if model not in usable:
            raise StoreError(400, "%s cannot call tools or is not installed; choose one of: %s"
                             % (model, ", ".join(usable) or "none"))
        opencode.write_config(self.store.root, self.models.ollama.url, usable, self.port)
        return str(opencode.config_home(self.store.root)), str(opencode.data_home(self.store.root))

    def resume(self, pid, name):
        """Continue an offline Claude, Codex or OpenCode agent's own session in tmux;
        it rejoins under its name, so it then has View terminal and Stop."""
        project, agent = self.store.project(pid), self.store._agent(pid, name)
        if any(name in (r["name"], r.get("resume")) for r in self.store.spawned(pid).values()):
            raise StoreError(409, "%s already has a terminal here; use View terminal" % name)
        if any(a["name"] == name and a["status"] != "offline" for a in self.store.status(pid)):
            raise StoreError(409, "%s is not offline" % name)
        if agent["kind"] == "claude":
            sid = claude_session(project["path"], name)
        elif agent["kind"] == "codex":
            sid = agent.get("thread")
        elif agent["kind"] == "opencode":
            from . import opencode
            sid, model = opencode.named_session(self.store.root, name) or (None, None)
        else:
            raise StoreError(400, "only Claude, Codex and OpenCode agents can be resumed")
        if not sid:
            raise StoreError(404, "no session of %s found to resume" % name)
        token = secrets.token_hex(16)
        session = "bullpen-%s-%s" % (pid, token[:6])
        extra = {}
        if agent["kind"] == "opencode":
            home, data = self._opencode(model)
            start("opencode", project["path"], session, token, model=model, config_home=home,
                  data_home=data, resume=sid, name=name)
            # its session is known; only calls written from now on are rescued
            extra = dict(model=model, oc_session=sid, oc_seen=int(time.time() * 1000), greet=BACK % name)
        else:
            start(agent["kind"], project["path"], session, token, resume=sid, name=name)
        self.store.add_spawned(pid, token, agent["kind"], session)
        return self.store.update_spawned(pid, token, resume=name, **extra)

    def record(self, pid, token):
        records = self.store.spawned(pid)
        if token not in records:
            raise StoreError(404, "no started agent %s" % token)
        return records[token]

    def linked(self, pid, token, name):
        """After a join: give the tmux session the agent's name, if free."""
        new = "bullpen-%s-%s" % (pid, name)
        with self.lock:
            if rename(self.record(pid, token)["session"], new):
                self.store.update_spawned(pid, token, session=new)

    def stop(self, pid, token):
        r = self.record(pid, token)
        stop(r["session"])
        self.store.drop_spawned(pid, token)
        if r.get("model") and self.models and not self._model_in_use(r["model"]):
            try:
                self.models.ollama.unload(r["model"])
            except Exception as e:  # stopping still succeeded
                print("bullpen: could not unload %s: %s" % (r["model"], e), file=sys.stderr)

    def _model_in_use(self, model):
        for p in self.store.projects():
            for a in self.store.agents(p["id"]).values():
                if a.get("model") == model and not a.get("removed") and not a.get("gone"):
                    return True
            if any(r.get("model") == model for r in self.store.spawned(p["id"]).values()):
                return True
        return False

    def poll(self):
        """Drop starts whose session ended; flag those whose terminal asks."""
        for project in self.store.projects():
            pid = project["id"]
            for token in list(self.store.spawned(pid)):
                with self.lock:
                    r = self.store.spawned(pid).get(token)
                    if r is None:
                        continue
                    r = dict(r, token=token)
                    if not alive(r["session"]):
                        self.store.drop_spawned(pid, token, session=r["session"])
                        continue
                if r["name"] and self.store.waiting.get((pid, r["name"])):
                    need = False  # an open bullpen wait means the agent is idle
                else:
                    try:
                        text = screen(r["session"])
                    except StoreError:
                        continue
                    need = needs_you(text)
                    if r["tool"] == "opencode" and opencode_state(text) == "idle":
                        if r.get("greet"):  # a resumed OpenCode: tell it once it can take input
                            send_text(r["session"], r["greet"])
                            self.store.update_spawned(pid, token, greet=None)
                            continue
                        seen, r = r.get("oc_seen"), self._rescue(pid, token, r)
                        if r.get("oc_seen") != seen:
                            continue  # it was just typed to: look again next poll
                    if not r["name"]:
                        age = time.time() - datetime.fromisoformat(r["started"]).timestamp()
                        need = need or age > JOIN_GRACE
                    elif r["tool"] == "opencode":
                        need = self._type_unread(pid, r, opencode_state(text))
                    elif r["tool"] == "codex":  # its messages come by its thread; the screen says if it works
                        self._activity(pid, r["name"], codex_activity(text))
                    elif r["tool"] == "claude":  # no wait loop: messages are typed in when idle
                        try:
                            colored = screen(r["session"], colors=True)
                        except StoreError:
                            continue
                        self._activity(pid, r["name"], claude_activity(colored))
                        need = self._type_unread(pid, r, claude_state(colored))
                self.store.set_needs(pid, token, need)

    def _rescue(self, pid, token, r):
        """Run the chat calls an idle OpenCode model wrote out as text instead of
        making them, and tell it so. Returns the start's fresh record."""
        from . import opencode
        root = self.store.root
        sid = r.get("oc_session") or opencode.find_session(
            root, self.store.project(pid)["path"],
            int(datetime.fromisoformat(r["started"]).timestamp() * 1000),
            {x.get("oc_session") for x in self.store.spawned(pid).values()})
        if not sid:
            return r
        fields = {"oc_session": sid}
        for t, tool, args in opencode.written_calls(root, sid, r.get("oc_seen", 0)):
            fields["oc_seen"] = t
            name = self.store.spawned(pid)[token]["name"]
            try:
                if tool == "chat_join" and not name:
                    name = self.store.join(pid, args.get("name", ""), "opencode", spawn=token)["name"]
                    self.linked(pid, token, name)
                    note = "You joined the chat as %s." % name
                elif tool == "chat_post" and name:
                    self.store.post(pid, name, args.get("text", ""), dm=name if args.get("private") is True else None,
                                    ask=args.get("ask") is True)
                    note = "Your message was posted."
                else:
                    continue
            except StoreError as e:
                note = "Your %s failed: %s." % (tool, e)
            send_text(self.store.spawned(pid)[token]["session"],
                      "[chat] %s (Your tool call came out as text; bullpen ran it for you.)" % note)
        return self.store.update_spawned(pid, token, **fields)

    def _activity(self, pid, name, activity):
        """Show what it is doing in the roster; after a compaction its summary may have
        dropped the rules, so they ride along with its next message again."""
        was = self.store.local.get((pid, name), {}).get("activity")
        if was == "compacting" and activity != "compacting":
            with self.store.changed:
                if name in self.store.agents(pid):
                    self.store._update(pid, name, told=None)
        self.store.set_local(pid, name, activity=activity)

    def _type_unread(self, pid, r, state):
        """Type the oldest unread message that wakes this OpenCode or Claude agent, if
        its screen is idle (state: from opencode_state or claude_state). Unread comes from its stored cursor, so nothing is lost
        on a restart and nothing it already read with chat_read is typed.
        Returns whether its screen asks the user something."""
        name = r["name"]
        agent = self.store.agents(pid).get(name)
        if not agent or agent.get("removed") or agent.get("gone"):
            return state == "question"
        unread = [m for m in self.store.messages(pid, agent["cursor"]) if wakes(m, name, self.store.away(), self.store.lead(pid))]
        if state == "idle" and unread:
            from . import rules
            told, personality = rules.fresh(self.store, pid, name)
            line, batch = format_message(unread[0], name, personality, told), unread[:1]
            for m in unread[1:]:  # all that waits, in one turn, as far as one line holds
                more = format_message(m, name)
                if len(line) + 4 + len(more) > MAX_TEXT:
                    break
                line, batch = line + " || " + more, batch + [m]
            send_text(r["session"], line)
            self.store.delivered(pid, name, batch[-1]["n"])
            owed = [m["n"] for m in batch if name in addressed(m["text"]) or m.get("dm")]
            if owed:  # a reply is owed
                self.store.update_spawned(pid, r["token"], owed=owed[-1])
        elif state == "idle" and r.get("owed"):
            # it answered in its terminal only (gpt-oss does): one reminder, then let it be
            if not any(m["from"] == name for m in self.store.messages(pid, r["owed"])):
                send_text(r["session"], "[bullpen] #%d was addressed to you and you posted no reply. "
                                        "If one is needed, post it now with the chat_post tool." % r["owed"])
                unread = [None]  # it is at work again
            self.store.update_spawned(pid, r["token"], owed=None)
        self.store.set_local(pid, name, busy=state != "idle" or bool(unread))
        return state == "question"

    def run(self):
        while True:
            try:
                self.poll()
            except Exception as e:  # keep watching whatever one poll ran into
                print("bullpen: poll failed: %s" % e, file=sys.stderr)
            time.sleep(POLL_SECONDS)

    def start_poller(self):
        threading.Thread(target=self.run, daemon=True).start()
