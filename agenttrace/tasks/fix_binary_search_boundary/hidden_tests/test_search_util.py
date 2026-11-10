import unittest

from src.search_util import binary_search


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(binary_search([1, 3, 5, 7, 9], 9), 4)
        self.assertEqual(binary_search([1, 3, 5], 1), 0)
        self.assertEqual(binary_search([1, 3, 5], 3), 1)
        self.assertEqual(binary_search([1, 3, 5], 4), -1)
        self.assertEqual(binary_search([], 1), -1)


if __name__ == "__main__":
    unittest.main()
