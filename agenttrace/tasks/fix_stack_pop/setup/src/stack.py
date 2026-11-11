class Stack:
    """通用容器：先进先出（FIFO）。"""

    def __init__(self):
        self._items = []

    def push(self, item):
        self._items.append(item)

    def pop(self):
        return self._items.pop(0)

    def size(self):
        return len(self._items)
