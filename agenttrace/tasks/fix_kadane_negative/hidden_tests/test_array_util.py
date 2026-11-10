import unittest

from src.array_util import max_subarray_sum


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(max_subarray_sum([-3, -1, -2]), -1)
        self.assertEqual(max_subarray_sum([1, -2, 3, 4]), 7)
        self.assertEqual(max_subarray_sum([-2, 1, -3, 4, -1, 2, 1, -5, 4]), 6)
        self.assertEqual(max_subarray_sum([5]), 5)


if __name__ == "__main__":
    unittest.main()
