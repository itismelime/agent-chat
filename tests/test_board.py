import tempfile
import unittest
from pathlib import Path

from agentchat.board import Board
from agentchat.store import Store, StoreError


class BoardTest(unittest.TestCase):
    def setUp(self):
        tmp = Path(tempfile.mkdtemp())
        (tmp / "proj").mkdir()
        self.store = Store(tmp / "data")
        self.store.add_project(str(tmp / "proj"))
        self.store.join("proj", "kit", "claude")
        self.store.join("proj", "cody", "codex")
        self.board = Board(self.store)
        self.woken = []
        self.store.deliver = None

    def notices(self):
        return [m["text"] for m in self.store.messages("proj") if m["from"] == "board"]

    def test_add_numbers_and_announces(self):
        a = self.board.add("proj", "user", "Fix login")
        b = self.board.add("proj", "kit", "Write tests", description="unit tests", column="doing")
        self.assertEqual((a["id"], b["id"]), (1, 2))
        self.assertEqual(b["column"], "doing")
        self.assertEqual(self.notices(), ['#1 "Fix login" added by user (To do)',
                                          '#2 "Write tests" added by kit (In progress)'])
        cards = self.board.get("proj")["cards"]
        self.assertEqual([c["id"] for c in cards], [1, 2])
        self.assertEqual(cards[1]["created_by"], "kit")

    def test_move_assign_edit_delete(self):
        c = self.board.add("proj", "user", "Fix login")
        self.board.update("proj", c["id"], "user", assignee="kit")
        self.board.update("proj", c["id"], "kit", column="review")
        self.board.update("proj", c["id"], "user", column="done")
        self.board.update("proj", c["id"], "user", title="Fix login page")
        self.board.update("proj", c["id"], "user", column="done")  # no change: no notice
        self.board.delete("proj", c["id"], "user")
        self.assertEqual(self.notices()[1:], [
            '@kit you were assigned #1 "Fix login" by user',
            'kit moved #1 "Fix login" to Review',
            '@kit user moved #1 "Fix login" to Done',
            '@kit user edited #1 "Fix login page"',
            '@kit user deleted #1 "Fix login page"'])
        self.assertEqual(self.board.get("proj")["cards"], [])

    def test_notices_wake_only_the_addressed(self):
        from agentchat.store import wakes
        c = self.board.add("proj", "user", "x")
        self.board.update("proj", c["id"], "user", assignee="cody")
        added, assigned = [m for m in self.store.messages("proj") if m["from"] == "board"]
        self.assertFalse(wakes(added, "kit"))
        self.assertTrue(wakes(assigned, "cody"))
        self.assertFalse(wakes(assigned, "kit"))

    def test_validation(self):
        c = self.board.add("proj", "user", "x")
        bad = [lambda: self.board.add("proj", "user", ""),
               lambda: self.board.add("proj", "user", "x" * 201),
               lambda: self.board.add("proj", "user", "x", description="d" * 4001),
               lambda: self.board.add("proj", "user", "x", column="later"),
               lambda: self.board.add("proj", "user", "x", assignee="ghost"),
               lambda: self.board.add("proj", "ghost", "x"),
               lambda: self.board.update("proj", c["id"], "user", column="nope")]
        for f in bad:
            with self.assertRaises(StoreError) as e:
                f()
            self.assertIn(e.exception.code, (400, 403))
        with self.assertRaises(StoreError) as e:
            self.board.update("proj", 99, "user", column="done")
        self.assertEqual(e.exception.code, 404)
        self.store.remove("proj", "kit")
        with self.assertRaises(StoreError) as e:
            self.board.add("proj", "kit", "x")
        self.assertEqual(e.exception.code, 403)

    def test_titles_are_one_line(self):
        c = self.board.add("proj", "user", "two\nlines")
        self.assertEqual(c["title"], "two lines")

    def test_board_is_a_reserved_name(self):
        with self.assertRaises(StoreError):
            self.store.join("proj", "board", "claude")
        with self.assertRaises(StoreError):
            self.store.post("proj", "board", "pretending")  # only the Board posts as board


if __name__ == "__main__":
    unittest.main()
