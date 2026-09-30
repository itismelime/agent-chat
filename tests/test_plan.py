import json
import tempfile
import time
import unittest
from unittest import mock

from bullpen import plan


class PlanTest(unittest.TestCase):
    def test_keep_read_and_show_the_windows(self):
        root = tempfile.mkdtemp()
        soon = time.time() + 2 * 3600 + 30 * 60 + 30
        raw = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 6.4, "resets_at": soon},
                                          "seven_day": {"used_percentage": 31, "resets_at": soon}}})
        limits = plan.capture(raw, root)
        got = plan.get(root)
        self.assertEqual((got["five_hour"]["used"], got["seven_day"]["used"]), (6, 31))
        self.assertEqual(plan.line(limits), "5h 6% (2h 30m left) | 7d 31% (2h 30m left)")
        # a statusline input without limits (an API key, not a plan) keeps what was there
        self.assertIsNone(plan.capture(json.dumps({"model": {}}), root))
        self.assertIsNone(plan.capture("not json", root))
        self.assertEqual(plan.get(root)["five_hour"]["used"], 6)
        self.assertIsNone(plan.get(tempfile.mkdtemp()))

    def test_never_runs_itself_as_your_statusline(self):
        with mock.patch.object(plan.Path, "read_text",
                               return_value=json.dumps({"statusLine": {"command": "python3 x/bullpen statusline"}})):
            self.assertIsNone(plan.yours())
        with mock.patch.object(plan.Path, "read_text",
                               return_value=json.dumps({"statusLine": {"command": "bash ~/mine.sh"}})):
            self.assertEqual(plan.yours(), "bash ~/mine.sh")

    def test_the_command_claude_runs(self):
        import os, subprocess, sys
        from pathlib import Path
        home = tempfile.mkdtemp()  # no settings of yours there: bullpen's own line
        env = dict(os.environ, HOME=home, XDG_DATA_HOME=home)
        raw = json.dumps({"rate_limits": {"five_hour": {"used_percentage": 12, "resets_at": time.time() + 3600}}})
        r = subprocess.run([sys.executable, str(Path(__file__).parent.parent / "bin" / "bullpen"), "statusline"],
                           input=raw, capture_output=True, text=True, env=env, timeout=30)
        self.assertEqual(r.returncode, 0, r.stderr)
        self.assertTrue(r.stdout.startswith("5h 12% ("), r.stdout)
        self.assertEqual(plan.get(Path(home) / "bullpen")["five_hour"]["used"], 12)


    def test_through_the_night(self):
        root, now = tempfile.mkdtemp(), 1000000.0
        keep = lambda five, seven: plan.capture(json.dumps({"rate_limits": {
            "five_hour": {"used_percentage": five, "resets_at": now + 3600},
            "seven_day": {"used_percentage": seven, "resets_at": now + 86400}}}), root)
        keep(50, 30)
        self.assertIsNone(plan.held_until(root, now))
        self.assertIsNone(plan.arm(root, now))  # far from a limit: nothing to wake for
        keep(92, 30)  # agents wrap up from here
        self.assertEqual(plan.arm(root, now), now + 3600)
        self.assertIsNone(plan.held_until(root, now))  # not used up: messages still go
        keep(100, 30)
        self.assertEqual(plan.held_until(root, now), now + 3600)
        self.assertIsNone(plan.held_until(root, now + 3601))  # reset: it goes again
        self.assertIsNone(plan.due(root, now + 3600))  # a minute's grace
        self.assertEqual(plan.due(root, now + 3700), now + 3600)
        self.assertIsNone(plan.due(root, now + 3800))  # once

if __name__ == "__main__":
    unittest.main()
