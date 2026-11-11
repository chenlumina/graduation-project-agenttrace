import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
from config import deep_merge

class TestDeepMerge(unittest.TestCase):
    def test_nested_keys_kept(self):
        merged = deep_merge(
            {"server": {"host": "a", "port": 1, "debug": False}},
            {"server": {"port": 2}},
        )
        self.assertEqual(merged["server"], {"host": "a", "port": 2, "debug": False})

    def test_user_wins(self):
        merged = deep_merge({"a": 1}, {"a": 2})
        self.assertEqual(merged["a"], 2)

    def test_new_keys_added(self):
        merged = deep_merge({"a": 1}, {"b": {"c": 2, "d": 3}})
        self.assertEqual(merged["b"], {"c": 2, "d": 3})
