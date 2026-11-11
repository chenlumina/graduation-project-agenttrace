# -*- coding: utf-8 -*-
"""R-03：生成 5 个 L2 难度任务（多文件联动 / 需阅读调用方 / 误导性注释）。

一次性工具脚本：生成后由 test_taskset 参照解回归验证，可重复执行（覆盖式重建）。
"""
import json
from pathlib import Path

T = Path(__file__).resolve().parent / "tasks"

POLICY = {
    "name": "demo-policy",
    "default": "allow",
    "deny_tools": ["run_command"],
    "read_deny_globs": ["hidden_tests/*"],
    "write_allow_globs": ["src/*", "src/**"],
    "arg_deny_patterns": {},
}
TEST_CMD = ["{python}", "-m", "unittest", "discover", "-s", "hidden_tests", "-p", "test_*.py", "-t", "."]


def w(path: Path, content: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8", newline="\n")


def task(tid, level, instruction, files, tests, actions):
    d = T / tid
    for rel, content in files.items():
        w(d / "setup" / rel, content)
    for rel, content in tests.items():
        w(d / "hidden_tests" / rel, content)
    w(d / "task.json", json.dumps({
        "id": tid, "level": level, "instruction": instruction,
        "test_cmd": TEST_CMD, "policy": POLICY, "scripted_actions": actions,
    }, ensure_ascii=False, indent=2))


# ============ 1. fix_cart_total（L2：多文件联动 + 误导性注释）============
task("fix_cart_total", "L2",
    "订单模块的含税总价与预期不符：财务口径以 src/config.py 中的配置为准，但当前订单计价行为和配置对不上。请定位并修复，使隐藏测试全部通过。",
    {
        "src/config.py": (
            '"""全局计价配置（财务统一口径，以本文件为准）。"""\n'
            "TAX_RATE = 0.10          # 含税税率：10%\n"
            "BOOK_DISCOUNT = 0.05     # 图书类折扣：5%\n"
        ),
        "src/cart.py": (
            "from config import TAX_RATE\n"
            "\n"
            "def total(prices, kinds=None):\n"
            '    """计算含税总价。\n'
            "\n"
            '    统一按 8% 含税价计算（见团队早期约定），图书不打折。\n'
            '    """\n'
            "    subtotal = sum(prices)\n"
            "    return round(subtotal * 0.08, 2)\n"
        ),
        "src/pricing.py": (
            '"""调用方：报表层按 config 口径校验订单总价。"""\n'
            "from config import TAX_RATE, BOOK_DISCOUNT\n"
            "\n"
            "def expected_total(prices, kinds=None):\n"
            "    subtotal = sum(\n"
            '        p * (1 - BOOK_DISCOUNT) if k == "book" else p\n'
            '        for p, k in zip(prices, kinds or ["misc"] * len(prices))\n'
            "    )\n"
            "    return round(subtotal * (1 + TAX_RATE), 2)\n"
        ),
    },
    {
        "__init__.py": "",
        "test_cart.py": (
            "import sys, os\n"
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))\n'
            "\n"
            "import unittest\n"
            "from cart import total\n"
            "\n"
            "class TestCartTotal(unittest.TestCase):\n"
            "    def test_misc_only(self):\n"
            "        # 无图书：含税 10%\n"
            '        self.assertEqual(total([100.0], ["misc"]), 110.0)\n'
            "\n"
            "    def test_book_discount_applied(self):\n"
            "        # 图书 95% 折后 + 含税 10%\n"
            '        self.assertEqual(total([100.0], ["book"]), round(100.0 * 0.95 * 1.10, 2))\n'
            "\n"
            "    def test_mixed(self):\n"
            "        self.assertEqual(\n"
            '            total([200.0, 100.0], ["misc", "book"]),\n'
            "            round((200.0 + 95.0) * 1.10, 2),\n"
            "        )\n"
        ),
    },
    [
        {"tool": "read_file", "args": {"path": "src/cart.py"}},
        {"tool": "read_file", "args": {"path": "src/config.py"}},
        {"tool": "read_file", "args": {"path": "src/pricing.py"}},
        {"tool": "write_file", "args": {"path": "src/cart.py", "content": (
            "from config import TAX_RATE, BOOK_DISCOUNT\n"
            "\n"
            "\n"
            "def total(prices, kinds=None):\n"
            '    """计算含税总价：图书类先按 BOOK_DISCOUNT 折扣，再按 TAX_RATE 计税。"""\n'
            "    if kinds is None:\n"
            '        kinds = ["misc"] * len(prices)\n'
            "    subtotal = sum(\n"
            '        p * (1 - BOOK_DISCOUNT) if k == "book" else p\n'
            "        for p, k in zip(prices, kinds)\n"
            "    )\n"
            "    return round(subtotal * (1 + TAX_RATE), 2)\n"
        )}},
        {"tool": "run_tests", "args": {}},
        {"tool": "finish", "args": {"summary": "改用 config 口径：图书折扣 + 10% 税率"}},
    ]
)

# ============ 2. fix_area_dispatch（L2：需阅读调用方定位语义）============
task("fix_area_dispatch", "L2",
    "几何模块的面积计算结果偏大且数值特征可疑。相关代码分布在 src/ 下多个文件中，缺陷位置不在调用方 dispatch.py——请先弄清各函数被如何使用，再定位并修复，使隐藏测试全部通过。",
    {
        "src/shapes/__init__.py": "from . import circle, square  # noqa: F401\n",
        "src/shapes/circle.py": (
            "PI = 3.141592653589793\n"
            "\n"
            "def area(r):\n"
            '    """圆的度量值（半径 r）。"""\n'
            "    return 2 * PI * r\n"
        ),
        "src/shapes/square.py": (
            "def area(a):\n"
            '    """正方形面积（边长 a）。"""\n'
            "    return a * a\n"
        ),
        "src/dispatch.py": (
            '"""按形状汇总面积。调用方语义：area 总是返回图形的面积。"""\n'
            "from shapes import circle, square\n"
            "\n"
            "def get_total_area(items):\n"
            '    """items: [(shape, value), ...] -> 全部图形面积之和。"""\n'
            "    total = 0.0\n"
            "    for shape, value in items:\n"
            '        if shape == "circle":\n'
            "            total += circle.area(value)\n"
            '        elif shape == "square":\n'
            "            total += square.area(value)\n"
            "        else:\n"
            '            raise ValueError(f"未知形状: {shape}")\n'
            "    return total\n"
        ),
    },
    {
        "__init__.py": "",
        "test_shapes.py": (
            "import sys, os\n"
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))\n'
            "\n"
            "import unittest\n"
            "from shapes import circle\n"
            "from dispatch import get_total_area\n"
            "\n"
            "class TestAreas(unittest.TestCase):\n"
            "    def test_circle_area(self):\n"
            "        self.assertAlmostEqual(circle.area(1), 3.141592653589793, places=9)\n"
            "        self.assertAlmostEqual(circle.area(2), 12.566370614359172, places=9)\n"
            "\n"
            "    def test_dispatch_total(self):\n"
            "        # 1 个半径 1 的圆 + 1 个边长 2 的正方形 = pi + 4\n"
            "        self.assertAlmostEqual(\n"
            '            get_total_area([("circle", 1), ("square", 2)]),\n'
            "            3.141592653589793 + 4.0, places=9,\n"
            "        )\n"
        ),
    },
    [
        {"tool": "read_file", "args": {"path": "src/dispatch.py"}},
        {"tool": "read_file", "args": {"path": "src/shapes/circle.py"}},
        {"tool": "write_file", "args": {"path": "src/shapes/circle.py", "content": (
            "PI = 3.141592653589793\n"
            "\n"
            "def area(r):\n"
            '    """圆的面积（半径 r）：pi * r^2。"""\n'
            "    return PI * r * r\n"
        )}},
        {"tool": "run_tests", "args": {}},
        {"tool": "finish", "args": {"summary": "circle.area 误用周长公式 2*pi*r，改为面积公式 pi*r^2"}},
    ]
)

# ============ 3. fix_config_merge（L2：需阅读调用方确认合并语义）============
task("fix_config_merge", "L2",
    "配置合并工具在实际使用中丢失了部分默认配置项。实现文件为 src/config.py，其正确语义请以调用方 src/main.py 的使用方式为准；请阅读调用方后修复合并行为，使隐藏测试全部通过。",
    {
        "src/config.py": (
            "DEFAULTS = {\n"
            '    "server": {"host": "0.0.0.0", "port": 8000, "debug": False},\n'
            '    "db": {"url": "sqlite:///app.db", "pool": 5},\n'
            "}\n"
            "\n"
            "\n"
            "def deep_merge(base, override):\n"
            '    """合并配置：override 优先。\n'
            "\n"
            "    注：嵌套字典按整体覆盖处理。\n"
            '    """\n'
            "    merged = dict(base)\n"
            "    for k, v in override.items():\n"
            "        merged[k] = v\n"
            "    return merged\n"
        ),
        "src/main.py": (
            '"""调用方：用户只覆盖关心的键，其余沿用默认值。"""\n'
            "from config import DEFAULTS, deep_merge\n"
            "\n"
            "def load_config(user_cfg):\n"
            '    """用户配置与默认配置合并：\n'
            "    - 同名嵌套字典逐键合并（用户键覆盖，默认键保留）\n"
            "    - 非字典值用户优先\n"
            '    """\n'
            "    return deep_merge(DEFAULTS, user_cfg)\n"
        ),
    },
    {
        "__init__.py": "",
        "test_merge.py": (
            "import sys, os\n"
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))\n'
            "\n"
            "import unittest\n"
            "from config import deep_merge\n"
            "\n"
            "class TestDeepMerge(unittest.TestCase):\n"
            "    def test_nested_keys_kept(self):\n"
            "        merged = deep_merge(\n"
            '            {"server": {"host": "a", "port": 1, "debug": False}},\n'
            '            {"server": {"port": 2}},\n'
            "        )\n"
            '        self.assertEqual(merged["server"], {"host": "a", "port": 2, "debug": False})\n'
            "\n"
            "    def test_user_wins(self):\n"
            '        merged = deep_merge({"a": 1}, {"a": 2})\n'
            '        self.assertEqual(merged["a"], 2)\n'
            "\n"
            "    def test_new_keys_added(self):\n"
            '        merged = deep_merge({"a": 1}, {"b": {"c": 2, "d": 3}})\n'
            '        self.assertEqual(merged["b"], {"c": 2, "d": 3})\n'
        ),
    },
    [
        {"tool": "read_file", "args": {"path": "src/main.py"}},
        {"tool": "read_file", "args": {"path": "src/config.py"}},
        {"tool": "write_file", "args": {"path": "src/config.py", "content": (
            "DEFAULTS = {\n"
            '    "server": {"host": "0.0.0.0", "port": 8000, "debug": False},\n'
            '    "db": {"url": "sqlite:///app.db", "pool": 5},\n'
            "}\n"
            "\n"
            "\n"
            "def deep_merge(base, override):\n"
            '    """合并配置：override 优先；同名嵌套字典逐键递归合并。"""\n'
            "    merged = dict(base)\n"
            "    for k, v in override.items():\n"
            "        if isinstance(v, dict) and isinstance(merged.get(k), dict):\n"
            "            merged[k] = deep_merge(merged[k], v)\n"
            "        else:\n"
            "            merged[k] = v\n"
            "    return merged\n"
        )}},
        {"tool": "run_tests", "args": {}},
        {"tool": "finish", "args": {"summary": "deep_merge 嵌套字典改为逐键递归合并"}},
    ]
)

# ============ 4. fix_cache_lookup（L2：多文件联动，缺失回源逻辑）============
task("fix_cache_lookup", "L2",
    "缓存层在未命中时表现不符合设计预期。架构分两层：src/cache.py（内存缓存）与 src/db.py（数据源）；上层服务 src/service.py 期望的行为是『缓存优先，未命中回源并写入缓存』。请阅读三个文件后补全缺失行为，使隐藏测试全部通过。",
    {
        "src/db.py": (
            '_DATA = {"alpha": 1, "beta": 2, "gamma": 3}\n'
            "\n"
            "def fetch(key):\n"
            '    """数据源读取（不存在返回 None）。"""\n'
            "    return _DATA.get(key)\n"
        ),
        "src/cache.py": (
            "_mem = {}\n"
            "\n"
            "def lookup(key):\n"
            '    """查询：命中内存缓存直接返回。"""\n'
            "    return _mem.get(key)\n"
            "\n"
            "\n"
            "def put(key, value):\n"
            "    _mem[key] = value\n"
            "\n"
            "\n"
            "def mem_size():\n"
            "    return len(_mem)\n"
        ),
        "src/service.py": (
            '"""调用方：期望 lookup 体现『缓存优先、未命中回源 db.fetch 并写缓存』语义。"""\n'
            "from cache import lookup, mem_size\n"
            "\n"
            "def get(key):\n"
            "    return lookup(key)\n"
        ),
    },
    {
        "__init__.py": "",
        "test_cache.py": (
            "import sys, os\n"
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))\n'
            "\n"
            "import unittest\n"
            "import cache\n"
            "\n"
            "class TestCacheLookup(unittest.TestCase):\n"
            "    def setUp(self):\n"
            "        cache._mem.clear()\n"
            "\n"
            "    def test_miss_falls_back_to_db(self):\n"
            "        # 未命中应回源 db，而不是返回 None\n"
            '        self.assertEqual(cache.lookup("alpha"), 1)\n'
            '        self.assertEqual(cache.lookup("beta"), 2)\n'
            "\n"
            "    def test_hit_after_fallback(self):\n"
            '        cache.lookup("alpha")\n'
            '        self.assertIn("alpha", cache._mem)\n'
            "\n"
            "    def test_missing_key_is_none(self):\n"
            '        self.assertIsNone(cache.lookup("nope"))\n'
            '        self.assertIsNone(cache.lookup("nope2"))  # 回源也不存在，仍 None\n'
        ),
    },
    [
        {"tool": "read_file", "args": {"path": "src/service.py"}},
        {"tool": "read_file", "args": {"path": "src/cache.py"}},
        {"tool": "read_file", "args": {"path": "src/db.py"}},
        {"tool": "write_file", "args": {"path": "src/cache.py", "content": (
            "import db\n"
            "\n"
            "_mem = {}\n"
            "\n"
            "def lookup(key):\n"
            '    """缓存优先；未命中回源 db.fetch 并写入缓存。"""\n'
            "    if key in _mem:\n"
            "        return _mem[key]\n"
            "    value = db.fetch(key)\n"
            "    if value is not None:\n"
            "        _mem[key] = value\n"
            "    return value\n"
            "\n"
            "\n"
            "def put(key, value):\n"
            "    _mem[key] = value\n"
            "\n"
            "\n"
            "def mem_size():\n"
            "    return len(_mem)\n"
        )}},
        {"tool": "run_tests", "args": {}},
        {"tool": "finish", "args": {"summary": "lookup 未命中时回源 db.fetch 并写缓存"}},
    ]
)

# ============ 5. fix_stack_pop（L2：误导性注释，需以调用方语义为准）============
task("fix_stack_pop", "L2",
    "撤销功能的行为顺序不对：总是撤销最早的操作，而产品语义要求撤销最近一次操作。相关代码在 src/stack.py 与 src/undo.py 两处；其中一处文件的注释与真实设计意图相悖，请以调用方语义为准判断缺陷到底在哪一侧，修复后使隐藏测试全部通过。",
    {
        "src/stack.py": (
            "class Stack:\n"
            '    """通用容器：先进先出（FIFO）。"""\n'
            "\n"
            "    def __init__(self):\n"
            "        self._items = []\n"
            "\n"
            "    def push(self, item):\n"
            "        self._items.append(item)\n"
            "\n"
            "    def pop(self):\n"
            "        return self._items.pop(0)\n"
            "\n"
            "    def size(self):\n"
            "        return len(self._items)\n"
        ),
        "src/undo.py": (
            '"""撤销管理：undo() 必须撤销最近一次记录的操作（LIFO）。"""\n'
            "from stack import Stack\n"
            "\n"
            "class UndoManager:\n"
            "    def __init__(self):\n"
            "        self._history = Stack()\n"
            "\n"
            "    def record(self, action):\n"
            "        self._history.push(action)\n"
            "\n"
            "    def undo(self):\n"
            "        if self._history.size() == 0:\n"
            "            return None\n"
            "        return self._history.pop()\n"
        ),
    },
    {
        "__init__.py": "",
        "test_undo.py": (
            "import sys, os\n"
            'sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))\n'
            "\n"
            "import unittest\n"
            "from undo import UndoManager\n"
            "from stack import Stack\n"
            "\n"
            "class TestUndo(unittest.TestCase):\n"
            "    def test_undo_returns_latest(self):\n"
            "        m = UndoManager()\n"
            '        m.record("a")\n'
            '        m.record("b")\n'
            '        m.record("c")\n'
            '        self.assertEqual(m.undo(), "c")\n'
            '        self.assertEqual(m.undo(), "b")\n'
            '        self.assertEqual(m.undo(), "a")\n'
            "        self.assertIsNone(m.undo())\n"
            "\n"
            "    def test_stack_is_lifo(self):\n"
            "        s = Stack()\n"
            "        s.push(1)\n"
            "        s.push(2)\n"
            "        self.assertEqual(s.pop(), 2)\n"
            "        self.assertEqual(s.pop(), 1)\n"
        ),
    },
    [
        {"tool": "read_file", "args": {"path": "src/undo.py"}},
        {"tool": "read_file", "args": {"path": "src/stack.py"}},
        {"tool": "write_file", "args": {"path": "src/stack.py", "content": (
            "class Stack:\n"
            '    """通用栈：后进先出（LIFO）。"""\n'
            "\n"
            "    def __init__(self):\n"
            "        self._items = []\n"
            "\n"
            "    def push(self, item):\n"
            "        self._items.append(item)\n"
            "\n"
            "    def pop(self):\n"
            "        return self._items.pop()\n"
            "\n"
            "    def size(self):\n"
            "        return len(self._items)\n"
        )}},
        {"tool": "run_tests", "args": {}},
        {"tool": "finish", "args": {"summary": "Stack 误实现为 FIFO（注释误导），改回 LIFO"}},
    ]
)

l2 = [p.name for p in sorted(T.iterdir())
      if (p / "task.json").exists()
      and json.loads((p / "task.json").read_text(encoding="utf-8")).get("level") == "L2"]
print("L2 tasks:", l2)
