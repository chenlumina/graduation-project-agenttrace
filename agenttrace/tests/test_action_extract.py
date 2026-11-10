import json
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举

from agenttrace.agent import _extract_action


class TestExtractAction(unittest.TestCase):
    def test_plain_json(self):
        a = _extract_action('{"tool": "read_file", "args": {"path": "a.py"}}')
        self.assertEqual(a["tool"], "read_file")

    def test_fenced_json(self):
        a = _extract_action('```json\n{"tool": "run_tests", "args": {}}\n```')
        self.assertEqual(a["tool"], "run_tests")

    def test_prose_with_json(self):
        a = _extract_action('好的，我先读取文件：\n{"tool": "read_file", "args": {"path": "a.py"}}')
        self.assertEqual(a["tool"], "read_file")

    def test_markup_with_args_json(self):
        # 实测 mimo-v2.6-flash 的原生标记变体：参数打包在 args 的 JSON 串里
        raw = ("＜tool_call＞<function=read_file>"
               "<parameter=args>{\"path\": \"src/search_util.py\"}</parameter>"
               "<parameter=tool>read_file</parameter></function>＜/tool_call＞")
        a = _extract_action(raw)
        self.assertEqual(a["tool"], "read_file")
        self.assertEqual(a["args"], {"path": "src/search_util.py"})

    def test_markup_with_plain_params(self):
        # 实测 mimo-v2.6-pro 的标记变体：参数直接平铺
        raw = ("<function=write_file>"
               "<parameter=path>src/a.py</parameter>"
               "<parameter=content>x = 1</parameter></function>")
        a = _extract_action(raw)
        self.assertEqual(a["tool"], "write_file")
        self.assertEqual(a["args"], {"path": "src/a.py", "content": "x = 1"})

    def test_markup_not_confused_by_inner_braces(self):
        # 标记内部含 JSON 时，不能把内层参数误当作动作（回归用例）
        raw = ('<function=read_file>'
               '<parameter=args>{"path": "src/a.py"}</parameter></function>')
        a = _extract_action(raw)
        self.assertEqual(a["tool"], "read_file")
        self.assertEqual(a["args"], {"path": "src/a.py"})

    def test_json_with_literal_newline_in_string(self):
        # M11 实验真实故障：flash 在 JSON 字符串里输出字面换行，strict 模式拒绝
        raw = '{"tool": "write_file", "args": {"path": "src/a.py", "content": "def f():\n    return 1\n"}}'
        a = _extract_action(raw)
        self.assertEqual(a["tool"], "write_file")
        self.assertEqual(a["args"]["content"], "def f():\n    return 1\n")

    def test_markup_args_with_literal_newline(self):
        raw = ('<function=write_file><parameter=args>'
               '{"path": "src/a.py", "content": "x = 1\ny = 2"}'
               '</parameter></function>')
        a = _extract_action(raw)
        self.assertEqual(a["args"]["content"], "x = 1\ny = 2")

    def test_broken_escape_repaired(self):
        # M11 实验真实故障：flash 把 docstring 转义写成 双反斜杠+未转义引号
        content = (
            'def max_subarray_sum(arr: list) -> int:\n'
            '    """最大子段和（非空整数数组；全为负数时返回最大的单个元素）。"""\n'
            '    best = arr[0]\n'
            '    cur = arr[0]\n'
            '    for x in arr[1:]:\n'
            '        cur = max(x, cur + x)\n'
            '        best = max(best, cur)\n'
            '    return best\n'
        )
        valid = json.dumps(
            {"tool": "write_file", "args": {"path": "src/array_util.py", "content": content}},
            ensure_ascii=False,
        )
        bs = chr(92)
        vq3 = (bs + '"') * 3        # valid 中的交替转义（源码层面的 \"\"\"）
        mq3 = (bs * 2) + '"""'      # 模型形态：双反斜杠 + 连续三引号
        broken = valid.replace(vq3, mq3)
        with self.assertRaises(ValueError):
            json.loads(broken)
        a = _extract_action(broken)
        # json-repair 兜底：动作可解析派发；content 保真是尽力而为（残留转义符
        # 会被 M7 代码审计的语法检查接住）——不要求逐字还原
        self.assertEqual(a["tool"], "write_file")
        self.assertIn("max_subarray_sum", (a["args"] or {}).get("content", ""))

    def test_no_action_raises(self):
        with self.assertRaises(ValueError):
            _extract_action("今天天气不错。")


if __name__ == "__main__":
    unittest.main()
