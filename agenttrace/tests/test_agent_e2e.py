import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.collector import TraceCollector
from agenttrace.llm import LLMResponse, ScriptedLLM
from agenttrace.model import AttemptMeta
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

        # R-09：工具与命令事件必须带真实耗时字段
        for e in events:
            if e.type == "command_exec":
                self.assertIsNotNone(e.data.get("duration_ms"))
            if e.type == "tool_call" and e.data["tool"] != "finish":
                self.assertIsNotNone(e.data.get("duration_ms"))

        # ScriptedLLM 每轮固定 120+40 token，验证统计口径
        usage = [e.data["usage"] for e in events if e.type == "llm_call"]
        self.assertEqual(sum(u["prompt_tokens"] for u in usage), 4 * 120)
        self.assertEqual(sum(u["completion_tokens"] for u in usage), 4 * 40)

        doc = self.store.export(res.attempt_id)
        self.assertEqual(len(doc["events"]), len(events))

        # R-11：attempt.meta 必须记录运行环境版本，保证实验可归因
        m = self.store.get_attempt(res.attempt_id)
        self.assertIn("code_version", m.meta)
        self.assertTrue(m.meta["code_version"])  # git 短 hash 或 "unknown"，不为空
        self.assertIn("python", m.meta)

        # R-08：llm_call 增量记录——首轮 full 快照，其后 incremental；重放可精确重建
        from agenttrace.model import rebuild_messages
        llm_events = [e for e in events if e.type == "llm_call"]
        self.assertEqual(llm_events[0].data["messages_mode"], "full")
        self.assertEqual(len(llm_events[0].data["messages"]), 2)  # system + instruction
        for e in llm_events[1:]:
            self.assertEqual(e.data["messages_mode"], "incremental")
            self.assertEqual(len(e.data["messages"]), 2)  # 上轮 assistant + tool 结果
        rebuilt = rebuild_messages(events)
        self.assertEqual(
            len(rebuilt), 2 + 2 * (len(llm_events) - 1)
        )  # 首轮 2 条 + 每轮新增 2 条
        self.assertEqual(rebuilt[0]["role"], "system")

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


class ParseFailOnceLLM:
    """第一次返回无法解析的自然语言回复，之后透传给脚本 LLM（R-15 场景）。"""

    def __init__(self, inner):
        self.inner = inner
        self.model = inner.model
        self.calls = 0

    def chat(self, messages):
        self.calls += 1
        if self.calls == 1:
            return LLMResponse("我认为应该修改闰年判断的逻辑……", self.model, 5, 5)
        return self.inner.chat(messages)


class AlwaysBadFormatLLM:
    """始终返回无法解析的回复（解析重试无法消除）。"""

    model = "bad-format-model"

    def chat(self, messages):
        return LLMResponse("这不是JSON，只是一段解释文字。", self.model, 5, 5)


class TestParseErrorRetry(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.task = TaskPackage.load(TASK_DIR)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_parse_error_retried_then_passes(self):
        """R-15：解析失败重试并反馈修正提示后，任务最终仍应通过。"""
        res = TaskRunner(self.store).run(
            self.task, ParseFailOnceLLM(ScriptedLLM(GOOD_ACTIONS))
        )
        self.assertTrue(res.passed)
        self.assertEqual(res.status, "passed")
        events = self.store.events(res.attempt_id)
        parse_errors = [
            e for e in events
            if e.type == "error" and e.data.get("error_code") == "parse_error"
        ]
        self.assertEqual(len(parse_errors), 1)
        # 修正提示必须反馈给模型（对话历史中出现解析失败提示）
        llm_events = [e for e in events if e.type == "llm_call"]
        self.assertGreaterEqual(len(llm_events), 2)

    def test_persistent_parse_error_marks_error(self):
        """R-15：持续解析失败在 MAX_PARSE_RETRIES 次后判为 error，不再首轮即死。"""
        res = TaskRunner(self.store).run(self.task, AlwaysBadFormatLLM(), max_turns=8)
        self.assertEqual(res.status, "error")
        self.assertFalse(res.passed)
        events = self.store.events(res.attempt_id)
        parse_errors = [
            e for e in events
            if e.type == "error" and e.data.get("error_code") == "parse_error"
        ]
        self.assertEqual(len(parse_errors), 3)  # 3 次重试后才放弃


class TestReadPagination(unittest.TestCase):
    """R-18：read_file 支持 offset/limit 分页，截断信息与上下文长度完整留痕。"""

    def setUp(self):
        # Windows 下 sqlite 句柄释放偶有延迟，忽略清理错误避免误报
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.root = Path(self.tmp.name)
        self.store = TraceStore(self.root / "t.db")
        self.store.begin_attempt(
            AttemptMeta(attempt_id="a", task_id="t", agent="mini-agent", model="m")
        )
        self.collector = TraceCollector(self.store, "a")

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_pagination_and_metadata(self):
        from agenttrace.agent import MiniAgent
        from agenttrace.tools import Workspace

        (self.root / "big.txt").write_text("x" * 5000, encoding="utf-8")
        llm = ScriptedLLM([
            {"tool": "read_file", "args": {"path": "big.txt"}},
            {"tool": "read_file", "args": {"path": "big.txt", "offset": 2000, "limit": 2000}},
            {"tool": "read_file", "args": {"path": "big.txt", "offset": 4000}},
            {"tool": "finish", "args": {}},
        ])
        MiniAgent(llm).run("read", ["python", "-c", "pass"], Workspace(self.root), self.collector)
        calls = [e for e in self.store.events("a") if e.type == "tool_call"]
        # 第 1 次：默认 limit=2000 → 截断
        self.assertTrue(calls[0].data["truncated"])
        self.assertEqual(calls[0].data["original_length"], 5000)
        self.assertEqual(calls[0].data["limit"], 2000)
        self.assertEqual(len(calls[0].data["result"]), 2000)
        # 第 2 次：offset=2000 → 仍截断（还剩 1000）
        self.assertTrue(calls[1].data["truncated"])
        self.assertEqual(calls[1].data["offset"], 2000)
        # 第 3 次：offset=4000 → 剩余 1000，读完不截断
        self.assertFalse(calls[2].data["truncated"])
        self.assertEqual(len(calls[2].data["result"]), 1000)
        # R-18：每轮上下文长度均已记录且单调
        series = [
            e.data["context_chars"] for e in self.store.events("a")
            if e.type == "llm_call"
        ]
        self.assertEqual(len(series), 4)
        self.assertTrue(all(isinstance(x, int) and x > 0 for x in series))
        self.assertEqual(series, sorted(series))


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
