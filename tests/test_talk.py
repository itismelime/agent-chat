import tempfile
import threading
import time
import unittest
from pathlib import Path

from agentchat import models, talk
from agentchat.store import Store
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
