import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
from cart import total

class TestCartTotal(unittest.TestCase):
    def test_misc_only(self):
        # 无图书：含税 10%
        self.assertEqual(total([100.0], ["misc"]), 110.0)

    def test_book_discount_applied(self):
        # 图书 95% 折后 + 含税 10%
        self.assertEqual(total([100.0], ["book"]), round(100.0 * 0.95 * 1.10, 2))

    def test_mixed(self):
        self.assertEqual(
            total([200.0, 100.0], ["misc", "book"]),
            round((200.0 + 95.0) * 1.10, 2),
        )
