import base64
import unittest
import zlib
import struct

from bullpen.client import ApiError, Client
from bullpen.mcp import Session, handle
from tests.helpers import start, stop


def png():
    """A 1x1 PNG, made here."""
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d))
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00")) + chunk(b"IEND", b""))


class AvatarsTest(unittest.TestCase):
    def setUp(self):
        self.store, self.server, self.port, self.tmp = start(wait_seconds=1)
        self.addCleanup(stop, self.server)
        (self.tmp / "proj").mkdir()
        self.store.add_project(str(self.tmp / "proj"))
        self.store.join("proj", "alice", "claude")
        self.c = Client(self.port)
        self.base = "/api/projects/proj/avatars/"

    def test_upload_show_rename_remove(self):
        data = "data:image/png;base64," + base64.b64encode(png()).decode()
        self.c.call("POST", self.base + "alice", {"data": data})
        self.c.call("POST", self.base + "user", {"data": data})
        got = self.c.call("GET", "/api/projects/proj/agents")[1]["avatars"]
        self.assertEqual(set(got), {"alice", "user"})
        self.c.call("POST", "/api/projects/proj/agents/alice/rename", {"name": "ally"})
        self.assertEqual(set(self.c.call("GET", "/api/projects/proj/agents")[1]["avatars"]), {"ally", "user"})
        self.c.call("POST", self.base + "ally/delete", {})
        self.assertEqual(set(self.c.call("GET", "/api/projects/proj/agents")[1]["avatars"]), {"user"})

    def test_only_pictures(self):
        svg = base64.b64encode(b'<svg xmlns="http://www.w3.org/2000/svg"><script>x</script></svg>').decode()
        for body, code in (({"data": svg}, 415), ({"data": "not base64!"}, 400), ({"data": 3}, 400)):
            with self.assertRaises(ApiError, msg=body) as e:
                self.c.call("POST", self.base + "alice", body)
            self.assertEqual(e.exception.code, code)
        with self.assertRaises(ApiError) as e:
            self.c.call("POST", self.base + "nobody", {"data": base64.b64encode(png()).decode()})
        self.assertEqual(e.exception.code, 404)

    def test_an_agent_sets_its_own(self):
        (self.tmp / "proj" / "me.png").write_bytes(png())
        s = Session(self.c, str(self.tmp / "proj"))
        handle(s, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"clientInfo": {"name": "claude-code"}}})
        s.call("chat_join", {"name": "bob"})
        self.assertEqual(s.call("chat_avatar", {"path": "me.png"}), ("your picture is set", False))
        self.assertTrue(s.call("chat_avatar", {"path": "missing.png"})[1])
        self.assertIn("bob", self.c.call("GET", "/api/projects/proj/agents")[1]["avatars"])


if __name__ == "__main__":
    unittest.main()
