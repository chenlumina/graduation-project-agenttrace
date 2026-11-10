import unittest

from src.stats_util import average


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(average([2, 4, 6]), 4.0)
        self.assertEqual(average([]), 0.0)
        self.assertEqual(average([5]), 5.0)
        self.assertEqual(average([1, 2]), 1.5)


if __name__ == "__main__":
    unittest.main()
