import unittest

from src.text_stats import count_words


class TestSpec(unittest.TestCase):
    def test_spec(self):
        self.assertEqual(count_words('Hello hello world'), {'hello': 2, 'world': 1})
        self.assertEqual(count_words('A a a'), {'a': 3})
        self.assertEqual(count_words(''), {})
        self.assertEqual(count_words('one'), {'one': 1})


if __name__ == "__main__":
    unittest.main()
