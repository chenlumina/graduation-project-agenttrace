import unittest

from src.utils import is_leap_year


class TestLeapYear(unittest.TestCase):
    def test_common_years(self):
        self.assertTrue(is_leap_year(2024))
        self.assertFalse(is_leap_year(2023))

    def test_century_years(self):
        self.assertFalse(is_leap_year(1900))
        self.assertFalse(is_leap_year(2100))

    def test_quadricentennial_years(self):
        self.assertTrue(is_leap_year(2000))
        self.assertTrue(is_leap_year(1600))


if __name__ == "__main__":
    unittest.main()
