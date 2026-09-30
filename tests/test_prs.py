import json
import subprocess
import unittest

from bullpen import prs
from bullpen.store import StoreError


class PrsTest(unittest.TestCase):
    def test_checks(self):
        ok, bad = {"conclusion": "SUCCESS"}, {"conclusion": "FAILURE"}
        self.assertIsNone(prs.checks([]))
        self.assertEqual(prs.checks([ok, {"conclusion": "SKIPPED"}]), "passed")
        self.assertEqual(prs.checks([ok, {"status": "IN_PROGRESS", "conclusion": ""}]), "running")
        self.assertEqual(prs.checks([ok, bad]), "failed")
        self.assertEqual(prs.checks([{"state": "SUCCESS"}]), "passed")  # a commit status, not a check run

    def test_state_through_gh_and_cached(self):
        calls = []
        old = prs.shutil.which
        prs.shutil.which = lambda name: "/usr/bin/" + name  # as if gh were installed
        self.addCleanup(setattr, prs.shutil, "which", old)

        def run(args, **kw):
            calls.append(args)
            out = {"state": "OPEN", "isDraft": True, "title": "Epics",
                   "statusCheckRollup": [{"name": "tests", "conclusion": "SUCCESS"}]}
            return subprocess.CompletedProcess(args, 0, json.dumps(out), "")
        prs._cache.clear()
        s = prs.state("itismelime/bullpen", "3", run=run)
        self.assertEqual((s["state"], s["checks"], s["detail"]), ("draft", "passed", [("tests", "success")]))
        prs.state("itismelime/bullpen", "3", run=run)
        self.assertEqual(len(calls), 1)  # cached
        self.assertEqual(calls[0][:5], ["gh", "pr", "view", "3", "-R"])
        for repo, n in (("a b/c", "1"), ("owner/repo", "x"), ("owner/repo;rm", "1"), (None, "1")):
            with self.assertRaises(StoreError, msg=(repo, n)):
                prs.state(repo, n, run=run)


if __name__ == "__main__":
    unittest.main()
