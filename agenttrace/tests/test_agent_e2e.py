import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.llm import LLMResponse, ScriptedLLM
from agenttrace.runner import TaskPackage, TaskRunner
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "tasks" / "fix_leap_year"

FIXED_UTILS = (
    "def is_leap_year(year: int) -> bool:\n"
    '    """闰年：四年一闰，百年不闰，四百年再闰。"""\n'
    "    if year % 400 == 0:\n"
    "        return True\n"
    "    if year % 100 == 0:\n"
    "        return False\n"
    "    return year % 4 == 0\n"
)

GOOD_ACTIONS = [
    {"tool": "read_file", "args": {"path": "src/utils.py"}},
    {"tool": "write_file", "args": {"path": "src/utils.py", "content": FIXED_UTILS}},
    {"tool": "run_tests", "args": {}},
    {"tool": "finish", "args": {"summary": "fixed"}},
]


class TestEndToEnd(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.task = TaskPackage.load(TASK_DIR)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_scripted_agent_passes_task(self):
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(GOOD_ACTIONS))
        self.assertTrue(res.passed)
        self.assertEqual(res.status, "passed")

        events = self.store.events(res.attempt_id)
        types = {e.type for e in events}
        self.assertTrue({"llm_call", "tool_call", "file_diff", "command_exec", "run_result"} <= types)

        seqs = [e.seq for e in events]
        self.assertEqual(seqs, sorted(seqs))
        self.assertEqual(len(seqs), len(set(seqs)))

        llm_ids = {e.id for e in events if e.type == "llm_call"}
        for e in events:
            if e.type == "tool_call":
                self.assertIn(e.parent_id, llm_ids)

        run_results = [e for e in events if e.type == "run_result"]
        self.assertEqual(len(run_results), 1)
        self.assertTrue(run_results[0].data["passed"])

        diffs = [e for e in events if e.type == "file_diff"]
        self.assertEqual(len(diffs), 1)
        self.assertIn("+", diffs[0].data["diff"])

        # ScriptedLLM 每轮固定 120+40 token，验证统计口径
        usage = [e.data["usage"] for e in events if e.type == "llm_call"]
        self.assertEqual(sum(u["prompt_tokens"] for u in usage), 4 * 120)
        self.assertEqual(sum(u["completion_tokens"] for u in usage), 4 * 40)

        doc = self.store.export(res.attempt_id)
        self.assertEqual(len(doc["events"]), len(events))

    def test_file_diff_nested_under_tool_call(self):
        """R-10：file_diff 必须挂在产生它的 write_file tool_call 之下（三层结构）。"""
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(GOOD_ACTIONS))
        self.assertTrue(res.passed)
        events = self.store.events(res.attempt_id)
        by_id = {e.id: e for e in events}
        diffs = [e for e in events if e.type == "file_diff"]
        self.assertEqual(len(diffs), 1)
        parent = by_id.get(diffs[0].parent_id)
        self.assertIsNotNone(parent, "file_diff 的父事件必须是 tool_call，而非 llm_call")
        self.assertEqual(parent.type, "tool_call")
        self.assertEqual(parent.data["tool"], "write_file")
        grand = by_id.get(parent.parent_id)
        self.assertIsNotNone(grand)
        self.assertEqual(grand.type, "llm_call")

    def test_broken_agent_marked_error(self):
        # 动作不是合法 JSON：Agent 异常 → error 状态，判定仍由隐藏测试给出
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(["这不是JSON"]))
        self.assertFalse(res.passed)
        self.assertEqual(res.status, "error")
        events = self.store.events(res.attempt_id)
        self.assertTrue(any(e.type == "error" for e in events))
        self.assertTrue(any(e.type == "run_result" and e.data["passed"] is False for e in events))

    def test_agent_cannot_escape_workspace(self):
        actions = [{"tool": "read_file", "args": {"path": "../../etc/hostname"}}] + GOOD_ACTIONS
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(actions))
        events = self.store.events(res.attempt_id)
        read_calls = [e for e in events if e.type == "tool_call" and e.data["tool"] == "read_file"]
        self.assertEqual(read_calls[0].data["status"], "error")
        self.assertIn("路径越界", str(read_calls[0].data["result"]))
        # 越界被拒后脚本继续执行，最终仍应通过
        self.assertTrue(res.passed)


class EmptyOnceLLM:
    """第一次 chat 返回空回复，之后透传给脚本 LLM（模拟瞬时故障）。"""

    def __init__(self, inner):
        self.inner = inner
        self.model = inner.model
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse("", self.model, 5, 0)
        return self.inner.chat(messages)


class AlwaysEmptyLLM:
    """始终返回空回复（瞬时故障未被重试消除）。"""

    model = "empty-model"

    def chat(self, messages):
        return LLMResponse("", self.model, 3, 0)


class TestEmptyResponseRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.task = TaskPackage.load(TASK_DIR)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_empty_response_retried_then_passes(self):
        res = TaskRunner(self.store).run(self.task, EmptyOnceLLM(ScriptedLLM(GOOD_ACTIONS)))
        self.assertTrue(res.passed)
        self.assertEqual(res.status, "passed")

    def test_persistent_empty_marks_error(self):
        res = TaskRunner(self.store).run(
            self.task, AlwaysEmptyLLM(), max_turns=3
        )
        self.assertEqual(res.status, "error")
        self.assertFalse(res.passed)
        events = self.store.events(res.attempt_id)
        errors = [e.data["message"] for e in events if e.type == "error"]
        self.assertTrue(any("空回复" in m for m in errors))


if __name__ == "__main__":
    unittest.main()
