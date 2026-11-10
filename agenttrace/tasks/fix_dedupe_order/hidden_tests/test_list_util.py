import unittest

from src.list_util import dedupe


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(dedupe([3, 1, 3, 2, 1]), [3, 1, 2])
        self.assertEqual(dedupe(['b', 'a', 'b']), ['b', 'a'])
        self.assertEqual(dedupe([]), [])
        self.assertEqual(dedupe([1, 1, 1]), [1])


if __name__ == "__main__":
    unittest.main()
