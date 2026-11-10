import unittest

from src.passwd_util import is_strong


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertTrue(is_strong('Abcdefg1'))
        self.assertFalse(is_strong('Abcdefgh'))
        self.assertFalse(is_strong('abcdefg1'))
        self.assertFalse(is_strong('ABCDEFG1'))
        self.assertFalse(is_strong('Ab1'))


if __name__ == "__main__":
    unittest.main()
