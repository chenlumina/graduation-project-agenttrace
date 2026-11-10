import unittest

from src.math_util import clamp


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(clamp(5, 0, 3), 3)
        self.assertEqual(clamp(-1, 0, 3), 0)
        self.assertEqual(clamp(2, 0, 3), 2)
        self.assertEqual(clamp(3, 0, 3), 3)


if __name__ == "__main__":
    unittest.main()
