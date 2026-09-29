# Talking local LLMs — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Installed local models can be added to a project as chat members that answer like other agents, with the same addressing rules, a loop guard and clear status.

**Architecture:** The store keeps local members as agents of kind `llm` with `model` and `role`, calls a new `store.talk` hook when one is woken, and reports Working/Offline from in-memory state. `bullpen/talk.py` holds the prompt building and a `Talker` worker that answers one wake-up at a time through the Ollama client. The server adds two routes and wires the Talker; the page gets menu entries, prompts, a `/local` command and the member line.

**Tech Stack:** Python ≥ 3.9 stdlib, vanilla JS.

**Spec:** `docs/specs/2026-09-27-local-talk-design.md`

## Global Constraints

- Python 3.9+ standard library only.
- Wake rules unchanged: user messages wake every member, agent messages only addressed names; nobody by its own message.
- A local member reads but does not answer messages addressed only to others; answers at most 3 agent messages in a row (reset by a user message); answers only the newest of several queued wake-ups.
- Prompt budget: 3/4 of `num_ctx` at 3 characters per token; `num_predict` 1024; request timeout 600 s; thinking from `Tuning.request(..., "talk")`.
- Role at most 500 characters; model must be installed in the Ollama in use.
- Files under 500 lines. No co-author trailer in this repo.
- Tests: `python3 -m unittest`; commit only after checking that the full suite printed `OK`.

## Review Focus

- A local member must never answer its own message, even when it addresses itself (test in Task 2).
- A reply that addresses another agent must wake that agent, so hand-offs work (test in Task 2).
- A member removed while its reply is being generated must not post it (test in Task 2).
- Status must show Working only while that member generates, not while another local member does (test in Task 2).
- An Offline member must come back to Available after its next successful answer (test in Task 2).

---

### Task 1: Local members in the store

**Files:**
- Modify: `bullpen/store.py`
- Test: `tests/test_store.py`

**Interfaces:**
- Produces: `Store.talk` (hook `talk(pid, name, message)`, default None); `Store.local` (`{(pid, name): {"busy": bool, "error": str|None}}`); `add_local(pid, name, model, role=None) -> agent dict`; `set_role(pid, name, role)`; `set_local(pid, name, **fields)`; `status()` entries gain `model`, `role`, `error` (None for other agents); local members' status `removed` / `offline` (error) / `busy` / `waiting`.

- [ ] **Step 1: Write the failing tests**

Add to `tests/test_store.py`, before `def test_remove_and_readd(self):`:

```python
    def test_local_members(self):
        woken = []
        self.store.talk = lambda pid, name, m: woken.append((name, m["text"]))
        a = self.store.add_local(self.pid, " Qwen ", "qwen3:0.6b", role="You review plans")
        self.assertEqual((a["name"], a["kind"], a["model"], a["role"]),
                         ("qwen", "llm", "qwen3:0.6b", "You review plans"))
        self.store.add_local(self.pid, "tiny", "qwen3:0.6b")
        self.store.join(self.pid, "alice", "claude")
        self.store.post(self.pid, "user", "hello all")          # wakes both
        self.store.post(self.pid, "alice", "@qwen please look")  # wakes qwen only
        self.store.post(self.pid, "qwen", "@qwen talking to myself")  # wakes nobody
        self.store.remove(self.pid, "tiny")
        self.store.post(self.pid, "user", "after removal")      # tiny is removed
        self.assertEqual(sorted(woken), sorted([
            ("qwen", "hello all"), ("tiny", "hello all"), ("qwen", "@qwen please look"),
            ("qwen", "after removal")]))

    def test_local_status_and_role(self):
        self.store.add_local(self.pid, "qwen", "qwen3:0.6b")
        row = lambda: next(a for a in self.store.status(self.pid) if a["name"] == "qwen")
        self.assertEqual((row()["status"], row()["model"], row()["error"]), ("waiting", "qwen3:0.6b", None))
        self.store.set_local(self.pid, "qwen", busy=True)
        self.assertEqual(row()["status"], "busy")
        self.store.set_local(self.pid, "qwen", busy=False, error="model not found")
        self.assertEqual((row()["status"], row()["error"]), ("offline", "model not found"))
        self.store.set_local(self.pid, "qwen", error=None)
        self.assertEqual(row()["status"], "waiting")
        self.store.set_role(self.pid, "qwen", "Be terse")
        self.assertEqual(row()["role"], "Be terse")
        self.store.set_role(self.pid, "qwen", "")
        self.assertIsNone(row()["role"])
        for bad in (lambda: self.store.set_role(self.pid, "qwen", "x" * 501),
                    lambda: self.store.add_local(self.pid, "big", "m", role="x" * 501),
                    lambda: self.store.set_role(self.pid, "nobody", "r")):
            with self.assertRaises(StoreError):
                bad()
        self.store.join(self.pid, "alice", "claude")
        self.assertIsNone(next(a for a in self.store.status(self.pid) if a["name"] == "alice")["model"])

```

- [ ] **Step 2: Run them to verify they fail**

Run: `python3 -m unittest tests.test_store 2>&1 | tail -3`
Expected: errors, `AttributeError: 'Store' object has no attribute 'add_local'`.

- [ ] **Step 3: Implement**

In `bullpen/store.py`:

1. After `LINK_SECONDS = 300 …` add `MAX_ROLE = 500`.
2. After `        self.needs = set()  # …` in `__init__` add:

```python
        # talk(pid, name, message) wakes a local-model member (talk.Talker); must not block
        self.talk = None
        self.local = {}  # (project id, name) -> {"busy", "error"} of local-model members
```

3. Replace

```python
            if self.deliver:
                for name, a in self.agents(pid).items():
                    if a.get("thread") and not a.get("removed") and not a.get("gone") \
                            and wakes(m, name):
                        self.deliver(pid, name, a["thread"], m)
            return m
```

with

```python
            for name, a in self.agents(pid).items():
                if a.get("removed") or a.get("gone") or not wakes(m, name):
                    continue
                if a.get("thread") and self.deliver:
                    self.deliver(pid, name, a["thread"], m)
                elif a.get("model") and self.talk:
                    self.talk(pid, name, m)
            return m
```

4. Before `    def read(self, pid, name):` add:

```python
    @staticmethod
    def _check_role(role):
        if role is not None and not (isinstance(role, str) and len(role) <= MAX_ROLE):
            raise StoreError(400, "a role is text of at most %d characters" % MAX_ROLE)
        return (role or "").strip() or None

    def add_local(self, pid, name, model, role=None):
        """A local-model member: an agent of kind llm answered by talk.Talker."""
        role = self._check_role(role)
        with self.changed:
            agent = self.join(pid, name, "llm")
            self._update(pid, agent["name"], model=model, role=role)
            return dict(agent, model=model, role=role)

    def set_role(self, pid, name, role):
        role = self._check_role(role)
        with self.changed:
            self._agent(pid, name, active=False)
            self._update(pid, name, role=role)

    def set_local(self, pid, name, **fields):
        with self.changed:
            self.local.setdefault((pid, name), {}).update(fields)
```

5. In `status()`, replace

```python
                elif a.get("gone"):
                    status = "offline"
```

with

```python
                elif a.get("gone"):
                    status = "offline"
                elif a.get("model"):
                    state = self.local.get((pid, name), {})
                    status = "offline" if state.get("error") else "busy" if state.get("busy") else "waiting"
```

and replace

```python
                out.append({"name": name, "kind": a["kind"], "joined": a["joined"],
                            "status": status, "spawn": token})
```

with

```python
                out.append({"name": name, "kind": a["kind"], "joined": a["joined"],
                            "status": status, "spawn": token, "model": a.get("model"),
                            "role": a.get("role"),
                            "error": self.local.get((pid, name), {}).get("error")
                            if a.get("model") else None})
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest 2>&1 | tail -1`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add bullpen/store.py tests/test_store.py
git commit -m "Store: local-model members, talk hook, their status"
```

---

### Task 2: The Talker

**Files:**
- Create: `bullpen/talk.py`, `tests/test_talk.py`
- Modify: `bullpen/ollama.py` (`chat` gets `num_predict`), `tests/fake_ollama.py` (`reply`, `chat_delay`)

**Interfaces:**
- Consumes: Task 1 store methods; `models.Models` (`ollama`, `tuning`, `gpu_total()`); `Ollama.chat`.
- Produces: `talk.system_prompt(name, project, others, role) -> str`; `talk.build_messages(name, project, others, role, history, num_ctx) -> [message]`; `talk.clean_reply(name, text) -> str`; `talk.Talker(store, models)` with `__call__(pid, name, m)` (the `store.talk` hook), `answer(pid, name, m)`, `run()`, `start()`; constants `CHARS_PER_TOKEN = 3`, `PROMPT_SHARE = 0.75`, `MAX_REPLY = 1024`, `MAX_AGENT_STREAK = 3`, `TIMEOUT = 600`. `Ollama.chat(model, messages, num_ctx=None, think=False, timeout=600, num_predict=None)`. `FakeOllama.reply` (text or None) and `FakeOllama.chat_delay` (seconds).

- [ ] **Step 1: Extend the fakes and the client**

In `tests/fake_ollama.py`: in `FakeOllama.__init__` add `self.reply, self.chat_delay = None, 0`; in the `/api/chat` branch, replace

```python
                    content = "391" if "17" in data["messages"][-1]["content"] else "benchmark ok"
```

with

```python
                    time.sleep(fake.chat_delay)
                    content = fake.reply if fake.reply is not None else (
                        "391" if "17" in data["messages"][-1]["content"] else "benchmark ok")
```

In `bullpen/ollama.py`, replace `chat` with:

```python
    def chat(self, model, messages, num_ctx=None, think=False, timeout=600, num_predict=None):
        body = {"model": model, "messages": messages, "stream": False, "think": think}
        options = {k: v for k, v in (("num_ctx", num_ctx), ("num_predict", num_predict)) if v}
        if options:
            body["options"] = options
        return self.json("POST", "/api/chat", body, timeout)
```

- [ ] **Step 2: Write the failing tests**

`tests/test_talk.py`:

```python
import tempfile
import threading
import time
import unittest
from pathlib import Path

from bullpen import models, talk
from bullpen.store import Store
from tests.fake_ollama import GIB, FakeOllama


def msg(n, sender, text):
    return {"n": n, "time": "2026-09-27T20:00:00+02:00", "from": sender, "kind": "x", "text": text}


class PromptTest(unittest.TestCase):
    def test_system_prompt(self):
        p = talk.system_prompt("qwen", "OpenVIBES", ["alice", "cody"], None)
        self.assertIn("You are qwen, a local model in the chat of project OpenVIBES", p)
        self.assertIn("with the user and alice, cody", p)
        self.assertNotIn("Your role", p)
        self.assertIn("Your role: Be terse", talk.system_prompt("qwen", "P", [], "Be terse"))
        self.assertIn("no other members", talk.system_prompt("qwen", "P", [], None))

    def test_budget_newest_first_and_roles(self):
        history = [msg(i, "user" if i % 2 else "qwen", "m%d " % i + "x" * 90) for i in range(1, 11)]
        out = talk.build_messages("qwen", "P", [], None, history, num_ctx=512)
        self.assertEqual(out[0]["role"], "system")
        budget = 512 * talk.PROMPT_SHARE * talk.CHARS_PER_TOKEN - len(out[0]["content"])
        self.assertLessEqual(sum(len(m["content"]) for m in out[1:]), budget)
        self.assertEqual(out[-1]["role"], "assistant")          # m10 is its own message
        self.assertEqual(out[-2]["content"][:9], "user: m9 ")   # others as "name: text"
        self.assertLess(len(out), 11)

    def test_the_newest_message_is_always_included(self):
        out = talk.build_messages("qwen", "P", [], None, [msg(1, "user", "x" * 10000)], num_ctx=512)
        self.assertEqual(len(out), 2)

    def test_clean_reply(self):
        self.assertEqual(talk.clean_reply("qwen", "  Qwen: hello  "), "hello")
        self.assertEqual(talk.clean_reply("qwen", "hello qwen:"), "hello qwen:")
        self.assertEqual(talk.clean_reply("qwen", "   "), "")


class TalkerTest(unittest.TestCase):
    def setUp(self):
        self.fake = FakeOllama()
        self.addCleanup(self.fake.close)
        self.fake.add("tiny", size=GIB)
        tmp = Path(tempfile.mkdtemp())
        (tmp / "proj").mkdir()
        self.store = Store(tmp / "data")
        self.store.add_project(str(tmp / "proj"))
        self.models = models.Models(root=tmp, ollama_url=self.fake.url)
        self.talker = talk.Talker(self.store, self.models)
        self.store.add_local("proj", "qwen", "tiny", role="Be terse")
        self.store.join("proj", "alice", "claude")
        self.fake.reply = "qwen: hi there"

    def texts(self):
        return [(m["from"], m["text"]) for m in self.store.messages("proj")]

    def chats(self):
        return [c[2] for c in self.fake.calls if c[:2] == ("POST", "/api/chat")]

    def test_answers_as_the_member(self):
        m = self.store.post("proj", "user", "hello")
        self.talker.answer("proj", "qwen", m)
        self.assertEqual(self.texts()[-1], ("qwen", "hi there"))
        req = self.chats()[0]
        self.assertEqual((req["model"], req["think"], req["options"]["num_predict"]), ("tiny", False, 1024))
        self.assertIn("Your role: Be terse", req["messages"][0]["content"])
        self.assertEqual(req["messages"][-1], {"role": "user", "content": "user: hello"})
        self.assertEqual(self.store.agents("proj")["qwen"]["cursor"], m["n"])

    def test_reads_but_does_not_answer_messages_for_others(self):
        self.talker.answer("proj", "qwen", self.store.post("proj", "user", "@alice only you"))
        self.assertEqual(self.chats(), [])

    def test_never_answers_itself(self):
        m = self.store.post("proj", "qwen", "@qwen note to self")
        self.talker.answer("proj", "qwen", m)
        self.assertEqual(self.chats(), [])

    def test_a_reply_can_hand_off(self):
        self.fake.reply = "@alice over to you"
        self.talker.answer("proj", "qwen", self.store.post("proj", "user", "hi"))
        got = self.store.wait("proj", "alice", 1)
        self.assertEqual(got["messages"][-1]["text"], "@alice over to you")

    def test_loop_guard(self):
        for i in range(5):
            self.talker.answer("proj", "qwen", self.store.post("proj", "alice", "@qwen %d" % i))
        self.assertEqual(len(self.chats()), talk.MAX_AGENT_STREAK)
        self.talker.answer("proj", "qwen", self.store.post("proj", "user", "reset"))
        self.talker.answer("proj", "qwen", self.store.post("proj", "alice", "@qwen again"))
        self.assertEqual(len(self.chats()), talk.MAX_AGENT_STREAK + 2)

    def test_offline_on_failure_then_back(self):
        self.fake.fail_chat = "model requires more system memory"
        self.talker.answer("proj", "qwen", self.store.post("proj", "user", "hi"))
        row = lambda: next(a for a in self.store.status("proj") if a["name"] == "qwen")
        self.assertEqual(row()["status"], "offline")
        self.assertIn("more system memory", row()["error"])
        self.fake.fail_chat = None
        self.talker.answer("proj", "qwen", self.store.post("proj", "user", "again"))
        self.assertEqual((row()["status"], row()["error"]), ("waiting", None))

    def test_empty_answer_is_not_posted(self):
        self.fake.reply = "   "
        before = len(self.texts())
        self.talker.answer("proj", "qwen", self.store.post("proj", "user", "hi"))
        self.assertEqual(len(self.texts()), before + 1)  # only the user's message

    def test_removed_while_generating_posts_nothing(self):
        self.fake.chat_delay = 0.5
        m = self.store.post("proj", "user", "hi")
        t = threading.Thread(target=self.talker.answer, args=("proj", "qwen", m))
        t.start()
        time.sleep(0.2)
        self.store.remove("proj", "qwen")
        t.join()
        self.assertNotIn("qwen", [f for f, _ in self.texts()])

    def test_busy_only_while_it_generates(self):
        self.store.add_local("proj", "other", "tiny")
        self.fake.chat_delay = 0.5
        m = self.store.post("proj", "user", "hi")
        t = threading.Thread(target=self.talker.answer, args=("proj", "qwen", m))
        t.start()
        time.sleep(0.2)
        status = {a["name"]: a["status"] for a in self.store.status("proj")}
        self.assertEqual((status["qwen"], status["other"]), ("busy", "waiting"))
        t.join()
        self.assertEqual(next(a for a in self.store.status("proj") if a["name"] == "qwen")["status"],
                         "waiting")

    def test_catches_up_once(self):
        m1 = self.store.post("proj", "user", "first")
        m2 = self.store.post("proj", "user", "second")
        self.talker("proj", "qwen", m1)
        self.talker("proj", "qwen", m2)
        self.talker.start()
        for _ in range(100):
            if any(f == "qwen" for f, _ in self.texts()):
                break
            time.sleep(0.05)
        time.sleep(0.2)
        self.assertEqual(len(self.chats()), 1)
        self.assertEqual(self.chats()[0]["messages"][-1]["content"], "user: second")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run them to verify they fail**

Run: `python3 -m unittest tests.test_talk 2>&1 | tail -3`
Expected: `ImportError: cannot import name 'talk'`.

- [ ] **Step 4: Implement**

`bullpen/talk.py`:

```python
"""Local-model members: chat messages in, Ollama requests out, replies posted.

One worker answers every local member, one reply at a time (bullpen's
Ollama loads one model at a time)."""
import queue
import re
import sys
import threading

from .ollama import OllamaError
from .store import StoreError, addressed

CHARS_PER_TOKEN = 3     # a rough guide for the prompt budget
PROMPT_SHARE = 0.75     # of num_ctx for the prompt; the rest is left for the answer
MAX_REPLY = 1024        # tokens per answer
MAX_AGENT_STREAK = 3    # agent messages answered in a row before waiting for the user
TIMEOUT = 600


def system_prompt(name, project, others, role):
    text = ('You are %s, a local model in the chat of project %s with the user and %s. '
            'Messages are shown as "name: text". Reply as %s only, briefly, in plain text, '
            'without your name in front. A message without @ is for everyone; with @names '
            'only those reply.' % (name, project, ", ".join(others) or "no other members", name))
    return text + ("\nYour role: " + role if role else "")


def build_messages(name, project, others, role, history, num_ctx):
    """The system message plus the newest messages that fit the budget,
    oldest first; the newest message is always included."""
    system = {"role": "system", "content": system_prompt(name, project, others, role)}
    budget = int(num_ctx * PROMPT_SHARE * CHARS_PER_TOKEN) - len(system["content"])
    picked = []
    for m in reversed(history):
        if m["from"] == name:
            item = {"role": "assistant", "content": m["text"]}
        else:
            item = {"role": "user", "content": "%s: %s" % (m["from"], m["text"])}
        budget -= len(item["content"])
        if budget < 0 and picked:
            break
        picked.append(item)
    return [system] + picked[::-1]


def clean_reply(name, text):
    return re.sub(r"^\s*%s\s*:\s*" % re.escape(name), "", text or "", flags=re.I).strip()


class Talker:
    """The store.talk hook and the worker behind it."""

    def __init__(self, store, models):
        self.store, self.models = store, models
        self.jobs, self.lock = queue.Queue(), threading.Lock()
        self.pending = {}  # (pid, name) -> newest message that woke it
        self.streak = {}   # (pid, name) -> agent messages answered in a row

    def __call__(self, pid, name, m):
        with self.lock:
            key = (pid, name)
            first = key not in self.pending
            self.pending[key] = m
            if first:
                self.jobs.put(key)

    def start(self):
        threading.Thread(target=self.run, daemon=True).start()

    def run(self):
        while True:
            key = self.jobs.get()
            with self.lock:
                m = self.pending.pop(key, None)
            if m:
                try:
                    self.answer(*key, m)
                except Exception as e:  # keep answering the other members
                    print("bullpen: local member %s failed: %s" % (key[1], e), file=sys.stderr)

    def answer(self, pid, name, m):
        key = (pid, name)
        to = addressed(m["text"])
        if m["from"] == name or (to and name not in to):
            return  # its own message, or one for others: read only
        if m["from"] == "user":
            self.streak[key] = 0
        elif self.streak.get(key, 0) >= MAX_AGENT_STREAK:
            return
        else:
            self.streak[key] = self.streak.get(key, 0) + 1
        agents = self.store.agents(pid)
        agent = agents.get(name)
        if not agent or agent.get("removed") or not agent.get("model"):
            return
        others = [n for n, a in agents.items() if n != name and not a.get("removed")]
        project = self.store.project(pid)["name"]
        history = [x for x in self.store.messages(pid) if x["n"] <= m["n"]]
        self.store.set_local(pid, name, busy=True)
        try:
            ollama = self.models.ollama
            num_ctx, think = self.models.tuning.request(agent["model"], ollama,
                                                        self.models.gpu_total(), "talk")
            r = ollama.chat(agent["model"], build_messages(name, project, others, agent.get("role"),
                                                           history, num_ctx),
                            num_ctx=num_ctx, think=think, timeout=TIMEOUT, num_predict=MAX_REPLY)
            text = clean_reply(name, (r.get("message") or {}).get("content", ""))
            if text and not self.store.agents(pid).get(name, {}).get("removed"):
                self.store.post(pid, name, text)
            self.store.delivered(pid, name, m["n"])
            self.store.set_local(pid, name, busy=False, error=None)
        except (OllamaError, StoreError) as e:
            self.store.set_local(pid, name, busy=False, error=str(e))
```

- [ ] **Step 5: Run the tests**

Run: `python3 -m unittest tests.test_talk -v 2>&1 | tail -3`
Expected: `OK`. Then the full suite: `python3 -m unittest 2>&1 | tail -1` → `OK`.

- [ ] **Step 6: Commit**

```bash
git add bullpen/talk.py bullpen/ollama.py tests/test_talk.py tests/fake_ollama.py
git commit -m "Talker: local-model members answer in the chat"
```

---

### Task 3: Routes and wiring

**Files:**
- Modify: `bullpen/server.py`, `tests/test_server.py`

**Interfaces:**
- Consumes: Tasks 1–2.
- Produces: `POST /api/projects/<id>/locals {model, name, role?}` → `201 {"agent"}` (`400` if the model is not installed, `409` name taken); `POST /api/projects/<id>/agents/<name>/role {role}` → `200 {"agents"}`; `serve()` sets `store.talk` to a started `talk.Talker` when `deliver` is true.

- [ ] **Step 1: Write the failing test**

Add to `ModelRoutesTest` in `tests/test_server.py`:

```python
    def test_local_member_routes(self):
        c = self.c
        pdir = Path(tempfile.mkdtemp())
        pid = c.call("POST", "/api/projects", {"path": str(pdir)})[1]["project"]["id"]
        base = "/api/projects/%s" % pid
        status, body = c.call("POST", base + "/locals", {"model": "qwen3.5:9b", "name": "qwen",
                                                         "role": "Be terse"})
        self.assertEqual((status, body["agent"]["model"]), (201, "qwen3.5:9b"))
        for data, code in (({"model": "ghost", "name": "g"}, 400),
                           ({"model": "qwen3.5:9b", "name": "qwen"}, 409)):
            with self.assertRaises(ApiError) as e:
                c.call("POST", base + "/locals", data)
            self.assertEqual(e.exception.code, code)
        agents = c.call("POST", base + "/agents/qwen/role", {"role": "Be kind"})[1]["agents"]
        row = next(a for a in agents if a["name"] == "qwen")
        self.assertEqual((row["role"], row["model"], row["kind"]), ("Be kind", "qwen3.5:9b", "llm"))
```

(Add `from pathlib import Path` to the imports if missing.)

- [ ] **Step 2: Run it to verify it fails**

Run: `python3 -m unittest tests.test_server.ModelRoutesTest.test_local_member_routes 2>&1 | tail -3`
Expected: FAIL with `ApiError: not found`.

- [ ] **Step 3: Implement**

In `bullpen/server.py`:

1. Change `from . import spawn` to `from . import spawn, talk`.
2. Before `                if what == ["spawned"] and method == "GET":` add:

```python
                if what == ["locals"] and method == "POST":
                    data = self.body()
                    model = self.field(data, "model")
                    if model not in [m["name"] for m in models.ollama.tags()]:
                        raise StoreError(400, "%s is not installed in the Ollama in use" % model)
                    agent = store.add_local(pid, self.field(data, "name"), model, data.get("role"))
                    return 201, {"agent": agent}
```

3. Change the `remove`/`readd` route to also take `role`: replace

```python
                if len(what) == 3 and what[0] == "agents" and method == "POST" \
                        and what[2] in ("remove", "readd"):
                    self.body()
                    (store.remove if what[2] == "remove" else store.readd)(pid, what[1])
                    return 200, {"agents": store.status(pid)}
```

with

```python
                if len(what) == 3 and what[0] == "agents" and method == "POST" \
                        and what[2] in ("remove", "readd", "role"):
                    data = self.body()
                    if what[2] == "role":
                        store.set_role(pid, what[1], data.get("role"))
                    else:
                        (store.remove if what[2] == "remove" else store.readd)(pid, what[1])
                    return 200, {"agents": store.status(pid)}
```

4. In `serve()`, replace

```python
    server.RequestHandlerClass = make_handler(store, server.server_address[1],
                                              wait_seconds, spawner,
                                              os.getuid() if owner is None else owner,
                                              models or models_mod.Models(store.root))
```

with

```python
    models = models or models_mod.Models(store.root)
    if deliver:
        talker = talk.Talker(store, models)
        store.talk = talker
        talker.start()
    server.RequestHandlerClass = make_handler(store, server.server_address[1],
                                              wait_seconds, spawner,
                                              os.getuid() if owner is None else owner, models)
```

- [ ] **Step 4: Run all tests**

Run: `python3 -m unittest 2>&1 | tail -1`
Expected: `OK`.

- [ ] **Step 5: Commit**

```bash
git add bullpen/server.py tests/test_server.py
git commit -m "Routes to add local members and set their role; the Talker runs in the service"
```

---

### Task 4: The page

**Files:**
- Modify: `bullpen/page.html`

- [ ] **Step 1: Local-model state**

After `api('api/tools').then(x=>{tools=x;}).catch(()=>{});` add:

```js
// installed local models for the start menu, refreshed every 30 s
let locals={ok:false,reason:'Checking Ollama…',names:[]};
async function loadLocals(){
  try{const s=await api('api/models/status');
    if(!s.reachable){locals={ok:false,reason:'Ollama is not reachable',names:[]};return;}
    locals={ok:true,reason:'',names:(await api('api/models')).models.map(m=>m.name)};}
  catch(e){locals={ok:false,reason:e.message,names:[]};}}
loadLocals();setInterval(loadLocals,30000);
function suggestName(model){return model.split(':')[0].split('/').pop().toLowerCase()
  .replace(/[^a-z0-9-]+/g,'-').replace(/^-+|-+$/g,'')||'local';}
async function addLocal(model,name){
  if(name===undefined){name=prompt(`Name for ${model} in this chat`,suggestName(model));if(!name)return;}
  const role=name&&arguments.length<2?prompt('Optional role, e.g. "You review plans critically" (leave empty for none)',''):null;
  await api(`api/projects/${cur}/locals`,{model,name:name.trim(),role:role||null});
  return `Added ${name.trim()} (${model}).`;}
```

- [ ] **Step 2: Menu entries, member line, Edit role**

1. Replace the body of `startItems()`:

```js
function startItems(){
  const items=['claude','codex'].map(tool=>{
    const off=!cur?'Add a project first':!tools.tmux?'tmux is not installed':!tools[tool]?`${KIND[tool]} is not installed`:'';
    return ['Start '+KIND[tool],()=>api(`api/projects/${cur}/spawned`,{tool}),off];});
  if(!locals.ok)items.push(['Add local model',()=>{},locals.reason]);
  else if(!locals.names.length)items.push(['Add local model',()=>{},'No models installed; get one in Models']);
  else for(const n of locals.names)items.push(['Add local model: '+n,()=>addLocal(n),cur?'':'Add a project first']);
  return items;}
```

2. Replace `      d.title='Right-click for options';d.append(el('i','dot'),a.name,el('small','',KIND[a.kind]||a.kind));` with:

```js
      d.title=a.error?'Offline: '+a.error:'Right-click for options';
      d.append(el('i','dot'),a.name,el('small','',a.model?'local LLM · '+a.model:KIND[a.kind]||a.kind));
```

3. In `agentItems`, replace `  if(a.starting)return items;` with:

```js
  if(a.starting)return items;
  if(a.model)items.push(['Edit role',()=>{const r=prompt(`Role for ${a.name} (empty for none)`,a.role||'');
    if(r!==null)return api(`api/projects/${cur}/agents/${encodeURIComponent(a.name)}/role`,{role:r||null});}]);
```

- [ ] **Step 3: /local and the overlay**

1. In `runCommand`, replace `  const [cmd,arg='']=text.slice(1).trim().split(/\s+/);` with `  const [cmd,arg='',arg2='']=text.slice(1).trim().split(/\s+/);`, and before `  throw new Error(\`Unknown command` add:

```js
  if(cmd==='local'){if(!arg||!arg2)throw new Error('Use /local <model> <name>.');return addLocal(arg,arg2);}
```

2. In the overlay, after `<dt>/remove &lt;name&gt;</dt><dd>remove it from the chat</dd>` add:

```html
<dt>/local &lt;model&gt; &lt;name&gt;</dt><dd>add an installed local model as a member</dd>
```

- [ ] **Step 4: Check syntax, run tests**

Extract the page script and `node --check` it (as in earlier tasks). Run `python3 -m unittest 2>&1 | tail -1` → `OK`.

- [ ] **Step 5: Browser check**

A demo service (script in the scratchpad) with a `FakeOllama` that has `tiny` installed and `reply` set to `"hello from tiny"`, `models.Models(root, ollama_url=fake.url)`, and `serve(port=8799, store=…, deliver=True, models=m)` so the Talker runs (tmux is not used unless an agent is started). Open http://127.0.0.1:8799 and check:
1. **+ Agent** lists **Add local model: tiny**; choosing it asks for a name (suggested `tiny`) and a role; the member appears as `tiny` · "local LLM · tiny", Available.
2. Posting "hi" makes `tiny` answer "hello from tiny"; posting "@someone hi" gets no answer from it.
3. With the fake's `fail_chat` set (from the demo script's console or a second request), the member turns Offline with the error as tooltip.
4. Right-click → **Edit role** saves; `/local tiny t2` adds a second member.
5. Stop the demo by its PID.

- [ ] **Step 6: Commit**

```bash
git add bullpen/page.html
git commit -m "Page: add local models as members, their line, Edit role, /local"
```

---

### Task 5: Docs, deploy, live check

- [ ] **Step 1: README**

Under "Use", after the Models item, add:

```markdown
6. Add a local model as a member: **+ Agent → Add local model: <model>** (or
   `/local <model> <name>`), with an optional role. It answers like the
   other agents (no `@` or `@name` for it), sees the newest messages that
   fit its context, thinks only if you set that in Models, and answers at
   most three agent messages in a row before waiting for you.
```

- [ ] **Step 2: Commit, merge, deploy**

Commit the README; after the final review and its fixes, merge into `main` (fast-forward), push, and restart the service (`systemctl --user restart bullpen`; no reinstall is needed).

- [ ] **Step 3: Live check with the user**

In a scratch project registered with `bullpen add`: add `qwen3:0.6b` as `qwen`, post "hello", see it Working then answering; post "@someone-else hi" and see no answer; Edit role and see the role reflected in its next answer. Record a "Verified" line in the spec, commit, push.
