import unittest

from src.sum_upto import sum_upto


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(sum_upto(1), 1)
        self.assertEqual(sum_upto(5), 15)
        self.assertEqual(sum_upto(10), 55)
        self.assertEqual(sum_upto(0), 0)


if __name__ == "__main__":
    unittest.main()
