import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
import cache

class TestCacheLookup(unittest.TestCase):
    def setUp(self):
        cache._mem.clear()

    def test_miss_falls_back_to_db(self):
        # 未命中应回源 db，而不是返回 None
        self.assertEqual(cache.lookup("alpha"), 1)
        self.assertEqual(cache.lookup("beta"), 2)

    def test_hit_after_fallback(self):
        cache.lookup("alpha")
        self.assertIn("alpha", cache._mem)

    def test_missing_key_is_none(self):
        self.assertIsNone(cache.lookup("nope"))
        self.assertIsNone(cache.lookup("nope2"))  # 回源也不存在，仍 None
