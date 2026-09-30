import os
import tempfile
import unittest
from pathlib import Path

from bullpen import removal
from bullpen.store import Store, StoreError


@unittest.skipIf(os.name == "nt", "the Trash here is the freedesktop one")
class RemovalTest(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        self.store = Store(self.tmp / "data")
        (self.tmp / "work" / "shop" / "src").mkdir(parents=True)
        (self.tmp / "work" / "shop" / "src" / "a.py").write_text("x = 1\n")
        self.store.add_project(str(self.tmp / "work" / "shop"))
        old = os.environ.get("XDG_DATA_HOME")
        os.environ["XDG_DATA_HOME"] = str(self.tmp / "share")
        self.addCleanup(lambda: os.environ.pop("XDG_DATA_HOME") if old is None else os.environ.__setitem__("XDG_DATA_HOME", old))

    def test_forget_keeps_the_folder_and_history(self):
        self.store.post("shop", "user", "hi")
        removal.forget(self.store, "shop")
        self.assertEqual(self.store.projects(), [])
        self.assertTrue((self.tmp / "work" / "shop").is_dir())
        p, existing = self.store.add_project(str(self.tmp / "work" / "shop"))
        self.assertEqual(self.store.messages("shop")[0]["text"], "hi")  # back with its history

    def test_trash_needs_the_name_and_moves_the_folder(self):
        i = removal.info(self.store, "shop")
        self.assertEqual((i["files"], i["size"], i["refused"]), (1, 6, None))
        with self.assertRaises(StoreError) as e:
            removal.trash(self.store, "shop", "shoP")
        self.assertEqual(e.exception.code, 400)
        removal.trash(self.store, "shop", "shop")
        self.assertFalse((self.tmp / "work" / "shop").exists())
        files, info = self.tmp / "share" / "Trash" / "files", self.tmp / "share" / "Trash" / "info"
        self.assertTrue((files / "shop" / "src" / "a.py").exists())
        self.assertIn("Path=%s" % (self.tmp / "work" / "shop"), (info / "shop.trashinfo").read_text())
        self.assertEqual(self.store.projects(), [])

    def test_refuses_a_folder_that_holds_another_project(self):
        (self.tmp / "work" / "shop" / "inner").mkdir()
        self.store.add_project(str(self.tmp / "work"))  # the parent, registered too
        self.store.add_project(str(self.tmp / "work" / "shop" / "inner"))
        ids = {p["path"]: p["id"] for p in self.store.projects()}
        parent = ids[str(self.tmp / "work")]
        self.assertIn("holds another project", removal.info(self.store, parent)["refused"])
        with self.assertRaises(StoreError) as e:
            removal.trash(self.store, parent, "work")
        self.assertEqual(e.exception.code, 403)
        self.assertTrue((self.tmp / "work").is_dir())


if __name__ == "__main__":
    unittest.main()
