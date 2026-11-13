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

    def test_time_breakdown_and_slowest_steps(self):
        """R-09：耗时占比与最慢步骤 TopN 的统计口径。"""
        def script(c):
            e1 = c.llm_call(model="m", messages=[], response="r",
                            usage={"prompt_tokens": 10, "completion_tokens": 5}, duration_ms=1000)
            c.tool_call("run_tests", {}, "x", "ok", parent_id=e1.id, duration_ms=3000)
            c.command_exec(["pytest"], 0, "o", "", parent_id=e1.id, duration_ms=2900)
            c.run_result(True, "OK")

        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertEqual(m["llm_time_ms"], 1000)
        self.assertEqual(m["tool_time_ms"], 3000)
        self.assertEqual(m["command_time_ms"], 2900)
        tb = m["time_breakdown"]
        self.assertEqual(tb["llm_share"], 0.25)   # 1000 / (1000+3000)
        self.assertEqual(tb["tool_share"], 0.75)
        self.assertEqual(tb["command_ms"], 2900)  # 子集信息，不计入占比分母
        self.assertEqual(m["slowest_steps"][0]["type"], "tool_call")
        self.assertEqual(m["slowest_steps"][0]["duration_ms"], 3000)
        self.assertEqual(m["slowest_steps"][0]["name"], "run_tests")
        self.assertEqual(len(m["slowest_steps"]), 3)

    def test_llm_params_recorded(self):
        """R-11：llm_call 事件必须携带采样参数快照（不含密钥）。"""
        def script(c):
            c.llm_call(
                model="m", messages=[], response="r",
                usage={"prompt_tokens": 1, "completion_tokens": 1},
                params={"temperature": 0, "top_p": None, "seed": 42, "interface": "openai-compat"},
                finish_reason="stop",
            )
            c.run_result(True, "OK")
        evs = self.store.events(self.collect(script))
        e = evs[0]
        self.assertEqual(e.data["params"]["seed"], 42)
        self.assertEqual(e.data["params"]["temperature"], 0)
        self.assertNotIn("api_key", e.data["params"])
        self.assertEqual(e.data["finish_reason"], "stop")

    def test_classify_structured_codes(self):
        """R-14：结构化 error_code 归因——终止性错误优先于过程性异常，取最早出现者。"""
        def script(c):
            e1 = c.llm_call(model="m", messages=[], response="r",
                            usage={"prompt_tokens": 1, "completion_tokens": 1})
            # 先发生过程性异常（越界被拒，不终止）
            c.tool_call("read_file", {"path": "../x"}, "路径越界", "error",
                        parent_id=e1.id, extra={"error_code": "workspace_escape"})
            # 后发生终止性错误
            c.error("达到最大轮数仍未完成", code="max_turns")
        evs = self.store.events(self.collect(script))
        cl = classify(evs)
        self.assertEqual(cl["primary"], "max_turns")  # 终止性优先
        self.assertIn("workspace_escape", cl["labels"])  # 过程标签保留
        self.assertFalse(cl["passed"])

    def test_classify_verdict_failed(self):
        """R-14：无终止性错误、判定未通过 → tests_failed。"""
        def script(c):
            e1 = c.llm_call(model="m", messages=[], response="r",
                            usage={"prompt_tokens": 1, "completion_tokens": 1})
            c.tool_call("finish", {}, "done", "ok", parent_id=e1.id)
            c.error("最终判定未通过: 断言失败", code="verdict_failed")
            c.run_result(False, "FAIL")
        evs = self.store.events(self.collect(script))
        cl = classify(evs)
        self.assertEqual(cl["primary"], "tests_failed")

    def test_classify_legacy_text_fallback(self):
        """R-14：旧轨迹（无 error_code）回退文本匹配，口径与 v1 兼容。"""
        def script(c):
            c.error("无法解析 Agent 动作: bad output")
        evs = self.store.events(self.collect(script))
        cl = classify(evs)
        self.assertEqual(cl["primary"], "parse_error")
        self.assertEqual(cl["labels"], ["parse_error"])

    def test_classify_pass_keeps_process_labels(self):
        """R-14：通过但带过程异常 → primary=passed，标签保留。"""
        def script(c):
            e1 = c.llm_call(model="m", messages=[], response="r",
                            usage={"prompt_tokens": 1, "completion_tokens": 1})
            c.tool_call("read_file", {"path": "../x"}, "路径越界", "error",
                        parent_id=e1.id, extra={"error_code": "workspace_escape"})
            c.run_result(True, "OK")
        evs = self.store.events(self.collect(script))
        cl = classify(evs)
        self.assertEqual(cl["primary"], "passed")
        self.assertIn("workspace_escape", cl["labels"])

    def test_ngram_loop_detection(self):
        """R-16：ABAB 交替循环可被识别（旧口径漏检），AAA 同键连发仍命中。"""
        def script_abab(c):
            for _ in range(3):
                c.tool_call("read_file", {"path": "a"}, "x", "ok")
                c.tool_call("read_file", {"path": "b"}, "y", "ok")
        evs = self.store.events(self.collect(script_abab))
        m = compute_metrics(evs)
        self.assertTrue(m["loop_detected"])
        self.assertEqual(m["loop_patterns"][0]["period"], 2)
        self.assertEqual(m["loop_patterns"][0]["repeats"], 3)

        def script_aaa(c):
            for _ in range(3):
                c.tool_call("read_file", {"path": "a"}, "x", "ok")
        evs2 = self.store.events(self.collect(script_aaa))
        m2 = compute_metrics(evs2)
        self.assertTrue(m2["loop_detected"])
        self.assertEqual(m2["loop_patterns"][0]["period"], 1)

        def script_ab(c):
            c.tool_call("read_file", {"path": "a"}, "x", "ok")
            c.tool_call("read_file", {"path": "b"}, "y", "ok")
        evs3 = self.store.events(self.collect(script_ab))
        self.assertFalse(compute_metrics(evs3)["loop_detected"])

    def test_no_progress_detection(self):
        """R-16：连续 ≥4 次工具调用无新 file_diff 且未通过 → 无进展告警。"""
        def script(c):
            for _ in range(5):
                c.tool_call("read_file", {"path": "a"}, "x", "ok")
            c.run_result(False, "FAIL")
        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertEqual(m["max_no_progress_streak"], 5)
        self.assertTrue(m["no_progress_detected"])

        def script_pass(c):
            for _ in range(5):
                c.tool_call("read_file", {"path": "a"}, "x", "ok")
            c.run_result(True, "OK")
        evs2 = self.store.events(self.collect(script_pass))
        self.assertFalse(compute_metrics(evs2)["no_progress_detected"])  # 通过不算无进展

    def test_reread_after_write_not_counted(self):
        """R-16：写后重读同一文件属合理行为，从无效重复率中剔除。"""
        def script(c):
            c.tool_call("write_file", {"path": "a.py", "content": "x"}, "d", "ok")
            c.file_diff("a.py", "+x")
            c.tool_call("read_file", {"path": "a.py"}, "x", "ok")  # 写后重读（合理）
            c.tool_call("read_file", {"path": "a.py"}, "x", "ok")  # 再次重读（重复）
            c.run_result(True, "OK")
        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertEqual(m["reread_after_write_count"], 2)
        # 重复键 2 个（两次重读），全部属于写后重读 → 有效重复率 0
        self.assertEqual(m["effective_duplicate_tool_rate"], 0.0)

    def test_cost_calculation(self):
        """R-17：成本金额折算；无价目模型不猜测、如实标注未计价。"""
        prices = {"m": {"input": 2.0, "output": 8.0}}  # 每百万 token 2/8 元

        def script(c):
            c.llm_call(model="m", messages=[], response="r",
                       usage={"prompt_tokens": 1_000_000, "completion_tokens": 500_000})
            c.llm_call(model="unknown-model", messages=[], response="r",
                       usage={"prompt_tokens": 100, "completion_tokens": 50})
        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs, prices=prices)
        # 1M×2 + 0.5M×8 = 2 + 4 = 6 元
        self.assertAlmostEqual(m["cost_cny"], 6.0, places=4)
        self.assertFalse(m["cost_fully_priced"])
        self.assertEqual(m["unpriced_tokens"], 150)

        from agenttrace.pricing import compute_cost
        self.assertIsNone(compute_cost("nope", 100, 100, prices))
        self.assertAlmostEqual(compute_cost("m", 0, 1_000_000, prices), 8.0, places=4)

    def test_context_and_truncation_metrics(self):
        """R-18：上下文增长与工具截断率指标。"""
        def script(c):
            c.llm_call(model="m", messages=[], response="r",
                       usage={"prompt_tokens": 1, "completion_tokens": 1}, context_chars=500)
            c.tool_call("read_file", {"path": "a"}, "x" * 10, "ok",
                        extra={"truncated": True, "original_length": 9999})
            c.llm_call(model="m", messages=[], response="r",
                       usage={"prompt_tokens": 1, "completion_tokens": 1}, context_chars=1800)
            c.tool_call("read_file", {"path": "b"}, "short", "ok",
                        extra={"truncated": False, "original_length": 5})
            c.run_result(True, "OK")
        evs = self.store.events(self.collect(script))
        m = compute_metrics(evs)
        self.assertEqual(m["context_chars_per_turn"], [500, 1800])
        self.assertEqual(m["max_context_chars"], 1800)
        self.assertEqual(m["context_growth_chars"], 1300)
        self.assertEqual(m["truncated_tool_calls"], 1)
        self.assertEqual(m["truncated_tool_rate"], 0.5)

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
