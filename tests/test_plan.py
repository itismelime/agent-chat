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


if __name__ == "__main__":
    unittest.main()
