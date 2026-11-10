import unittest

from src.text_util import is_palindrome


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertTrue(is_palindrome('Level'))
        self.assertTrue(is_palindrome('Never odd or even'))
        self.assertFalse(is_palindrome('hello'))
        self.assertTrue(is_palindrome('AbBa'))


if __name__ == "__main__":
    unittest.main()
