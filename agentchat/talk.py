"""Local-model members: chat messages in, Ollama requests out, replies posted.

One worker answers every local member, one reply at a time (agent-chat's
Ollama loads one model at a time)."""
import queue
import re
import sys
import threading

from .ollama import OllamaError
from .store import StoreError, addressed, sees

CHARS_PER_TOKEN = 3     # a rough guide for the prompt budget
PROMPT_SHARE = 0.75     # of num_ctx for the prompt; the rest is left for the answer
MAX_REPLY = 1024        # tokens per answer
MAX_AGENT_STREAK = 3    # agent messages answered in a row before waiting for the user
TIMEOUT = 600


def system_prompt(name, project, others, role, rules=""):
    text = ('You are %s, a local model in the chat of project %s with the user and %s. '
            'Messages are shown as "name: text". Reply as %s only, briefly, in plain text, '
            'without your name in front. A message without @ is for everyone; with @names '
            'only those reply.' % (name, project, ", ".join(others) or "no other members", name))
    return text + ("\nYour personality: " + role if role else "") + ("\n" + rules if rules else "")


def build_messages(name, project, others, role, history, num_ctx, rules=""):
    """The system message plus the newest messages that fit the budget,
    oldest first; the newest message is always included."""
    system = {"role": "system", "content": system_prompt(name, project, others, role, rules)}
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
    """The answer as posted: no inline <think>…</think>, no leading "name:"."""
    text = re.sub(r"<think>.*?</think>", "", text or "", flags=re.S | re.I)
    return re.sub(r"^\s*%s\s*:\s*" % re.escape(name), "", text, flags=re.I).strip()


class Talker:
    """The store.talk hook and the worker behind it."""

    def __init__(self, store, models):
        self.store, self.models = store, models
        self.jobs, self.lock = queue.Queue(), threading.Lock()
        self.pending = {}  # (pid, name) -> newest message that woke it
        self.streak = {}   # (pid, name) -> agent messages answered in a row

    def __call__(self, pid, name, m):
        key = (pid, name)
        to = addressed(m["text"])
        with self.lock:
            if m["from"] == "user":
                self.streak[key] = 0  # any user message resets the loop guard
            if m["from"] == name or (to and name not in to):
                return  # read only: queueing it could replace a question for this member
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
                    print("agent-chat: local member %s failed: %s" % (key[1], e), file=sys.stderr)

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
        history = [x for x in self.store.messages(pid) if x["n"] <= m["n"] and sees(x, name)]
        self.store.set_local(pid, name, busy=True)
        try:
            ollama = self.models.ollama
            num_ctx, think = self.models.tuning.request(agent["model"], ollama,
                                                        self.models.gpu_total(), "talk")
            from . import rules
            r = ollama.chat(agent["model"], build_messages(name, project, others, agent.get("role"),
                                                           history, num_ctx,
                                                           rules.summary(rules.get(self.store, pid))),
                            num_ctx=num_ctx, think=think, timeout=TIMEOUT, num_predict=MAX_REPLY)
            text = clean_reply(name, (r.get("message") or {}).get("content", ""))
            if text and not self.store.agents(pid).get(name, {}).get("removed"):
                self.store.post(pid, name, text)
            self.store.delivered(pid, name, m["n"])
            self.store.set_local(pid, name, busy=False, error=None)
        except (OllamaError, StoreError) as e:
            self.store.set_local(pid, name, busy=False, error=str(e))
        except Exception as e:  # e.g. a connection reset mid-reply: never stay Working
            self.store.set_local(pid, name, busy=False, error="%s: %s" % (type(e).__name__, e))
