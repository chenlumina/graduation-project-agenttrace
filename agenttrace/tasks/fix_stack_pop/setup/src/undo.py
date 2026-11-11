"""撤销管理：undo() 必须撤销最近一次记录的操作（LIFO）。"""
from stack import Stack

class UndoManager:
    def __init__(self):
        self._history = Stack()

    def record(self, action):
        self._history.push(action)

    def undo(self):
        if self._history.size() == 0:
            return None
        return self._history.pop()
