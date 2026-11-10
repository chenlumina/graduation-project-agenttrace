import unittest

from src.nested_util import flatten


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(flatten([1, [2, [3, [4]]], 5]), [1, 2, 3, 4, 5])
        self.assertEqual(flatten([[1, 2], [3]]), [1, 2, 3])
        self.assertEqual(flatten([]), [])
        self.assertEqual(flatten([1, 2]), [1, 2])


if __name__ == "__main__":
    unittest.main()
