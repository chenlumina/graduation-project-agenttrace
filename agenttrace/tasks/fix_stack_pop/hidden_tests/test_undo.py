import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import unittest
from undo import UndoManager
from stack import Stack

class TestUndo(unittest.TestCase):
    def test_undo_returns_latest(self):
        m = UndoManager()
        m.record("a")
        m.record("b")
        m.record("c")
        self.assertEqual(m.undo(), "c")
        self.assertEqual(m.undo(), "b")
        self.assertEqual(m.undo(), "a")
        self.assertIsNone(m.undo())

    def test_stack_is_lifo(self):
        s = Stack()
        s.push(1)
        s.push(2)
        self.assertEqual(s.pop(), 2)
        self.assertEqual(s.pop(), 1)
