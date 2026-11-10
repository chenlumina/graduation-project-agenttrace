"""任务集生成脚本：重建 tasks/ 下的任务包（幂等，可重复执行）。

每个任务 = task.json + setup/src/<module>.py（含单一缺陷）+ hidden_tests/（权威测试）。
任务集设计原则：
- 单一缺陷，指令明确描述预期行为；
- 隐藏测试确定性（无随机、无死循环风险），至少覆盖一个边界用例；
- 难度梯度：单行修复 → 多行修复；
- 全部仅依赖 Python 标准库，与沙箱镜像 python:3.13-slim 兼容；
- 每个任务自带脚本参照解（scripted_actions），保证离线可回归。

用法：python tasks/make_tasks.py
"""
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent

TEST_CMD = ["{python}", "-m", "unittest", "discover", "-s", "hidden_tests", "-p", "test_*.py", "-t", "."]

TESTS_TEMPLATE = '''import unittest

from src.{module} import {func}


class TestSpec(unittest.TestCase):
    def test_spec(self):
{cases}


if __name__ == "__main__":
    unittest.main()
'''


def tests_source(module: str, func: str, cases: list[str]) -> str:
    body = "\n".join(f"        {c}" for c in cases)
    return TESTS_TEMPLATE.format(module=module, func=func, cases=body)


def actions(module: str, fixed: str) -> list[dict]:
    return [
        {"tool": "read_file", "args": {"path": f"src/{module}.py"}},
        {"tool": "write_file", "args": {"path": f"src/{module}.py", "content": fixed}},
        {"tool": "run_tests", "args": {}},
        {"tool": "finish", "args": {"summary": "修复完成，隐藏测试全部通过"}},
    ]


SPECS = [
    {
        "id": "fix_range_sum_offbyone",
        "module": "sum_upto",
        "func": "sum_upto",
        "instruction": (
            "src/sum_upto.py 中的 sum_upto(n) 应返回 1 到 n（含 n）的整数和，"
            "但当前实现漏掉了 n 本身（差一错误）。请阅读该文件并修复，使隐藏测试全部通过；"
            "可先用 run_tests 验证。"
        ),
        "bug": (
            "def sum_upto(n: int) -> int:\n"
            '    """返回 1 到 n（含 n）的整数和（存在缺陷：漏掉 n 本身）。"""\n'
            "    return sum(range(1, n))\n"
        ),
        "fixed": (
            "def sum_upto(n: int) -> int:\n"
            '    """返回 1 到 n（含 n）的整数和。"""\n'
            "    return sum(range(1, n + 1))\n"
        ),
        "cases": [
            "self.assertEqual(sum_upto(1), 1)",
            "self.assertEqual(sum_upto(5), 15)",
            "self.assertEqual(sum_upto(10), 55)",
            "self.assertEqual(sum_upto(0), 0)",
        ],
    },
    {
        "id": "fix_average_empty",
        "module": "stats_util",
        "func": "average",
        "instruction": (
            "src/stats_util.py 中的 average(values) 对空列表会抛 ZeroDivisionError。"
            "按约定：空列表应返回 0.0。请阅读该文件并修复，使隐藏测试全部通过；"
            "可先用 run_tests 验证。"
        ),
        "bug": (
            "def average(values: list) -> float:\n"
            '    """返回数值列表的平均值（存在缺陷：空列表会除零）。"""\n'
            "    return sum(values) / len(values)\n"
        ),
        "fixed": (
            "def average(values: list) -> float:\n"
            '    """返回数值列表的平均值；空列表约定返回 0.0。"""\n'
            "    if not values:\n"
            "        return 0.0\n"
            "    return sum(values) / len(values)\n"
        ),
        "cases": [
            "self.assertEqual(average([2, 4, 6]), 4.0)",
            "self.assertEqual(average([]), 0.0)",
            "self.assertEqual(average([5]), 5.0)",
            "self.assertEqual(average([1, 2]), 1.5)",
        ],
    },
    {
        "id": "fix_palindrome_normalize",
        "module": "text_util",
        "func": "is_palindrome",
        "instruction": (
            "src/text_util.py 中的 is_palindrome(s) 按约定应忽略大小写与空白后判断回文，"
            "但当前实现直接比较原串。请阅读该文件并修复，使隐藏测试全部通过；"
            "可先用 run_tests 验证。"
        ),
        "bug": (
            "def is_palindrome(s: str) -> bool:\n"
            '    """判断回文（存在缺陷：未按约定忽略大小写与空白）。"""\n'
            "    return s == s[::-1]\n"
        ),
        "fixed": (
            "def is_palindrome(s: str) -> bool:\n"
            '    """判断回文：忽略大小写与空白。"""\n'
            "    t = s.replace(\" \", \"\").lower()\n"
            "    return t == t[::-1]\n"
        ),
        "cases": [
            "self.assertTrue(is_palindrome('Level'))",
            "self.assertTrue(is_palindrome('Never odd or even'))",
            "self.assertFalse(is_palindrome('hello'))",
            "self.assertTrue(is_palindrome('AbBa'))",
        ],
    },
    {
        "id": "fix_binary_search_boundary",
        "module": "search_util",
        "func": "binary_search",
        "instruction": (
            "src/search_util.py 中的 binary_search(arr, target) 在有序数组中二分查找下标，"
            "找不到返回 -1。当前实现的循环条件漏判了边界元素（如最后一个元素）。"
            "请阅读该文件并修复，使隐藏测试全部通过；可先用 run_tests 验证。"
        ),
        "bug": (
            "def binary_search(arr: list, target) -> int:\n"
            '    """二分查找（存在缺陷：循环条件漏判边界元素）。"""\n'
            "    low, high = 0, len(arr) - 1\n"
            "    while low < high:\n"
            "        mid = (low + high) // 2\n"
            "        if arr[mid] == target:\n"
            "            return mid\n"
            "        if arr[mid] < target:\n"
            "            low = mid + 1\n"
            "        else:\n"
            "            high = mid - 1\n"
            "    return -1\n"
        ),
        "fixed": (
            "def binary_search(arr: list, target) -> int:\n"
            '    """二分查找：返回下标，找不到返回 -1。"""\n'
            "    low, high = 0, len(arr) - 1\n"
            "    while low <= high:\n"
            "        mid = (low + high) // 2\n"
            "        if arr[mid] == target:\n"
            "            return mid\n"
            "        if arr[mid] < target:\n"
            "            low = mid + 1\n"
            "        else:\n"
            "            high = mid - 1\n"
            "    return -1\n"
        ),
        "cases": [
            "self.assertEqual(binary_search([1, 3, 5, 7, 9], 9), 4)",
            "self.assertEqual(binary_search([1, 3, 5], 1), 0)",
            "self.assertEqual(binary_search([1, 3, 5], 3), 1)",
            "self.assertEqual(binary_search([1, 3, 5], 4), -1)",
            "self.assertEqual(binary_search([], 1), -1)",
        ],
    },
    {
        "id": "fix_word_count_case",
        "module": "text_stats",
        "func": "count_words",
        "instruction": (
            "src/text_stats.py 中的 count_words(text) 按约定应大小写不敏感地统计单词频次"
            "（按空白切分），但当前实现区分了大小写。请阅读该文件并修复，"
            "使隐藏测试全部通过；可先用 run_tests 验证。"
        ),
        "bug": (
            "def count_words(text: str) -> dict:\n"
            '    """统计单词频次（存在缺陷：未按约定忽略大小写）。"""\n'
            "    counts: dict = {}\n"
            "    for w in text.split():\n"
            "        counts[w] = counts.get(w, 0) + 1\n"
            "    return counts\n"
        ),
        "fixed": (
            "def count_words(text: str) -> dict:\n"
            '    """统计单词频次：大小写不敏感，按空白切分。"""\n'
            "    counts: dict = {}\n"
            "    for w in text.split():\n"
            "        w = w.lower()\n"
            "        counts[w] = counts.get(w, 0) + 1\n"
            "    return counts\n"
        ),
        "cases": [
            "self.assertEqual(count_words('Hello hello world'), {'hello': 2, 'world': 1})",
            "self.assertEqual(count_words('A a a'), {'a': 3})",
            "self.assertEqual(count_words(''), {})",
            "self.assertEqual(count_words('one'), {'one': 1})",
        ],
    },
    {
        "id": "fix_dedupe_order",
        "module": "list_util",
        "func": "dedupe",
        "instruction": (
            "src/list_util.py 中的 dedupe(xs) 按约定应去除重复并保持首次出现的顺序，"
            "但当前实现做了排序，破坏了顺序。请阅读该文件并修复，使隐藏测试全部通过；"
            "可先用 run_tests 验证。"
        ),
        "bug": (
            "def dedupe(xs: list) -> list:\n"
            '    """去重（存在缺陷：排序破坏了首次出现顺序）。"""\n'
            "    return sorted(set(xs))\n"
        ),
        "fixed": (
            "def dedupe(xs: list) -> list:\n"
            '    """去重并保持首次出现顺序。"""\n'
            "    seen: set = set()\n"
            "    out: list = []\n"
            "    for x in xs:\n"
            "        if x not in seen:\n"
            "            seen.add(x)\n"
            "            out.append(x)\n"
            "    return out\n"
        ),
        "cases": [
            "self.assertEqual(dedupe([3, 1, 3, 2, 1]), [3, 1, 2])",
            "self.assertEqual(dedupe(['b', 'a', 'b']), ['b', 'a'])",
            "self.assertEqual(dedupe([]), [])",
            "self.assertEqual(dedupe([1, 1, 1]), [1])",
        ],
    },
    {
        "id": "fix_flatten_deep",
        "module": "nested_util",
        "func": "flatten",
        "instruction": (
            "src/nested_util.py 中的 flatten(xs) 按约定应把任意深度嵌套的列表展平成一级列表"
            "（非列表元素保持原样），但当前实现只展开了一层。请阅读该文件并修复，"
            "使隐藏测试全部通过；可先用 run_tests 验证。"
        ),
        "bug": (
            "def flatten(xs: list) -> list:\n"
            '    """展平嵌套列表（存在缺陷：只展开一层）。"""\n'
            "    out: list = []\n"
            "    for x in xs:\n"
            "        if isinstance(x, list):\n"
            "            out.extend(x)\n"
            "        else:\n"
            "            out.append(x)\n"
            "    return out\n"
        ),
        "fixed": (
            "def flatten(xs: list) -> list:\n"
            '    """展平任意深度嵌套的列表。"""\n'
            "    out: list = []\n"
            "    for x in xs:\n"
            "        if isinstance(x, list):\n"
            "            out.extend(flatten(x))\n"
            "        else:\n"
            "            out.append(x)\n"
            "    return out\n"
        ),
        "cases": [
            "self.assertEqual(flatten([1, [2, [3, [4]]], 5]), [1, 2, 3, 4, 5])",
            "self.assertEqual(flatten([[1, 2], [3]]), [1, 2, 3])",
            "self.assertEqual(flatten([]), [])",
            "self.assertEqual(flatten([1, 2]), [1, 2])",
        ],
    },
    {
        "id": "fix_clamp_swapped",
        "module": "math_util",
        "func": "clamp",
        "instruction": (
            "src/math_util.py 中的 clamp(value, lo, hi) 应把 value 限制在 [lo, hi] 区间内，"
            "但当前实现把 min/max 用反了。请阅读该文件并修复，使隐藏测试全部通过；"
            "可先用 run_tests 验证。"
        ),
        "bug": (
            "def clamp(value, lo, hi):\n"
            '    """限制取值范围（存在缺陷：min/max 用反）。"""\n'
            "    return min(lo, max(hi, value))\n"
        ),
        "fixed": (
            "def clamp(value, lo, hi):\n"
            '    """把 value 限制在 [lo, hi] 区间内。"""\n'
            "    return max(lo, min(hi, value))\n"
        ),
        "cases": [
            "self.assertEqual(clamp(5, 0, 3), 3)",
            "self.assertEqual(clamp(-1, 0, 3), 0)",
            "self.assertEqual(clamp(2, 0, 3), 2)",
            "self.assertEqual(clamp(3, 0, 3), 3)",
        ],
    },
    {
        "id": "fix_password_strength",
        "module": "passwd_util",
        "func": "is_strong",
        "instruction": (
            "src/passwd_util.py 中的 is_strong(pwd) 按约定要求：长度至少 8，且同时包含"
            "小写字母、大写字母和数字。当前实现漏掉了对数字的检查。请阅读该文件并修复，"
            "使隐藏测试全部通过；可先用 run_tests 验证。"
        ),
        "bug": (
            "def is_strong(pwd: str) -> bool:\n"
            '    """强口令判断（存在缺陷：漏掉数字检查）。"""\n'
            "    if len(pwd) < 8:\n"
            "        return False\n"
            "    has_lower = any(c.islower() for c in pwd)\n"
            "    has_upper = any(c.isupper() for c in pwd)\n"
            "    return has_lower and has_upper\n"
        ),
        "fixed": (
            "def is_strong(pwd: str) -> bool:\n"
            '    """强口令：长度>=8 且含小写、大写、数字。"""\n'
            "    if len(pwd) < 8:\n"
            "        return False\n"
            "    has_lower = any(c.islower() for c in pwd)\n"
            "    has_upper = any(c.isupper() for c in pwd)\n"
            "    has_digit = any(c.isdigit() for c in pwd)\n"
            "    return has_lower and has_upper and has_digit\n"
        ),
        "cases": [
            "self.assertTrue(is_strong('Abcdefg1'))",
            "self.assertFalse(is_strong('Abcdefgh'))",
            "self.assertFalse(is_strong('abcdefg1'))",
            "self.assertFalse(is_strong('ABCDEFG1'))",
            "self.assertFalse(is_strong('Ab1'))",
        ],
    },
    {
        "id": "fix_kadane_negative",
        "module": "array_util",
        "func": "max_subarray_sum",
        "instruction": (
            "src/array_util.py 中的 max_subarray_sum(arr) 应返回非空整数数组的最大子段和"
            "（允许全为负数，此时返回最大的单个元素）。当前实现对全负数组错误地返回 0。"
            "请阅读该文件并修复，使隐藏测试全部通过；可先用 run_tests 验证。"
        ),
        "bug": (
            "def max_subarray_sum(arr: list) -> int:\n"
            '    """最大子段和（存在缺陷：全负数组错误返回 0）。"""\n'
            "    best = 0\n"
            "    cur = 0\n"
            "    for x in arr:\n"
            "        cur = max(0, cur + x)\n"
            "        best = max(best, cur)\n"
            "    return best\n"
        ),
        "fixed": (
            "def max_subarray_sum(arr: list) -> int:\n"
            '    """最大子段和：数组非空，结果至少为最大单元素。"""\n'
            "    best = arr[0]\n"
            "    cur = arr[0]\n"
            "    for x in arr[1:]:\n"
            "        cur = max(x, cur + x)\n"
            "        best = max(best, cur)\n"
            "    return best\n"
        ),
        "cases": [
            "self.assertEqual(max_subarray_sum([-3, -1, -2]), -1)",
            "self.assertEqual(max_subarray_sum([1, -2, 3, 4]), 7)",
            "self.assertEqual(max_subarray_sum([-2, 1, -3, 4, -1, 2, 1, -5, 4]), 6)",
            "self.assertEqual(max_subarray_sum([5]), 5)",
        ],
    },
]


def build(spec: dict) -> None:
    d = ROOT / spec["id"]
    (d / "setup" / "src").mkdir(parents=True, exist_ok=True)
    (d / "hidden_tests").mkdir(parents=True, exist_ok=True)
    module = spec["module"]
    task = {
        "id": spec["id"],
        "instruction": spec["instruction"],
        "test_cmd": TEST_CMD,
        "scripted_actions": actions(module, spec["fixed"]),
    }
    (d / "task.json").write_text(
        json.dumps(task, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (d / "setup" / "src" / f"{module}.py").write_text(spec["bug"], encoding="utf-8")
    (d / "setup" / "src" / "__init__.py").write_text("", encoding="utf-8")
    (d / "hidden_tests" / "__init__.py").write_text("", encoding="utf-8")
    (d / "hidden_tests" / f"test_{module}.py").write_text(
        tests_source(module, spec["func"], spec["cases"]), encoding="utf-8"
    )


if __name__ == "__main__":
    for spec in SPECS:
        build(spec)
        print("built:", spec["id"])
    print(f"total: {len(SPECS)} tasks")
