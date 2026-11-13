import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.agent import MiniAgent
from agenttrace.collector import TraceCollector
from agenttrace.faults import PRESET_PROFILES, FaultProfile, resolve_fault
from agenttrace.llm import ScriptedLLM
from agenttrace.metrics import compute_metrics
from agenttrace.model import AttemptMeta, new_id
from agenttrace.runner import TaskPackage, TaskRunner
from agenttrace.store import TraceStore

ROOT = Path(__file__).resolve().parents[1]
TASK_DIR = ROOT / "tasks" / "fix_leap_year"


class TestFaultInjection(unittest.TestCase):
    """R-05：故障注入实验——扰动条件下平台过程指标必须产生可观测信号。

    这组断言本身就是论文"故障注入实验"章节的证据链：
    功能存在性（指标能算出来）→ 功能有效性（异常发生时指标确实响应）。
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(ignore_cleanup_errors=True)
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.task = TaskPackage.load(TASK_DIR)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def _run(self, faults=None, script=None):
        llm = ScriptedLLM(script or self.task.scripted_actions)
        return TaskRunner(self.store).run(
            self.task, llm, max_turns=10, faults=faults
        )

    def test_resolve_fault_profiles(self):
        self.assertIsNone(resolve_fault(None))
        self.assertIsNone(resolve_fault("nope"))
        self.assertEqual(resolve_fault("slow_tool").slow_tool_ms, 1500)
        self.assertIsInstance(PRESET_PROFILES["flaky_tool"], FaultProfile)

    def test_baseline_no_signal(self):
        """基线：无扰动时工具全部成功、无注入留痕（对照组）。"""
        res = self._run()
        self.assertTrue(res.passed)
        m = compute_metrics(self.store.events(res.attempt_id))
        self.assertEqual(m["invalid_tool_calls"], 0)
        self.assertEqual(m["error_event_count"], 0)

    def test_slow_tool_visible_in_time_metrics(self):
        """扰动：慢工具 → tool_time_ms 显著抬升（耗时归因可见）。"""
        res = self._run(faults=FaultProfile("slow", slow_tool_ms=400))
        m = compute_metrics(self.store.events(res.attempt_id))
        # 参照解约 3 次本地工具执行（read/write/run_tests），每次 ≥400ms
        self.assertGreaterEqual(m["tool_time_ms"], 3 * 400)
        # 耗时占比：工具侧占大头（ScriptedLLM 推理耗时为 0）
        self.assertGreater(m["time_breakdown"]["tool_share"], 0.9)
        self.assertEqual(m["slowest_steps"][0]["type"], "tool_call")

    def test_flaky_tool_visible_in_invalid_rate(self):
        """扰动：必失败工具 → invalid_tool_rate、结构化 tool_error 留痕。"""
        res = self._run(faults=FaultProfile("always-fail", flaky_rate=1.0))
        m = compute_metrics(self.store.events(res.attempt_id))
        # 参照解 4 动作：read/write/run_tests 走本地分发被注入打挂，finish 在分发前
        # 本地处理不受注入 → 3/4 = 0.75
        self.assertEqual(m["invalid_tool_rate"], 0.75)
        # 错误码留痕：结构化归因可判读
        tool_errors = [
            e for e in self.store.events(res.attempt_id)
            if e.type == "tool_call" and e.data.get("error_code") == "tool_error"
        ]
        self.assertGreaterEqual(len(tool_errors), 1)
        # 任务不应通过（工具全被扰动打断）
        self.assertFalse(res.passed)

    def test_prompt_injection_visible_in_flags(self):
        """扰动：注入样本 → injection_flags 留痕（H-3 检测链路响应）。"""
        res = self._run(faults=FaultProfile("inj", inject_instruction=True))
        events = self.store.events(res.attempt_id)
        flagged = [
            e for e in events
            if e.type == "tool_call" and e.data.get("injection_flags")
        ]
        self.assertGreaterEqual(len(flagged), 1)
        flags = flagged[0].data["injection_flags"]
        self.assertIn("ignore_previous_instructions", flags)
        self.assertIn("credential_access", flags)
        # 对照：判定不受注入样本影响（参照解照常通过——留痕不阻断的定位，R-21）
        self.assertTrue(res.passed)

    def test_meta_records_fault_profile(self):
        """留痕：扰动配置完整记入 attempt.meta（实验可归因、可复现）。"""
        profile = FaultProfile("slow", slow_tool_ms=100)
        res = self._run(faults=profile)
        meta = self.store.get_attempt(res.attempt_id)
        self.assertEqual(meta.meta["fault"]["name"], "slow")
        self.assertEqual(meta.meta["fault"]["slow_tool_ms"], 100)


if __name__ == "__main__":
    unittest.main()
