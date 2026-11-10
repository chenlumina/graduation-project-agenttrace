import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))  # 测试自举：直接从源码导入

from agenttrace.sandbox import engine_available

HAS_DOCKER = engine_available()

from agenttrace.llm import ScriptedLLM
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


@unittest.skipUnless(HAS_DOCKER, "需要 Docker 引擎可用")
class TestDockerExecutor(unittest.TestCase):
    """Docker 沙箱模式端到端：每个用例真实起容器执行。"""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.tmp.name) / "traces.db")
        self.task = TaskPackage.load(TASK_DIR)

    def tearDown(self):
        self.store.close()
        self.tmp.cleanup()

    def test_scripted_agent_passes_in_sandbox(self):
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(GOOD_ACTIONS), use_docker=True)
        self.assertTrue(res.passed)
        self.assertEqual(res.status, "passed")
        events = self.store.events(res.attempt_id)
        types = {e.type for e in events}
        self.assertTrue({"llm_call", "tool_call", "file_diff", "command_exec", "run_result"} <= types)
        diffs = [e for e in events if e.type == "file_diff"]
        self.assertEqual(len(diffs), 1)
        self.assertEqual(diffs[0].data["path"], "src/utils.py")
        # Agent 的 run_tests 与最终判定各产生一次命令执行，均在容器内完成
        cmd_execs = [e for e in events if e.type == "command_exec"]
        self.assertGreaterEqual(len(cmd_execs), 2)

    def test_hidden_tests_tamper_proof(self):
        # Agent 尝试改写隐藏测试：容器内只读挂载 → 篡改无效，
        # 最终判定用原始测试（3 个用例）且通过
        actions = [
            {"tool": "write_file", "args": {"path": "hidden_tests/test_utils.py", "content": "pass\n"}},
        ] + GOOD_ACTIONS
        res = TaskRunner(self.store).run(self.task, ScriptedLLM(actions), use_docker=True)
        self.assertTrue(res.passed)
        events = self.store.events(res.attempt_id)
        run_results = [e for e in events if e.type == "run_result"]
        self.assertEqual(len(run_results), 1)
        self.assertIn("Ran 3 tests", run_results[0].data["output_tail"])

    def test_network_isolation(self):
        # --network none：容器内无法访问外网，工具报错但不影响整体流程
        # 注：fix_leap_year 的任务策略显式 deny_tools=["run_command"]，此处改用
        # 内置安全策略（policy=None），以便真正触达容器内的网络隔离行为
        task = TaskPackage.load(TASK_DIR)
        task.policy = None
        actions = [
            {"tool": "run_command", "args": {"cmd": [
                "python", "-c",
                "import urllib.request;urllib.request.urlopen('https://example.com',timeout=8)",
            ]}},
        ] + GOOD_ACTIONS
        res = TaskRunner(self.store).run(task, ScriptedLLM(actions), use_docker=True)
        self.assertTrue(res.passed)
        events = self.store.events(res.attempt_id)
        rc = [e for e in events if e.type == "tool_call" and e.data["tool"] == "run_command"]
        self.assertEqual(len(rc), 1)
        self.assertEqual(rc[0].data["status"], "error")


if __name__ == "__main__":
    unittest.main()
