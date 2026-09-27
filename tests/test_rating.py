import time
import unittest
from datetime import datetime, timezone

from agentchat import rating

GIB = rating.GIB
NOW = datetime.now(timezone.utc).isoformat()
CHAT = {"id": "example/chat-GGUF", "downloads": 100000, "likes": 500, "trendingScore": 50,
        "lastModified": NOW, "siblings": [
            {"rfilename": "model-Q8_0.gguf", "size": 12 * GIB},
            {"rfilename": "model-Q5_K_M.gguf", "size": 8 * GIB},
            {"rfilename": "model-Q4_K_M.gguf", "size": 6 * GIB},
            {"rfilename": "model-mmproj-Q8_0.gguf", "size": GIB},
            {"rfilename": "model-Q8_0-mtp.gguf", "size": GIB},
            {"rfilename": "model-Q8_0-00001-of-00002.gguf", "size": 6 * GIB},
            {"rfilename": "sub/model-F16.gguf", "size": GIB}]}


class RatingTest(unittest.TestCase):
    # the first cases are local-ai-chat's test-recommendations.mjs, ported
    def test_best_file_fits_and_has_the_best_quant(self):
        self.assertEqual(rating.best_file(CHAT, 11 * GIB)["rfilename"], "model-Q5_K_M.gguf")

    def test_rating_of_a_good_model(self):
        r = rating.rate(CHAT, 11 * GIB)
        self.assertEqual(r["file"], "model-Q5_K_M.gguf")
        self.assertGreaterEqual(r["score"], 70)
        self.assertEqual(r["name"], "chat:q5_k_m")
        self.assertEqual(r["url"], "https://huggingface.co/example/chat-GGUF/resolve/main/model-Q5_K_M.gguf")

    def test_oversized_scores_lower(self):
        huge = dict(CHAT, siblings=[{"rfilename": "huge-Q8_0.gguf", "size": 30 * GIB}])
        r = rating.rate(huge, 10 * GIB)
        self.assertEqual(r["verdict"], "Won't fit GPU memory")
        self.assertLess(r["score"], rating.rate(CHAT, 11 * GIB)["score"])

    def test_files_in_folders_are_not_picked(self):
        only_sub = dict(CHAT, siblings=[{"rfilename": "sub/model-Q4_K_M.gguf", "size": GIB}])
        self.assertIsNone(rating.best_file(only_sub, 16 * GIB))
        self.assertEqual(rating.rate(only_sub, 16 * GIB)["label"], "Poor")

    def test_fit_boundaries(self):
        gpu = 100 * GIB
        cases = [(0.55, 45, "Plenty of room"), (0.75, 40, "Comfortable"), (0.9, 33, "Good fit"),
                 (1.0, 20, "Tight fit"), (1.01, 0, "Won't fit GPU memory")]
        for ratio, points, verdict in cases:
            self.assertEqual(rating.fit(ratio * gpu / rating.MARGIN, gpu), (points, verdict), ratio)
        self.assertEqual(rating.fit(0, gpu), (10, "Unknown fit"))
        self.assertEqual(rating.fit(GIB, 0), (10, "Unknown fit"))

    def test_quant_table(self):
        for name, q in (("x-F16.gguf", 100), ("x-Q8_0.gguf", 92), ("x-Q6_K.gguf", 86),
                        ("x-Q5_K_M.gguf", 82), ("x-Q4_K_M.gguf", 73), ("x-IQ4_XS.gguf", 68),
                        ("x-Q3_K_L.gguf", 57), ("x-Q2_K.gguf", 46), ("x.gguf", 60)):
            self.assertEqual(rating.quant_quality(name), q, name)

    def test_freshness_and_community(self):
        now = time.time()
        day = lambda d: datetime.fromtimestamp(now - d * 86400, timezone.utc).isoformat()
        for days, points in ((1, 10), (100, 8), (300, 6), (700, 3), (1000, 1)):
            self.assertEqual(rating.freshness(day(days), now), points, days)
        self.assertEqual(rating.freshness(None, now), 2)
        self.assertEqual(rating.community({}), 0)
        self.assertLessEqual(rating.community({"downloads": 10 ** 9, "likes": 10 ** 6,
                                               "trendingScore": 10 ** 6}), 20)

    def test_iq1_ranks_lowest_and_helper_files_are_skipped(self):
        self.assertEqual(rating.quant_quality("m-IQ1_M.gguf"), 30)
        coder = dict(CHAT, siblings=[{"rfilename": "c-UD-IQ1_M.gguf", "size": 7 * GIB},
                                     {"rfilename": "c-Q4_K_M.gguf", "size": 12 * GIB}])
        self.assertEqual(rating.best_file(coder, 16 * GIB)["rfilename"], "c-Q4_K_M.gguf")
        oss = dict(CHAT, siblings=[{"rfilename": "eagle3-gpt-oss-20b-BF16.gguf", "size": GIB},
                                   {"rfilename": "x-DRAFT-0.5B.f16.gguf", "size": GIB},
                                   {"rfilename": "model-dflash.gguf", "size": GIB},
                                   {"rfilename": "gpt-oss-20b-Q8_0.gguf", "size": 12 * GIB}])
        self.assertEqual(rating.best_file(oss, 16 * GIB)["rfilename"], "gpt-oss-20b-Q8_0.gguf")

    def test_suggest_name(self):
        self.assertEqual(rating.suggest_name("Qwen/Qwen3.5-9B-GGUF", "Qwen3.5-9B-Q4_K_M.gguf"),
                         "qwen3.5-9b:q4_k_m")
        self.assertEqual(rating.suggest_name("a/Weird Name!", "w.gguf"), "weird-name")


if __name__ == "__main__":
    unittest.main()
