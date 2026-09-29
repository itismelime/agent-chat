import unittest

from bullpen import pins, rules
from bullpen.client import ApiError, Client
from tests.helpers import start, stop


class PinsTest(unittest.TestCase):
    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        (self.tmp / "proj").mkdir()
        self.store.add_project(str(self.tmp / "proj"))
        self.store.join("proj", "alice", "claude")
        self.c = Client(self.port)

    def pin(self, n):
        return self.c.call("POST", "/api/projects/proj/messages/%d/pin" % n, {})[1]["pins"]

    def test_pin_unpin_and_agents_get_them(self):
        self.store.post("proj", "alice", "@user heads-up for prod: don't run deploy disable")
        self.assertEqual(self.pin(1), [1])
        self.assertEqual(self.c.call("GET", "/api/projects/proj/messages")[1]["pins"], [1])
        want = "Pinned in this chat (keep them in mind): #1 alice: @user heads-up for prod: don't run deploy disable"
        rules.add(self.store, "proj", "Link every PR")
        self.assertEqual(rules.standing(self.store, "proj"),
                         "Project rules (follow them): 1. Link every PR " + want)
        self.store.post("proj", "user", "@alice hi")
        self.assertIn(want, self.c.call("GET", "/api/projects/proj/agents/alice/wait")[1]["rules"])
        self.assertEqual(self.pin(1), [])  # again: unpinned
        self.assertEqual(pins.summary(self.store, "proj"), "")
        notes = [m["text"] for m in self.store.messages("proj") if m["from"] == "board"]
        self.assertTrue(notes[-1].startswith("📌 unpinned #1 (alice)"))
        with self.assertRaises(ApiError) as e:
            self.pin(99)
        self.assertEqual(e.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
