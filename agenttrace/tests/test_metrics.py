import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.collector import TraceCollector
from agenttrace.llm import ScriptedLLM
from agenttrace.metrics import classify, compute_metrics
from agenttrace.model import AttemptMeta, new_id
from agenttrace.runner import TaskPackage, TaskRunner
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "tasks" / "fix_leap_year"


class TestMetrics(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.task = TaskPackage.load(TASK_DIR)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def collect(self, script) -> str:
        attempt_id = new_id()
        self.store.begin_attempt(
            AttemptMeta(attempt_id=attempt_id, task_id="t", agent="a", model="m")
        )
        collector = TraceCollector(self.store, attempt_id)
        script(collector)
        return attempt_id

    def test_clean_pass(self):
        def script(c):
            e1 = c.llm_call(model="m", messages=[], response="r",
                            usage={"prompt_tokens": 10, "completion_tokens": 5}, duration_ms=100)
            c.tool_call("read_file", {"path": "a"}, "x", "ok", parent_id=e1.id)
            e2 = c.llm_call(model="m", messages=[], response="r",
                            usage={"prompt_tokens": 10, "completion_tokens": 5}, duration_ms=50)
            c.tool_call("finish", {}, "done", "ok", parent_id=e2.id)
            c.run_result(True, "OK")

        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertEqual(m["tool_call_count"], 2)
        self.assertEqual(m["invalid_tool_rate"], 0.0)
        self.assertEqual(m["duplicate_tool_rate"], 0.0)
        self.assertFalse(m["loop_detected"])
        self.assertEqual(m["max_consecutive_errors"], 0)
        self.assertTrue(m["finish_reached"])
        self.assertTrue(m["passed"])
        self.assertEqual(m["total_tokens"], 30)
        self.assertEqual(m["llm_time_ms"], 150)

        cl = classify(evs)
        self.assertTrue(cl["passed"])
        self.assertEqual(cl["primary"], "passed")
        self.assertEqual(cl["labels"], [])

    def test_error_retry_and_duplicates(self):
        def script(c):
            e = c.llm_call(model="m", messages=[], response="r",
                           usage={"prompt_tokens": 1, "completion_tokens": 1})
            c.tool_call("write_file", {"path": "f", "content": "x"}, "boom", "error", parent_id=e.id)
            c.tool_call("write_file", {"path": "f", "content": "x"}, "boom2", "error", parent_id=e.id)
            c.tool_call("write_file", {"path": "f", "content": "x"}, {"diff": ""}, "ok", parent_id=e.id)
            c.run_result(False, "FAIL")

        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertEqual(m["invalid_tool_calls"], 2)
        self.assertEqual(m["max_consecutive_errors"], 2)
        self.assertEqual(m["duplicate_tool_calls"], 2)
        self.assertFalse(m["finish_reached"])

        cl = classify(evs)
        self.assertFalse(cl["passed"])
        self.assertEqual(cl["primary"], "incomplete")

    def test_tests_failed_primary(self):
        def script(c):
            e = c.llm_call(model="m", messages=[], response="r",
                           usage={"prompt_tokens": 1, "completion_tokens": 1})
            c.tool_call("finish", {}, "done", "ok", parent_id=e.id)
            c.run_result(False, "FAIL")

        evs = self.store.events(self.collect(script))
        cl = classify(evs)
        self.assertFalse(cl["passed"])
        self.assertEqual(cl["primary"], "tests_failed")

    def test_loop_detected(self):
        def script(c):
            e = c.llm_call(model="m", messages=[], response="r",
                           usage={"prompt_tokens": 1, "completion_tokens": 1})
            for _ in range(3):
                c.tool_call("read_file", {"path": "a"}, "x", "ok", parent_id=e.id)
            c.run_result(False, "FAIL")

        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertTrue(m["loop_detected"])
        self.assertEqual(m["duplicate_tool_calls"], 2)

    def test_zero_tool_calls_no_crash(self):
        def script(c):
            c.llm_call(model="m", messages=[], response="r",
                       usage={"prompt_tokens": 1, "completion_tokens": 1})
            c.run_result(False, "x")

        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertEqual(m["invalid_tool_rate"], 0.0)
        self.assertEqual(m["duplicate_tool_rate"], 0.0)
        self.assertFalse(m["loop_detected"])
        self.assertEqual(classify(evs)["primary"], "incomplete")

    def test_e2e_parse_error_classification(self):
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(["这不是JSON"]))
        evs = self.store.events(res.attempt_id)
        cl = classify(evs)
        self.assertIn("parse_error", cl["labels"])
        self.assertEqual(cl["primary"], "parse_error")

    def test_e2e_max_turns_classification(self):
        actions = [{"tool": "read_file", "args": {"path": "src/utils.py"}}] * 8
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(actions), max_turns=8)
        self.assertEqual(res.status, "error")
        evs = self.store.events(res.attempt_id)
        cl = classify(evs)
        self.assertIn("max_turns", cl["labels"])
        self.assertEqual(cl["primary"], "max_turns")
        m = compute_metrics(evs)
        self.assertTrue(m["loop_detected"])

    def test_e2e_escape_recorded_but_passed(self):
        actions = [{"tool": "read_file", "args": {"path": "../../etc/hostname"}}] + [
            {"tool": "read_file", "args": {"path": "src/utils.py"}},
            {"tool": "write_file", "args": {"path": "src/utils.py",
             "content": "def is_leap_year(year: int) -> bool:\n    return year % 4 == 0 and (year % 100 != 0 or year % 400 == 0)\n"}},
            {"tool": "run_tests", "args": {}},
            {"tool": "finish", "args": {"summary": "fixed"}},
        ]
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(actions))
        self.assertTrue(res.passed)
        evs = self.store.events(res.attempt_id)
        cl = classify(evs)
        self.assertTrue(cl["passed"])
        self.assertEqual(cl["primary"], "passed")
        self.assertIn("workspace_escape", cl["labels"])


if __name__ == "__main__":
    unittest.main()
